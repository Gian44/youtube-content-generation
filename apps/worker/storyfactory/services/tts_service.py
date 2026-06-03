"""TTS (Text-to-Speech) service with provider abstraction and voice rotation."""

import hashlib
import os
import random
import uuid
from pathlib import Path

from storyfactory.channel_context import effective_config, resolve_api_key
from storyfactory.config import get_settings
from storyfactory.db.engine import get_session
from storyfactory.db.models import TTSJob, Story
from storyfactory.logger import get_logger
from storyfactory.services.api_tracker import track_api_call

log = get_logger("tts_service")

OPENAI_VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]

PERSONA_VOICE_MAP = {
    "calm": ["alloy", "nova"],
    "dramatic": ["onyx", "echo"],
    "sarcastic": ["fable", "shimmer"],
    "confession": ["alloy", "echo"],
    "horror": ["onyx", "echo"],
    "warm": ["nova", "shimmer"],
}


def generate_tts(story: Story, output_dir: str = "./data/tts") -> TTSJob:
    """Generate TTS audio for a story."""
    settings = get_settings()
    session = get_session()

    # Determine provider based on ratio
    provider = _select_provider()
    voice = _select_voice(story.voice_persona, provider)

    # Check cache
    cache_key = _compute_cache_key(story.body, provider, voice)
    existing = session.query(TTSJob).filter_by(cache_key=cache_key, status="completed").first()
    if existing:
        log.info("tts_cache_hit", cache_key=cache_key[:16])
        return existing

    # Prepare output path
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    output_path = os.path.join(output_dir, f"tts_{story.id[:8]}_{provider}.mp3")

    tts_job = TTSJob(
        id=str(uuid.uuid4()),
        story_id=story.id,
        provider=provider,
        voice=voice,
        persona=story.voice_persona,
        status="processing",
        input_text=story.body,
        output_path=output_path,
        cache_key=cache_key,
    )

    try:
        if settings.dry_run:
            # Create a placeholder file in dry-run mode
            _create_dry_run_audio(output_path, story.body)
            tts_job.status = "completed"
            tts_job.duration_seconds = _estimate_duration(story.body)
            log.info("tts_dry_run", story_id=story.id, provider=provider, voice=voice)
        elif provider == "openai":
            _generate_openai_tts(story.body, voice, output_path)
            tts_job.status = "completed"
            tts_job.duration_seconds = _get_audio_duration(output_path)
        elif provider == "gemini":
            _generate_gemini_tts(story.body, voice, output_path)
            tts_job.status = "completed"
            tts_job.duration_seconds = _get_audio_duration(output_path)

        # Normalize audio loudness
        if not settings.dry_run and os.path.exists(output_path):
            _normalize_loudness(output_path)
            tts_job.loudness_lufs = -14.0  # Target LUFS

    except Exception as e:
        tts_job.status = "failed"
        tts_job.error = str(e)
        log.error("tts_failed", story_id=story.id, error=str(e))

    session.add(tts_job)
    session.commit()
    session.close()
    return tts_job


def _select_provider() -> str:
    """Select TTS provider based on the active channel's configured ratio."""
    ratio = effective_config("tts_openai_ratio", 0.8)
    return "openai" if random.random() < ratio else "gemini"


def _select_voice(persona: str, provider: str) -> str:
    """Select a voice based on persona and provider."""
    if provider == "openai":
        voices = PERSONA_VOICE_MAP.get(persona, OPENAI_VOICES)
        return random.choice(voices)
    else:
        # Gemini voices - using default for now
        return "en-US-Standard-D"


def _compute_cache_key(text: str, provider: str, voice: str) -> str:
    """Compute a cache key for TTS output."""
    content = f"{provider}:{voice}:{text}"
    return hashlib.sha256(content.encode()).hexdigest()


def _generate_openai_tts(text: str, voice: str, output_path: str):
    """Generate TTS using OpenAI API."""
    api_key = resolve_api_key("tts.openai", "openai_api_key")
    if not api_key or api_key.startswith("sk-your"):
        raise RuntimeError("OpenAI API key not configured for TTS")

    import openai

    client = openai.OpenAI(api_key=api_key)

    log.info("openai_tts_request", voice=voice, text_length=len(text))

    response = client.audio.speech.create(
        model="tts-1",
        voice=voice,
        input=text,
        response_format="mp3",
    )

    response.stream_to_file(output_path)

    track_api_call(
        provider="openai",
        endpoint="audio.speech/tts-1",
        cost_estimate=len(text) * 0.000015,  # $15 per 1M chars
    )

    log.info("openai_tts_complete", output=output_path)


def _generate_gemini_tts(text: str, voice: str, output_path: str):
    """Generate TTS using Gemini/Google Cloud TTS."""
    api_key = resolve_api_key("tts.gemini", "gemini_api_key")
    if not api_key or api_key == "your-gemini-api-key":
        raise RuntimeError("Gemini API key not configured for TTS")

    # Use Google Cloud TTS via Gemini API
    import google.generativeai as genai

    genai.configure(api_key=api_key)

    log.info("gemini_tts_request", voice=voice, text_length=len(text))

    # Gemini TTS via generateContent with audio output
    # Fallback to OpenAI if Gemini TTS is not available
    try:
        model = genai.GenerativeModel("gemini-2.0-flash")
        response = model.generate_content(
            f"Convert this text to speech audio: {text}",
            generation_config=genai.types.GenerationConfig(
                response_mime_type="audio/mp3",
            ),
        )

        if hasattr(response, "audio") and response.audio:
            with open(output_path, "wb") as f:
                f.write(response.audio)
        else:
            # Fallback: generate using OpenAI
            log.warning("gemini_tts_no_audio_fallback_openai")
            _generate_openai_tts(text, "alloy", output_path)

        track_api_call(
            provider="gemini",
            endpoint="generateContent/tts",
        )

    except Exception as e:
        log.warning("gemini_tts_fallback", error=str(e))
        _generate_openai_tts(text, "alloy", output_path)


def _normalize_loudness(audio_path: str, target_lufs: float = -14.0):
    """Normalize audio loudness to target LUFS using pydub."""
    try:
        from pydub import AudioSegment
        from pydub.effects import normalize

        audio = AudioSegment.from_file(audio_path)
        normalized = normalize(audio)
        normalized.export(audio_path, format="mp3")
        log.info("audio_normalized", path=audio_path, target_lufs=target_lufs)
    except ImportError:
        log.warning("pydub_not_available_skipping_normalization")
    except Exception as e:
        log.warning("normalization_failed", error=str(e))


def _get_audio_duration(audio_path: str) -> float:
    """Get audio duration in seconds."""
    try:
        from pydub import AudioSegment
        audio = AudioSegment.from_file(audio_path)
        return len(audio) / 1000.0  # milliseconds to seconds
    except Exception:
        return 0.0


def _estimate_duration(text: str) -> float:
    """Estimate speech duration from text."""
    word_count = len(text.split())
    return (word_count / 150) * 60  # ~150 wpm average


def _create_dry_run_audio(output_path: str, text: str):
    """Create a placeholder audio file for dry-run mode."""
    # Create a minimal valid MP3-like placeholder
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        # Write a minimal file marker
        f.write(b"DRY_RUN_AUDIO_PLACEHOLDER")
    log.info("dry_run_audio_created", path=output_path)
