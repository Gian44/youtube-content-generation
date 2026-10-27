import io
import json
from pathlib import Path

import httpx
from PIL import Image

from sof import cues


def _jpeg(color=(90, 120, 150), size=(1920, 1080)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "JPEG", quality=80)
    return b.getvalue()


def test_split_passages_keeps_paragraphs_and_cuts_long_ones():
    long = " ".join(f"Sentence number {i} talks about the glacier and its slow movement." for i in range(30))
    text = "First paragraph is short but long enough to stand alone as a passage of narration here.\n\n" \
           + "A" * 200 + ".\n\n" + long
    parts = cues.split_passages(text, target=300, max_chars=600, min_chars=60)
    assert parts[0][0] == 0 and parts[0][1].startswith("First paragraph")
    assert all(text[off:off + 20] == p[:20] for off, p in parts)
    assert len(parts) > 3 and all(len(p) <= 700 for _, p in parts[2:])


def test_split_passages_merges_scraps():
    parts = cues.split_passages("x" * 300 + "\n\nTiny.", min_chars=50)
    assert len(parts) == 1 and parts[0][1].endswith("Tiny.")


class FakeLLM:
    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def generate_json(self, prompt, **kw):
        self.prompts.append(prompt)
        return self.answer


def test_write_cues_keeps_only_valid_passages():
    body = "Wind tears across the summit at great speed every winter afternoon.\n\n" \
           "The idea of measurement is abstract and hard to picture in any way at all, truly."
    llm = FakeLLM({"passages": [{"n": 1, "shot": "snow plume off a summit", "stock": ["snow plume", "summit wind", "x", "y"],
                                 "commons": "Everest snow plume"},
                                {"n": 2, "shot": "", "stock": ["a"]}, {"n": 9, "shot": "bad", "stock": ["b"]}, "junk"]})
    rows = cues.write_cues(llm, [{"heading": "Winds", "body": body}], topic="Mount Everest")
    assert len(rows) == 1
    r = rows[0]
    assert r["shot"] == "snow plume off a summit" and r["stock"] == ["snow plume", "summit wind"]
    assert r["commons"] == "Everest snow plume" and r["quote"].startswith("Wind tears") and r["pos"] == 0
    assert "[1] Wind tears" in llm.prompts[0] and "Mount Everest" in llm.prompts[0]


def test_time_cues_runs_each_passage_to_the_next():
    a = "Wind tears across the summit at great speed every winter afternoon."
    b = "Climbers wait in their tents for the jet stream to lift."
    rows = [{"section": 0, "n": 1, "text": a, "quote": "Wind tears across the summit", "pos": 0.0, "shot": "s1"},
            {"section": 0, "n": 2, "text": b, "quote": "Climbers wait in their tents", "pos": 0.5, "shot": "s2"}]
    out = cues.time_cues(rows, chunks=[a + " " + b], owner=[0], chunk_durations=[100.0], total=100.0)
    assert [r["shot"] for r in out] == ["s1", "s2"]
    assert out[0]["start"] == 0 and 50 < out[0]["end"] < 60 and out[1]["end"] == 100.0
    assert "key" not in out[0]


def _plan(n, **over):
    rows = [{"slot": k, "entity": None, "why": "subject reel", "mention_time": None, "held_for": None} for k in range(n)]
    rows[0]["why"] = "opening: best subject photo"
    for k, r in over.items():
        rows[int(k)].update(r)
    return rows


def test_open_slot_rules():
    assert not cues.open_slot({"slot": 0, "why": "opening"}, hold_after=60)
    assert not cues.open_slot({"slot": 3, "entity": "x", "mention_time": 40.0, "why": "'x' named"}, hold_after=60)
    assert not cues.open_slot({"slot": 3, "entity": "x", "held_for": 30.0, "why": "holding"}, hold_after=60)
    assert cues.open_slot({"slot": 3, "entity": "x", "held_for": 80.0, "why": "holding"}, hold_after=60)
    assert cues.open_slot({"slot": 3, "entity": None, "why": "subject reel"}, hold_after=60)


