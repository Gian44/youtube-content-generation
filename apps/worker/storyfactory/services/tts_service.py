"""TTS (Text-to-Speech) service with provider abstraction and voice rotation."""

import hashlib
import os
import random
import re
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

# Scripts longer than this (e.g. ~3h sleep narration) are chunked across multiple
# TTS requests and assembled/normalized with ffmpeg (streaming, bounded memory)
# instead of the pydub path used for short content.
_LONG_TEXT_CHARS = 6000

# Per-request input ceiling. tts-1/tts-1-hd accept 4096 chars; we leave headroom.
_TTS_MAX_CHARS = 3800

# Approximate USD cost per input character by model.
_TTS_CHAR_COST = {
    "tts-1": 0.000015,        # $15 / 1M chars
    "tts-1-hd": 0.000030,     # $30 / 1M chars
    "gpt-4o-mini-tts": 0.000015,
}


def generate_tts(
    story: Story,
    output_dir: str = "./data/tts",
    *,
    provider: str | None = None,
    voice: str | None = None,
    model: str | None = None,
    speed: float | None = None,
    instructions: str | None = None,
) -> TTSJob:
    """Generate TTS audio for a story.

    Optional overrides let a pipeline pin the narration (e.g. Sleep On Facts uses
    a single calm voice — ``onyx`` on ``tts-1-hd`` at ``speed=0.9``). When an
    override is ``None`` the historical behavior applies: a provider chosen by
    ratio, a persona-mapped voice, and the ``tts-1`` model.
    """
    settings = get_settings()
    session = get_session()

    # Resolve provider/voice/model (overrides win; otherwise legacy behavior).
    provider = provider or _select_provider()
    voice = voice or _select_voice(story.voice_persona, provider)
    model = model or "tts-1"

    # Long scripts (e.g. ~3h sleep narration) are chunked + assembled and
    # normalized with ffmpeg to keep memory bounded; short content keeps the
    # original pydub path.
    is_long = len(story.body or "") > _LONG_TEXT_CHARS

    # Check cache (model + speed are part of the identity).
    cache_key = _compute_cache_key(story.body, provider, voice, model, speed)
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
            _generate_openai_tts(
                story.body, voice, output_path,
                model=model, speed=speed, instructions=instructions,
            )
            tts_job.status = "completed"
        elif provider == "gemini":
            _generate_gemini_tts(
                story.body, voice, output_path,
                model=model, speed=speed, instructions=instructions,
            )
            tts_job.status = "completed"

        # Normalize loudness + measure duration. Long files use ffmpeg loudnorm +
        # ffprobe (streaming); short files keep the pydub path.
        if not settings.dry_run and os.path.exists(output_path):
            if is_long:
                normalized = _normalize_loudness_ffmpeg(output_path, target_lufs=-16.0)
                tts_job.loudness_lufs = -16.0 if normalized else None
                tts_job.duration_seconds = _audio_duration_ffprobe(output_path)
            else:
                _normalize_loudness(output_path)
                tts_job.loudness_lufs = -14.0
                tts_job.duration_seconds = _get_audio_duration(output_path)

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


def _compute_cache_key(
    text: str, provider: str, voice: str, model: str = "tts-1", speed: float | None = None
) -> str:
    """Compute a cache key for TTS output.

    Back-compat: the legacy default (``tts-1`` at default speed) keeps the original
    two-field key so pre-existing cached ``TTSJob`` rows still hit. Only non-default
    model/speed (e.g. the sleep ``tts-1-hd`` @ 0.9 path) extend the key, giving those
    a distinct identity without invalidating the existing cache.
    """
    if model == "tts-1" and speed is None:
        content = f"{provider}:{voice}:{text}"
    else:
        content = f"{provider}:{voice}:{model}:{speed}:{text}"
    return hashlib.sha256(content.encode()).hexdigest()


