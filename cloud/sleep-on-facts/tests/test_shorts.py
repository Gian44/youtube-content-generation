import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sof import shorts
from sof.config import Config

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


class FakeLLM:
    def __init__(self, data):
        self.data, self.calls = data, []

    def generate_json(self, prompt, **kw):
        self.calls.append((prompt, kw))
        return self.data


SECTIONS = [{"heading": "The Lagoon", "body": "The city sits on 118 islands. " * 20},
            {"heading": "Wooden Foundations", "body": "Millions of alder logs hold the city up. " * 20}]


def test_write_short_cleans_and_appends_cta():
    llm = FakeLLM({"section": 1, "hook_title": "Venice Stands on Millions of Logs",
                   "narration": "Venice rests on wooden posts! There are millions of them. They were driven "
                                "into the mud centuries ago. Without oxygen the wood hardened like stone. "
                                "That is why the city still stands today. And here is a sentence that will be cut"})
    out = shorts.write_short(llm, topic="Venice", sections=SECTIONS, words=40, model="fast")
    assert out["section"] == 1 and out["hook_title"] == "Venice Stands on Millions of Logs"
    assert "!" not in out["narration"].replace(shorts.CTA_LINE, "")
    assert out["narration"].endswith(shorts.CTA_LINE)
    assert "will be cut" not in out["narration"]           # trimmed on a sentence boundary at the word cap
    assert "Venice" in llm.calls[0][0] and "1: Wooden Foundations" in llm.calls[0][0]
    assert llm.calls[0][1]["model"] == "fast"


def test_write_short_rejects_thin_output_and_bad_section():
    with pytest.raises(RuntimeError):
        shorts.write_short(FakeLLM({"narration": "Too short.", "section": 0}), topic="X", sections=SECTIONS, words=100, model="m")
    out = shorts.write_short(FakeLLM({"narration": "One. " * 30, "section": "nine", "hook_title": "Shocking Secret Of X"}),
                             topic="Venice", sections=SECTIONS, words=100, model="m")
    assert out["section"] == 0                                   # unparsable → first chapter
    assert out["hook_title"] == "One Strange Fact About Venice"  # clickbait words → safe fallback


def test_caption_cues_cover_the_audio_in_order():
    text = "Venice rests on wooden posts. There are millions of them, driven into the mud, centuries ago, one by one. Done."
    cues = shorts.caption_cues(text, 10.0, max_chars=40)
    assert cues[0][0] == 0 and abs(cues[-1][1] - 10.0) < 0.01
    assert all(a[1] == b[0] for a, b in zip(cues, cues[1:]))
    assert all(len(c[2]) <= 40 or "," not in c[2] for c in cues)
    assert len(cues) >= 4


def test_choose_photos_prefers_named_things_from_the_fact_chapter(tmp_path):
    files = {}
    for n in ("a", "b", "c", "d"):
        f = tmp_path / f"{n}.jpg"; f.write_bytes(b"x"); files[n] = str(f)
    plan = [{"section": 0, "entity": None, "image": files["a"]},
            {"section": 1, "entity": "logs", "image": files["b"]},
            {"section": 1, "entity": None, "image": files["c"]},
            {"section": 1, "entity": "logs", "image": "/nope/missing.jpg"}]
    reel = [files["d"], files["a"]]
    out = shorts.choose_photos(section=1, plan=plan, reel=reel, labels={files["d"]: "subject"}, want=3)
    assert out == [files["b"], files["c"], files["d"]]
    assert shorts.choose_photos(section=5, plan=None, reel=reel, labels={}, want=9) == [files["d"], files["a"]]


def test_short_metadata():
    assert shorts.short_title("Venice Stands on Millions of Logs", "Venice") == "Venice Stands on Millions of Logs #Shorts"
    assert len(shorts.short_title("x" * 120, "Venice")) <= 100
    d = shorts.short_description(long_video_id="kdYKy-Fz3Ts", topic="Venice", category="cities, islands & landscapes",
                                 hours=3, credit_lines=["Ann · CC BY-SA 4.0 · Wikimedia Commons"])
    assert d.splitlines()[1] == "https://youtu.be/kdYKy-Fz3Ts" and "#Shorts" in d and "geography" in d
    assert "github" not in d.lower() and "AI" not in d
    tags = shorts.short_tags("Venice", "cities")
    assert "shorts" in tags and "venice" in tags
    assert shorts.short_publish_at("2026-10-13T00:30:00Z", delay_minutes=60) == "2026-10-13T01:30:00Z"
    assert shorts.short_publish_at(None, delay_minutes=60) is None
    # one Short a day after the first
    assert shorts.short_publish_at("2026-10-13T00:30:00Z", delay_minutes=60, index=1) == "2026-10-14T01:30:00Z"
    assert shorts.short_publish_at("2026-10-13T00:30:00Z", delay_minutes=60, index=2, spacing_hours=24) == "2026-10-15T01:30:00Z"
    # long video published at once (manual rerun): first Short at once, the rest a day apart from now
    from datetime import datetime, timezone
    now = datetime(2026, 10, 19, 9, 41, 27, tzinfo=timezone.utc)
    assert shorts.short_publish_at(None, delay_minutes=60, index=0, now=now) is None
    assert shorts.short_publish_at(None, delay_minutes=60, index=2, now=now) == "2026-10-21T09:41:00Z"


