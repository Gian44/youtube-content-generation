"""Caption generation service - word-by-word highlighted captions for Shorts, sentence captions for long-form."""

import os
import uuid
from pathlib import Path

from storyfactory.config import get_settings
from storyfactory.db.engine import get_session
from storyfactory.db.models import CaptionJob, CaptionWord, Story, TTSJob
from storyfactory.logger import get_logger

log = get_logger("caption_service")

# Safe margins for YouTube Shorts UI elements (in pixels at 1080x1920)
SHORTS_SAFE_MARGINS = {
    "top": 200,      # Channel name, follow button
    "bottom": 300,   # Like, comment, share buttons
    "left": 50,
    "right": 50,
}

DEFAULT_CAPTION_CONFIG = {
    "font_size": 48,
    "stroke_width": 3,
    "shadow_depth": 2,
    "max_words_per_line": 3,
    "font_name": "Arial Bold",
    "primary_color": "&H00FFFFFF",   # White
    "highlight_color": "&H0000FFFF", # Yellow
    "outline_color": "&H00000000",   # Black
    "shadow_color": "&H80000000",    # Semi-transparent black
}


def generate_captions(
    story: Story,
    tts_job: TTSJob,
    style: str = "word_highlight",
    output_dir: str = "./data/captions",
) -> CaptionJob:
    """Generate captions for a story using the specified style.

    Args:
        story: The story to caption
        tts_job: The TTS job with audio file
        style: 'word_highlight' for Shorts, 'sentence' for long-form
        output_dir: Directory to save caption files

    Returns:
        CaptionJob with generated caption file
    """
    settings = get_settings()
    session = get_session()

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    output_path = os.path.join(output_dir, f"captions_{story.id[:8]}_{style}.ass")

    caption_job = CaptionJob(
        id=str(uuid.uuid4()),
        story_id=story.id,
        tts_job_id=tts_job.id,
        style=style,
        status="processing",
        output_path=output_path,
        font_size=DEFAULT_CAPTION_CONFIG["font_size"],
        stroke_width=DEFAULT_CAPTION_CONFIG["stroke_width"],
        shadow_depth=DEFAULT_CAPTION_CONFIG["shadow_depth"],
        max_words_per_line=DEFAULT_CAPTION_CONFIG["max_words_per_line"],
    )

    try:
        # Get word timings
        words_with_timing = _get_word_timings(story, tts_job)

        if not words_with_timing:
            # Fallback: approximate timing based on word count and duration
            words_with_timing = _approximate_word_timings(
                story.body, tts_job.duration_seconds or _estimate_duration(story.body)
            )
            caption_job.alignment_method = "approximate"
        else:
            caption_job.alignment_method = "transcription"

        # Save word timings to database
        for i, word_data in enumerate(words_with_timing):
            word = CaptionWord(
                id=str(uuid.uuid4()),
                caption_job_id=caption_job.id,
                word=word_data["word"],
                start_time=word_data["start"],
                end_time=word_data["end"],
                confidence=word_data.get("confidence"),
                order_index=i,
            )
            session.add(word)

        # Generate ASS subtitle file
        if style == "word_highlight":
            _generate_word_highlight_ass(words_with_timing, output_path)
        else:
            _generate_sentence_ass(words_with_timing, output_path)

        caption_job.status = "completed"
        caption_job.word_count = len(words_with_timing)

        log.info(
            "captions_generated",
            story_id=story.id,
            style=style,
            word_count=len(words_with_timing),
            method=caption_job.alignment_method,
        )

    except Exception as e:
        caption_job.status = "failed"
        caption_job.error = str(e)
        log.error("caption_generation_failed", story_id=story.id, error=str(e))

    session.add(caption_job)
    session.commit()
    session.close()
    return caption_job


def _get_word_timings(story: Story, tts_job: TTSJob) -> list[dict]:
    """Get word-level timings using OpenAI transcription or local alignment."""
    settings = get_settings()

    if settings.dry_run:
        return _approximate_word_timings(
            story.body, tts_job.duration_seconds or _estimate_duration(story.body)
        )

    # Try OpenAI Whisper transcription for word-level timing
    if tts_job.output_path and os.path.exists(tts_job.output_path):
        try:
            return _openai_transcription(tts_job.output_path)
        except Exception as e:
            log.warning("whisper_transcription_failed", error=str(e))

    # Fallback to approximate timing
    return []