def test_apply_cues_fills_only_open_slots_and_rotates():
    plan = _plan(10, **{"3": {"entity": "x", "mention_time": 55.0, "why": "'x' named"}})
    slots = [f"p{k}.jpg" for k in range(10)]
    cue = {"start": 0.0, "end": 200.0, "shot": "ice ridge"}
    out, rows, n = cues.apply_cues(slots, plan, [cue], {0: ["a.jpg", "b.mp4"]}, dwell=20.0)
    assert out[0] == "p0.jpg" and out[3] == "p3.jpg"
    assert n == 8 and set(out[1:3] + out[4:]) == {"a.jpg", "b.mp4"}
    assert all(out[k] != out[k + 1] for k in range(4, 9))
    assert rows[1]["cue"] == "ice ridge" and rows[1]["why"].startswith("cue:") and rows[1]["entity"] is None
    assert plan[1]["why"] == "subject reel"                       # input untouched
    assert cues.apply_cues(slots, plan, [cue], {}, dwell=20.0)[2] == 0


def test_select_prefers_cues_covering_open_slots():
    plan = _plan(10, **{str(k): {"entity": "x", "mention_time": 1.0} for k in range(5, 10)})
    c1 = {"start": 0.0, "end": 100.0, "shot": "a"}     # slots 0-4: 4 open (slot 0 is the opening)
    c2 = {"start": 100.0, "end": 200.0, "shot": "b"}   # all slots answer mentions
    c3 = {"start": 20.0, "end": 60.0, "shot": "c"}     # 2 open
    assert [c["shot"] for c in cues.select([c1, c2, c3], plan, dwell=20.0, max_cues=5)] == ["a", "c"]
    assert [c["shot"] for c in cues.select([c1, c2, c3], plan, dwell=20.0, max_cues=1)] == ["a"]


class FakeVision:
    active = ["fake"]

    def __init__(self, match):
        self.match, self.prompts = match, []

    def ask(self, prompt, image):
        self.prompts.append(prompt)
        assert Path(image).exists()
        return {"match": self.match}


def test_fetch_cue_media_keeps_only_tiles_the_vision_check_approves(tmp_path):
    shades: dict[str, int] = {}

    def handler(req: httpx.Request):
        u = str(req.url)
        if "pixabay.com/api/videos" in u:
            return httpx.Response(200, json={"hits": [{"id": 5, "duration": 20, "pageURL": "pg", "user": "u",
                                                      "tags": "snow", "videos": {"large": {"url": "https://v/clip.mp4",
                                                      "width": 1920, "height": 1080, "thumbnail": "https://v/t.jpg"}}}]})
        if "pixabay.com/api" in u:
            q = req.url.params["q"]
            return httpx.Response(200, json={"hits": [{"largeImageURL": f"https://i/{q}-{j}.jpg", "tags": q,
                                                       "user": "u", "pageURL": "pg"} for j in range(2)]})
        if "commons.wikimedia.org" in u:
            return httpx.Response(200, json={"query": {"pages": []}})
        if u.endswith(".mp4"):
            return httpx.Response(200, content=b"m" * 50_000)
        shade = shades.setdefault(str(req.url), 30 * len(shades))  # a clearly different picture per URL
        return httpx.Response(200, content=_jpeg((shade, 100, 150)), headers={"content-type": "image/jpeg"})

    cue = {"section": 0, "n": 1, "text": "Wind tears across the summit.", "shot": "snow plume",
           "stock": ["snow plume", "summit wind"], "commons": "Everest", "start": 0, "end": 60}
    vis = FakeVision([5, 1, 99, "x"])
    media = cues.fetch_cue_media([cue], str(tmp_path), topic="Mount Everest", vis=vis, pexels_key="",
                                 pixabay_key="k", transport=httpx.MockTransport(handler), per_cue=3)
    assert list(media) == [0] and len(media[0]) == 2
    assert media[0][0].endswith(".mp4") and media[0][1].endswith("000.jpg")    # best first, clip downloaded
    assert "Wind tears across the summit." in vis.prompts[0] and "snow plume" in vis.prompts[0]
    meta = json.loads((tmp_path / "_cue000" / "cue.json").read_text())
    assert meta["tiles"] == 5 and len(meta["approved"]) == 2
    assert (tmp_path / "_cue000" / "credits.json").exists()


def test_fetch_cue_media_nothing_approved(tmp_path):
    def handler(req):
        if "pixabay.com/api" in str(req.url):
            return httpx.Response(200, json={"hits": [{"largeImageURL": "https://i/a.jpg", "tags": "t"}]})
        return httpx.Response(200, content=_jpeg())

    cue = {"text": "t", "shot": "s", "stock": ["q"], "commons": ""}
    media = cues.fetch_cue_media([cue], str(tmp_path), topic="T", vis=FakeVision([]), pexels_key="", pixabay_key="k",
                                 transport=httpx.MockTransport(handler))
    assert media == {}