def test_config_and_cli_flags():
    from sof.config import parse_args
    cfg = Config()
    assert cfg.shorts_enabled and cfg.shorts_height == 1920 and cfg.shorts_delay_minutes == 60
    assert cfg.shorts_count == 3 and cfg.shorts_spacing_hours == 24
    assert parse_args(["--no-shorts"]).no_shorts and not parse_args([]).no_shorts


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")
def test_build_video_renders_a_vertical_captioned_short(tmp_path):
    from PIL import Image
    photos = []
    for i, size in enumerate(((1600, 900), (800, 1200), (1200, 1200))):
        p = tmp_path / f"p{i}.jpg"; Image.new("RGB", size, (40 * i + 60, 80, 120)).save(p); photos.append(str(p))
    audio = tmp_path / "short.mp3"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=4", "-ac", "1", str(audio)], check=True)
    narration = "Venice rests on wooden posts. Millions of them. " + shorts.CTA_LINE
    out = shorts.build_video(photos=photos, narration=narration, audio=str(audio), work=tmp_path, title="Venice Stands on Logs",
                             brand="Sleep On Facts", credits={photos[1]: "Photo: Ann · CC BY 4.0 · Wikimedia Commons"},
                             width=540, height=960, fps=24)
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,duration",
                            "-of", "json", out], capture_output=True, text=True, check=True)
    st = json.loads(probe.stdout)["streams"][0]
    assert (st["width"], st["height"]) == (540, 960) and abs(float(st["duration"]) - 4.0) < 0.3
    assert (tmp_path / "short_tmp" / "credit.png").exists() and (tmp_path / "short_tmp" / "cue_000.png").exists()