def _split_text_for_tts(text: str, max_chars: int = _TTS_MAX_CHARS) -> list[str]:
    """Split narration into request-sized chunks on sentence boundaries.

    Greedily packs whole sentences into chunks of at most ``max_chars`` characters,
    preserving order. A single sentence longer than ``max_chars`` is hard-split.
    Returns ``[]`` for empty input and ``[text]`` when it already fits.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    units: list[str] = []
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", para):
            sentence = sentence.strip()
            if sentence:
                units.append(sentence)

    chunks: list[str] = []
    current = ""
    for unit in units:
        if len(unit) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(unit), max_chars):
                chunks.append(unit[i : i + max_chars])
            continue
        if not current:
            current = unit
        elif len(current) + 1 + len(unit) <= max_chars:
            current = f"{current} {unit}"
        else:
            chunks.append(current)
            current = unit
    if current:
        chunks.append(current)
    return chunks


def _build_speech_kwargs(
    text: str, voice: str, model: str, speed: float | None, instructions: str | None
) -> dict:
    """Build the kwargs for ``client.audio.speech.create``. Pure (no I/O).

    ``speed`` is included only when set. ``instructions`` is honored only by the
    steerable ``gpt-4o-mini-tts`` model (it is ignored/unsupported on tts-1*).
    """
    kwargs: dict = {"model": model, "voice": voice, "input": text, "response_format": "mp3"}
    if speed is not None:
        kwargs["speed"] = speed
    if instructions and "gpt-4o-mini-tts" in model:
        kwargs["instructions"] = instructions
    return kwargs


def _tts_cost(text: str, model: str) -> float:
    """Estimate USD cost of voicing ``text`` with ``model``."""
    return len(text) * _TTS_CHAR_COST.get(model, 0.000015)


def _nonempty_file(path: str) -> bool:
    """True if ``path`` exists and is larger than zero bytes."""
    try:
        return os.path.exists(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def _openai_tts_chunk(
    client,
    text: str,
    output_path: str,
    *,
    voice: str,
    model: str,
    speed: float | None,
    instructions: str | None,
) -> None:
    """Voice a single chunk to ``output_path`` via the OpenAI speech API."""
    kwargs = _build_speech_kwargs(text, voice, model, speed, instructions)
    response = client.audio.speech.create(**kwargs)
    response.stream_to_file(output_path)


def _generate_openai_tts(
    text: str,
    voice: str,
    output_path: str,
    *,
    model: str = "tts-1",
    speed: float | None = None,
    instructions: str | None = None,
):
    """Generate TTS using the OpenAI API, chunking long text and concatenating.

    Short text is a single request (legacy behavior). Long text is split on
    sentence boundaries, each chunk voiced with the SAME voice/model/speed, then
    assembled with ffmpeg so memory stays bounded for multi-hour narration.
    """
    api_key = resolve_api_key("tts.openai", "openai_api_key")
    if not api_key or api_key.startswith("sk-your"):
        raise RuntimeError("OpenAI API key not configured for TTS")

    import openai

    client = openai.OpenAI(api_key=api_key)

    chunks = _split_text_for_tts(text)
    if not chunks:
        raise RuntimeError("Cannot synthesize empty TTS text")

    log.info(
        "openai_tts_request", voice=voice, model=model, chunks=len(chunks), text_length=len(text)
    )

    if len(chunks) == 1:
        _openai_tts_chunk(
            client, chunks[0], output_path,
            voice=voice, model=model, speed=speed, instructions=instructions,
        )
    else:
        tmp_dir = Path(output_path).parent / f"_tts_chunks_{uuid.uuid4().hex[:8]}"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        parts: list[str] = []
        try:
            for i, chunk in enumerate(chunks):
                part = str(tmp_dir / f"part_{i:04d}.mp3")
                _openai_tts_chunk(
                    client, chunk, part,
                    voice=voice, model=model, speed=speed, instructions=instructions,
                )
                parts.append(part)
            _concat_audio_files(parts, output_path)
        finally:
            # rmtree (not rmdir) so a partial file written by a failed mid-stream
            # chunk — which never made it into ``parts`` — is also cleaned up.
            import shutil

            shutil.rmtree(tmp_dir, ignore_errors=True)

    track_api_call(
        provider="openai",
        endpoint=f"audio.speech/{model}",
        cost_estimate=_tts_cost(text, model),
    )

    log.info("openai_tts_complete", output=output_path)


def _concat_audio_files(parts: list[str], output_path: str) -> None:
    """Concatenate MP3 chunk files into ``output_path`` via the ffmpeg concat demuxer.

    Tries a fast stream copy first; falls back to re-encoding if the copy fails
    (handles minor per-chunk header variance).
    """
    import subprocess

    list_file = Path(output_path).parent / f"_tts_concat_{uuid.uuid4().hex[:8]}.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for part in parts:
            f.write(f"file '{os.path.abspath(part)}'\n")

    try:
        copy_cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file), "-c", "copy", output_path,
        ]
        result = subprocess.run(copy_cmd, capture_output=True, text=True, timeout=1800)
        # A stream copy of mismatched-header MP3s can exit 0 yet write a zero-byte
        # file, so verify a non-empty output before trusting it; otherwise re-encode.
        if result.returncode != 0 or not _nonempty_file(output_path):
            reencode_cmd = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(list_file), "-c:a", "libmp3lame", "-q:a", "2", output_path,
            ]
            result = subprocess.run(reencode_cmd, capture_output=True, text=True, timeout=3600)
            if result.returncode != 0 or not _nonempty_file(output_path):
                raise RuntimeError(f"TTS concat failed: {result.stderr[-300:]}")
    finally:
        try:
            os.remove(list_file)
        except OSError:
            pass


def _generate_gemini_tts(
    text: str,
    voice: str,
    output_path: str,
    *,
    model: str = "tts-1",
    speed: float | None = None,
    instructions: str | None = None,
):
    """Generate TTS using Gemini/Google Cloud TTS.

    On any failure it falls back to OpenAI, threading the caller's ``model`` /
    ``speed`` / ``instructions`` through so overrides aren't silently dropped. The
    Gemini API call is only tracked when Gemini actually produced the audio (the
    fallback's own OpenAI call is tracked inside :func:`_generate_openai_tts`).
    """
    api_key = resolve_api_key("tts.gemini", "gemini_api_key")
    if not api_key or api_key == "your-gemini-api-key":
        raise RuntimeError("Gemini API key not configured for TTS")

    # Use Google Cloud TTS via Gemini API
    import google.generativeai as genai

    genai.configure(api_key=api_key)

    log.info("gemini_tts_request", voice=voice, text_length=len(text))

    # Gemini TTS via generateContent with audio output.
    # Fallback to OpenAI if Gemini TTS is not available. The OpenAI fallback uses a
    # neutral OpenAI voice ("alloy") because the resolved gemini voice name (e.g.
    # "en-US-Standard-D") is not a valid OpenAI voice.
    try:
        gemini_model = genai.GenerativeModel("gemini-2.0-flash")
        response = gemini_model.generate_content(
            f"Convert this text to speech audio: {text}",
            generation_config=genai.types.GenerationConfig(
                response_mime_type="audio/mp3",
            ),
        )

        if hasattr(response, "audio") and response.audio:
            with open(output_path, "wb") as f:
                f.write(response.audio)
            track_api_call(provider="gemini", endpoint="generateContent/tts")
        else:
            log.warning("gemini_tts_no_audio_fallback_openai")
            _generate_openai_tts(
                text, "alloy", output_path,
                model=model, speed=speed, instructions=instructions,
            )

    except Exception as e:
        log.warning("gemini_tts_fallback", error=str(e))
        _generate_openai_tts(
            text, "alloy", output_path,
            model=model, speed=speed, instructions=instructions,
        )


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


def _normalize_loudness_ffmpeg(audio_path: str, target_lufs: float = -16.0) -> bool:
    """Normalize loudness with ffmpeg ``loudnorm`` (streaming, low memory).

    Used for multi-hour narration where loading the whole file into pydub would
    consume gigabytes of RAM. Writes to a temp file then atomically replaces.
    Returns True only when normalization actually succeeded, so the caller can
    avoid recording a loudness it did not achieve.
    """
    import subprocess

    tmp_path = f"{audio_path}.norm.mp3"
    try:
        cmd = [
            "ffmpeg", "-y", "-i", audio_path,
            "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11",
            "-c:a", "libmp3lame", "-q:a", "2",
            tmp_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if result.returncode == 0 and _nonempty_file(tmp_path):
            os.replace(tmp_path, audio_path)
            log.info("audio_normalized_ffmpeg", path=audio_path, target_lufs=target_lufs)
            return True
        log.warning("ffmpeg_loudnorm_failed", stderr=result.stderr[-300:])
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return False
    except Exception as e:
        log.warning("ffmpeg_loudnorm_error", error=str(e))
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return False


def _audio_duration_ffprobe(audio_path: str) -> float:
    """Get audio duration in seconds via ffprobe (no full-file decode)."""
    import subprocess

    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            audio_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception as e:
        log.warning("ffprobe_duration_failed", path=audio_path, error=str(e))
    return 0.0


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