def _openai_transcription(audio_path: str) -> list[dict]:
    """Use OpenAI Whisper to get word-level timestamps."""
    from storyfactory.channel_context import resolve_api_key

    api_key = resolve_api_key("text.openai", "openai_api_key")
    if not api_key or api_key.startswith("sk-your"):
        return []

    import openai

    client = openai.OpenAI(api_key=api_key)

    with open(audio_path, "rb") as audio_file:
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )

    words = []
    if hasattr(response, "words") and response.words:
        for w in response.words:
            words.append({
                "word": w.word,
                "start": w.start,
                "end": w.end,
                "confidence": getattr(w, "confidence", None),
            })

    from storyfactory.services.api_tracker import track_api_call
    track_api_call(
        provider="openai",
        endpoint="audio.transcriptions/whisper-1",
        cost_estimate=0.006,  # $0.006/min
    )

    return words


def _approximate_word_timings(text: str, total_duration: float) -> list[dict]:
    """Generate approximate word timings based on even distribution."""
    words = text.split()
    if not words:
        return []

    # Average duration per word
    duration_per_word = total_duration / len(words)

    timings = []
    current_time = 0.0

    for word in words:
        # Adjust duration slightly based on word length
        word_factor = max(0.5, min(2.0, len(word) / 5.0))
        word_duration = duration_per_word * word_factor

        timings.append({
            "word": word,
            "start": round(current_time, 3),
            "end": round(current_time + word_duration, 3),
            "confidence": None,
        })
        current_time += word_duration

    # Normalize to fit total duration
    if timings:
        scale = total_duration / timings[-1]["end"]
        for t in timings:
            t["start"] = round(t["start"] * scale, 3)
            t["end"] = round(t["end"] * scale, 3)

    return timings


def _generate_word_highlight_ass(words: list[dict], output_path: str):
    """Generate ASS subtitle file with word-by-word highlighting for Shorts."""
    config = DEFAULT_CAPTION_CONFIG
    max_words = config["max_words_per_line"]

    ass_content = _ass_header(
        width=1080,
        height=1920,
        font_size=config["font_size"],
        font_name=config["font_name"],
    )

    # Group words into display lines
    groups = []
    for i in range(0, len(words), max_words):
        group = words[i:i + max_words]
        groups.append(group)

    for group in groups:
        group_start = group[0]["start"]
        group_end = group[-1]["end"]

        for i, word_data in enumerate(group):
            # Create highlighted version for current word
            line_parts = []
            for j, w in enumerate(group):
                if j == i:
                    line_parts.append(
                        f"{{\\c{config['highlight_color']}\\b1}}{w['word']}{{\\c{config['primary_color']}\\b0}}"
                    )
                else:
                    line_parts.append(w["word"])

            text = " ".join(line_parts)
            start_ts = _format_ass_time(word_data["start"])
            end_ts = _format_ass_time(word_data["end"])

            # Position in safe area (vertical center-ish)
            y_pos = 1920 // 2 + 100  # Below center, above bottom UI
            ass_content += f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{{\\an5\\pos(540,{y_pos})}}{text}\n"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ass_content)


def _generate_sentence_ass(words: list[dict], output_path: str):
    """Generate ASS subtitle file with sentence-level captions for long-form."""
    config = DEFAULT_CAPTION_CONFIG

    ass_content = _ass_header(
        width=1920,
        height=1080,
        font_size=36,
        font_name=config["font_name"],
    )

    # Group into sentences (roughly 8-12 words per line)
    sentence_group_size = 10
    groups = []
    for i in range(0, len(words), sentence_group_size):
        group = words[i:i + sentence_group_size]
        groups.append(group)

    for group in groups:
        text = " ".join(w["word"] for w in group)
        start_ts = _format_ass_time(group[0]["start"])
        end_ts = _format_ass_time(group[-1]["end"])

        # Bottom center position
        ass_content += f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{{\\an2}}{text}\n"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ass_content)


def _ass_header(width: int, height: int, font_size: int, font_name: str) -> str:
    """Generate ASS subtitle file header."""
    config = DEFAULT_CAPTION_CONFIG
    return f"""[Script Info]
Title: StoryFactory Captions
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{config['primary_color']},{config['highlight_color']},{config['outline_color']},{config['shadow_color']},1,0,0,0,100,100,0,0,1,{config['stroke_width']},{config['shadow_depth']},2,50,50,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _format_ass_time(seconds: float) -> str:
    """Format seconds to ASS timestamp (H:MM:SS.CC)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centiseconds = int((seconds % 1) * 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


def _estimate_duration(text: str) -> float:
    """Estimate reading duration in seconds."""
    word_count = len(text.split())
    return (word_count / 150) * 60