def test_make_is_resumable_and_uses_cached_files(tmp_path, monkeypatch):
    cfg = Config(openai_api_key="k")
    (tmp_path / "short.json").write_text(json.dumps({"section": 0, "hook_title": "H", "narration": "A fact. " + shorts.CTA_LINE}))
    (tmp_path / "short.mp3").write_bytes(b"a")
    (tmp_path / "short.mp4").write_bytes(b"v")
    p = tmp_path / "x.jpg"; p.write_bytes(b"j")
    monkeypatch.setattr(shorts.tts, "duration_seconds", lambda path: 48.0)
    monkeypatch.setattr(shorts.tts, "synthesize", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no TTS when cached")))
    meta = shorts.make(cfg, llm=None, work=tmp_path, topic="T", sections=SECTIONS, plan=None, reel=[str(p)], labels={}, credits={})
    assert meta["path"].endswith("short.mp4") and meta["seconds"] == 48.0 and meta["photos"] == [str(p)]


def test_upload_short_schedules_after_the_long_video():
    from sof import upload
    seen = {}
    class YT: pass
    def fake_upload(yt, path, body, thumb, **kw):
        seen.update(body=body, path=path, thumb=thumb); return "SHORT1"
    orig = upload.upload_video
    upload.upload_video = fake_upload
    try:
        cfg = Config(privacy="public", shorts_delay_minutes=60)
        vid, title, at = shorts.upload_short(YT(), cfg, meta={"hook_title": "Venice Stands on Logs", "path": "/w/short.mp4"},
                                         long_video_id="LONG1", long_publish_at="2026-10-13T00:30:00Z", topic="Venice",
                                         category="cities", hours=3,
                                         credit_rows=[{"source": "commons", "license": "CC BY-SA 4.0", "artist": "Ann"}])
    finally:
        upload.upload_video = orig
    assert vid == "SHORT1" and title.endswith("#Shorts") and seen["thumb"] is None
    st = seen["body"]["status"]
    assert st["privacyStatus"] == "private" and st["publishAt"] == "2026-10-13T01:30:00Z" == at
    assert "https://youtu.be/LONG1" in seen["body"]["snippet"]["description"] and "Ann" in seen["body"]["snippet"]["description"]


THREE = [{"heading": "The Lagoon", "body": "The city sits on 118 islands. " * 20},
         {"heading": "Wooden Foundations", "body": "Millions of alder logs hold the city up. " * 20},
         {"heading": "Glass", "body": "Murano glassmakers were not allowed to leave. " * 20}]
LONG = "This sentence is here to give the narration enough words to pass the floor. " * 3


def test_write_shorts_returns_distinct_chapters_titles_and_closing_lines():
    llm = FakeLLM({"shorts": [
        {"section": 1, "hook_title": "Venice Stands on Millions of Logs", "narration": "Logs hold Venice up. " + LONG},
        {"section": 1, "hook_title": "Another Log Fact", "narration": "Same chapter again. " + LONG},      # dropped: same chapter
        {"section": 0, "hook_title": "Shocking Secret", "narration": "Venice has 118 islands. " + LONG},   # clickbait → fallback
        {"section": 2, "hook_title": "Murano Kept Its Glassmakers", "narration": "Too thin."},          # dropped: thin
        {"section": 2, "hook_title": "Murano Kept Its Glassmakers", "narration": "Glassmakers stayed. " + LONG},
        {"section": 0, "hook_title": "Extra", "narration": "Beyond the count. " + LONG}]})
    out = shorts.write_shorts(llm, topic="Venice", sections=THREE, words=60, model="fast", count=3)
    assert [o["section"] for o in out] == [1, 0, 2]
    assert out[1]["hook_title"] == "A Quiet Fact About Venice"            # second fallback title, not a repeat of the first
    assert [o["narration"].endswith(c) for o, c in zip(out, shorts.CTA_LINES)] == [True, True, True]
    assert "Write 3 different Shorts" in llm.calls[0][0] and llm.calls[0][1]["model"] == "fast"


def test_write_shorts_raises_when_nothing_usable_and_count_one_uses_single_prompt():
    with pytest.raises(RuntimeError):
        shorts.write_shorts(FakeLLM({"shorts": [{"section": 0, "narration": "Thin."}]}), topic="X", sections=THREE,
                            words=60, model="m", count=3)
    one = shorts.write_shorts(FakeLLM({"section": 2, "hook_title": "Murano Kept Its Glassmakers", "narration": LONG}),
                              topic="Venice", sections=THREE, words=60, model="m", count=1)
    assert len(one) == 1 and one[0]["section"] == 2 and one[0]["narration"].endswith(shorts.CTA_LINE)


def test_choose_photos_avoids_photos_of_earlier_shorts(tmp_path):
    files = []
    for n in "abc":
        f = tmp_path / f"{n}.jpg"; f.write_bytes(b"x"); files.append(str(f))
    out = shorts.choose_photos(section=0, plan=None, reel=files, labels={}, want=2, avoid={files[0]})
    assert out == [files[1], files[2]]
    assert shorts.choose_photos(section=0, plan=None, reel=files, labels={}, want=3, avoid=set(files[:2])) == [files[2], files[0], files[1]]


def test_make_all_writes_once_and_renders_each_short_under_its_own_name(tmp_path, monkeypatch):
    cfg = Config(openai_api_key="k")
    llm = FakeLLM({"shorts": [{"section": i, "hook_title": f"Fact Number {w} About Venice", "narration": LONG}
                              for i, w in enumerate(("One", "Two", "Three"))]})
    photos = []
    for n in "abcdef":
        f = tmp_path / f"{n}.jpg"; f.write_bytes(b"x"); photos.append(str(f))
    built = []
    monkeypatch.setattr(shorts.tts, "synthesize", lambda texts, out_dir, **k: [str(Path(out_dir) / "p.mp3")])
    monkeypatch.setattr(shorts.render, "run", lambda cmd, t: Path(cmd[-1]).write_bytes(b"a"))
    monkeypatch.setattr(shorts.tts, "duration_seconds", lambda path: 47.0)
    def fake_build(*, photos, work, name, **k):
        built.append((name, list(photos))); p = work / f"{name}.mp4"; p.write_bytes(b"v"); return str(p)
    monkeypatch.setattr(shorts, "build_video", fake_build)
    cfg.shorts_photos = 2
    metas = shorts.make_all(cfg, llm=llm, work=tmp_path, topic="Venice", sections=THREE, plan=None, reel=photos,
                            labels={}, credits={}, count=3)
    assert [m["index"] for m in metas] == [0, 1, 2] and [n for n, _ in built] == ["short", "short2", "short3"]
    assert [Path(m["path"]).name for m in metas] == ["short.mp4", "short2.mp4", "short3.mp4"]
    assert len({p for _, ps in built for p in ps}) == 6                   # no photo repeated across the three
    assert (tmp_path / "shorts.json").exists() and (tmp_path / "short3.json").exists()
    # rerun: nothing is written or rendered again
    llm.calls.clear(); built.clear()
    again = shorts.make_all(cfg, llm=llm, work=tmp_path, topic="Venice", sections=THREE, plan=None, reel=photos,
                            labels={}, credits={}, count=3)
    assert llm.calls == [] and built == [] and [m["hook_title"] for m in again] == [m["hook_title"] for m in metas]


def test_make_all_skips_a_failing_short(tmp_path, monkeypatch):
    cfg = Config(openai_api_key="k")
    (tmp_path / "shorts.json").write_text(json.dumps([{"section": i, "hook_title": f"T{i}", "narration": "N."} for i in range(3)]))
    def make(cfg, *, index, item, **kw):
        if index == 1:
            raise RuntimeError("tts down")
        return {**item, "index": index, "photos": [f"p{index}"], "path": f"s{index}.mp4", "seconds": 40.0}
    monkeypatch.setattr(shorts, "make", make)
    metas = shorts.make_all(cfg, llm=None, work=tmp_path, topic="V", sections=THREE, plan=None, reel=[], labels={},
                            credits={}, count=3)
    assert [m["index"] for m in metas] == [0, 2]
