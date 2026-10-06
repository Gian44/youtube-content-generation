import httpx
from sof.tts import chunk_text, synthesize, concat_cmd, loudnorm_cmd, estimate_cost_usd


def test_chunking_respects_limit_and_sentences():
    text = ("This is a sentence. " * 300).strip()
    chunks = chunk_text(text, 4000)
    assert all(len(c) <= 4000 for c in chunks)
    assert " ".join(chunks) == text
    assert all(c.rstrip().endswith(".") for c in chunks)
    assert chunk_text("x" * 9000, 4000) == ["x" * 4000, "x" * 4000, "x" * 1000]


def test_synthesize_writes_chunks_and_skips_existing(tmp_path):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, content=b"ID3mp3")
    t = httpx.MockTransport(handler)
    kw = dict(api_key="sk", model="tts-1", voice="onyx", speed=0.9, transport=t)
    paths = synthesize(["one.", "two."], str(tmp_path), **kw)
    assert len(paths) == 2 and all((tmp_path / f"{i:04d}.mp3").exists() for i in range(2)) and len(calls) == 2
    synthesize(["one.", "two."], str(tmp_path), **kw)
    assert len(calls) == 2


def test_synthesize_retries_then_fails(tmp_path):
    t = httpx.MockTransport(lambda r: httpx.Response(503))
    try:
        synthesize(["one."], str(tmp_path), api_key="sk", model="tts-1", voice="onyx", speed=0.9, transport=t, retries=1, sleep=lambda s: None)
    except RuntimeError as exc:
        assert "503" in str(exc)
    else:
        raise AssertionError("expected failure")


def test_commands_and_cost():
    assert concat_cmd("l.txt", "o.mp3")[:4] == ["ffmpeg", "-y", "-f", "concat"]
    assert "loudnorm=I=-18" in " ".join(loudnorm_cmd("i.mp3", "o.mp3"))
    assert abs(estimate_cost_usd(150_000, "tts-1") - 2.25) < 0.01
