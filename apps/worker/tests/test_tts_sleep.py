"""tts_service — long-form chunking + single calm voice for Sleep On Facts.

A 3-hour script far exceeds OpenAI's per-request input limit, so the body is
split on sentence boundaries, voiced chunk-by-chunk with one fixed voice, and
the audio is concatenated. These tests pin the splitter, the request-kwargs
builder, the cache key, and the chunk orchestration — all without real API calls.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# _split_text_for_tts
# ---------------------------------------------------------------------------

def test_split_short_text_is_single_chunk():
    from storyfactory.services import tts_service as t

    assert t._split_text_for_tts("Just one short line.", max_chars=3800) == [
        "Just one short line."
    ]


def test_split_empty_text_returns_empty_list():
    from storyfactory.services import tts_service as t

    assert t._split_text_for_tts("   ", max_chars=3800) == []


def test_split_packs_sentences_under_limit_and_preserves_order():
    from storyfactory.services import tts_service as t

    sentences = [f"Sentence number {i}." for i in range(40)]
    text = " ".join(sentences)

    chunks = t._split_text_for_tts(text, max_chars=100)

    assert len(chunks) > 1
    assert all(len(c) <= 100 for c in chunks)
    assert all(c.strip() for c in chunks)
    # Every sentence survives, in order.
    joined = " ".join(chunks)
    positions = [joined.index(f"Sentence number {i}.") for i in range(40)]
    assert positions == sorted(positions)


def test_split_hard_splits_an_oversized_sentence():
    from storyfactory.services import tts_service as t

    giant = "word " * 100  # 500 chars, no sentence break
    chunks = t._split_text_for_tts(giant.strip(), max_chars=80)

    assert len(chunks) > 1
    assert all(len(c) <= 80 for c in chunks)


# ---------------------------------------------------------------------------
# _build_speech_kwargs
# ---------------------------------------------------------------------------

def test_speech_kwargs_passthrough_model_voice_input():
    from storyfactory.services import tts_service as t

    kw = t._build_speech_kwargs("hello", voice="onyx", model="tts-1-hd", speed=None, instructions=None)
    assert kw["model"] == "tts-1-hd"
    assert kw["voice"] == "onyx"
    assert kw["input"] == "hello"
    assert kw["response_format"] == "mp3"
    assert "speed" not in kw          # omitted when None
    assert "instructions" not in kw


def test_speech_kwargs_includes_speed_when_set():
    from storyfactory.services import tts_service as t

    kw = t._build_speech_kwargs("x", voice="onyx", model="tts-1-hd", speed=0.9, instructions=None)
    assert kw["speed"] == 0.9


def test_speech_kwargs_instructions_only_for_steerable_model():
    from storyfactory.services import tts_service as t

    # Fixed model ignores instructions (the param is unsupported there).
    kw_fixed = t._build_speech_kwargs(
        "x", voice="onyx", model="tts-1-hd", speed=0.9, instructions="speak softly"
    )
    assert "instructions" not in kw_fixed

    # Steerable model carries them through.
    kw_steer = t._build_speech_kwargs(
        "x", voice="onyx", model="gpt-4o-mini-tts", speed=None, instructions="speak softly"
    )
    assert kw_steer["instructions"] == "speak softly"


# ---------------------------------------------------------------------------
# cache key
# ---------------------------------------------------------------------------

def test_cache_key_varies_by_model_and_speed():
    from storyfactory.services import tts_service as t

    base = t._compute_cache_key("body", "openai", "onyx", "tts-1", None)
    diff_model = t._compute_cache_key("body", "openai", "onyx", "tts-1-hd", None)
    diff_speed = t._compute_cache_key("body", "openai", "onyx", "tts-1", 0.9)

    assert base != diff_model
    assert base != diff_speed
    assert base == t._compute_cache_key("body", "openai", "onyx", "tts-1", None)


# ---------------------------------------------------------------------------
# chunk orchestration
# ---------------------------------------------------------------------------

def test_generate_openai_tts_single_chunk_no_concat(monkeypatch, tmp_path):
    from storyfactory.services import tts_service as t

    monkeypatch.setattr(t, "resolve_api_key", lambda *a, **k: "sk-real")
    monkeypatch.setattr(t, "track_api_call", lambda **k: None)

    calls = []

    def _fake_chunk(client, text, output_path, *, voice, model, speed, instructions):
        calls.append((text, voice, model, speed, instructions))
        open(output_path, "wb").close()

    def _no_concat(parts, output_path):
        raise AssertionError("single chunk must not concat")

    monkeypatch.setattr(t, "_openai_tts_chunk", _fake_chunk)
    monkeypatch.setattr(t, "_concat_audio_files", _no_concat)

    out = str(tmp_path / "out.mp3")
    t._generate_openai_tts("A short calm line.", "onyx", out, model="tts-1-hd", speed=0.9)

    assert len(calls) == 1
    assert calls[0][1] == "onyx" and calls[0][2] == "tts-1-hd" and calls[0][3] == 0.9


def test_generate_openai_tts_multi_chunk_concats(monkeypatch, tmp_path):
    from storyfactory.services import tts_service as t

    monkeypatch.setattr(t, "resolve_api_key", lambda *a, **k: "sk-real")
    monkeypatch.setattr(t, "track_api_call", lambda **k: None)

    chunk_calls = []
    concat_calls = []

    def _fake_chunk(client, text, output_path, *, voice, model, speed, instructions):
        chunk_calls.append((voice, model, speed, instructions))
        open(output_path, "wb").close()

    def _fake_concat(parts, output_path):
        concat_calls.append((list(parts), output_path))
        open(output_path, "wb").close()

    monkeypatch.setattr(t, "_openai_tts_chunk", _fake_chunk)
    monkeypatch.setattr(t, "_concat_audio_files", _fake_concat)

    long_text = " ".join(f"Calm sentence number {i}." for i in range(400))
    out = str(tmp_path / "out.mp3")
    t._generate_openai_tts(long_text, "onyx", out, model="tts-1-hd", speed=0.9)

    assert len(chunk_calls) > 1
    assert len(concat_calls) == 1
    # one part file per chunk, all voiced with the same fixed voice/model/speed
    assert len(concat_calls[0][0]) == len(chunk_calls)
    assert all(c == ("onyx", "tts-1-hd", 0.9, None) for c in chunk_calls)
    assert concat_calls[0][1] == out
