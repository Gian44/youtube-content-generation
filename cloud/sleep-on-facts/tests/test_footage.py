import json

import httpx

from sof import footage


def test_is_video():
    assert footage.is_video("a/b.MP4") and footage.is_video("x.webm") and not footage.is_video("a.jpg")


def test_pexels_and_pixabay_parsing_keeps_landscape_hd_clips():
    def handler(req):
        if "pexels" in str(req.url):
            return httpx.Response(200, json={"videos": [
                {"id": 1, "duration": 20, "url": "https://www.pexels.com/video/slow-ocean-waves-1/", "user": {"name": "Ann"},
                 "video_pictures": [{"picture": "p0"}, {"picture": "p1"}, {"picture": "p2"}],
                 "video_files": [{"file_type": "video/mp4", "width": 3840, "height": 2160, "link": "uhd"},
                                 {"file_type": "video/mp4", "width": 1920, "height": 1080, "link": "hd"},
                                 {"file_type": "video/mp4", "width": 640, "height": 360, "link": "sd"}]},
                {"id": 2, "duration": 4, "url": "u", "video_pictures": [{"picture": "p"}],
                 "video_files": [{"file_type": "video/mp4", "width": 1920, "height": 1080, "link": "short"}]},
                {"id": 3, "duration": 30, "url": "u", "video_pictures": [{"picture": "p"}],
                 "video_files": [{"file_type": "video/mp4", "width": 1080, "height": 1920, "link": "portrait"}]}]})
        return httpx.Response(200, json={"hits": [
            {"id": 9, "duration": 15, "pageURL": "pg", "tags": "ocean, waves", "user": "Bob",
             "videos": {"large": {"url": "L", "width": 1920, "height": 1080, "thumbnail": "T"}}},
            {"id": 10, "duration": 15, "videos": {"large": {"url": "", "width": 0, "height": 0},
                                                 "medium": {"url": "M", "width": 960, "height": 540, "thumbnail": "t"}}}]})
    out = footage.search(["ocean waves"], pexels_key="k", pixabay_key="k", transport=httpx.MockTransport(handler))
    assert [(o["id"], o["url"], o["preview"]) for o in out] == [("pexels-1", "hd", "p1"), ("pixabay-9", "L", "T")]
    assert out[0]["desc"] == "slow ocean waves 1" and out[1]["license"] == "Pixabay Content License"
    assert footage.search(["x"], pexels_key="", pixabay_key="", transport=httpx.MockTransport(handler)) == []


def test_gather_downloads_only_clips_whose_preview_passes(tmp_path):
    cands = [{"id": f"pexels-{i}", "url": f"https://v/{i}.mp4", "preview": f"https://p/{i}.jpg", "seconds": 20,
              "source": "pexels", "license": "Pexels License", "page": "pg", "artist": "a", "query": "q"} for i in range(3)]
    body = b"x" * 5000

    def handler(req):
        return httpx.Response(200, content=body)

    def classify(paths):
        return {p: ("reject" if i == 1 else "subject" if i == 2 else "setting") for i, p in enumerate(sorted(paths))}
    order = sorted(str(tmp_path / f) for f in [])                         # noqa: F841
    clips = footage.gather(cands, str(tmp_path), classify_fn=classify, want=5, transport=httpx.MockTransport(handler))
    rows = json.loads((tmp_path / "footage.json").read_text())
    assert len(clips) == 1 and {r["label"] for r in rows} == {"subject"}   # scenery clips read as off-topic: dropped
    assert all(c.endswith(".mp4") for c in clips)


def test_mix_gives_every_third_filler_slot_to_footage_and_spaces_clips():
    slots = [f"p{k}.jpg" for k in range(30)]
    plan = [{"entity": "titan" if 10 <= k < 13 else None} for k in range(30)]
    out, n = footage.mix_into_slots(slots, plan, ["a.mp4", "b.mp4"], every=3, max_uses=3, gap=4)
    vids = [k for k, s in enumerate(out) if s.endswith(".mp4")]
    assert out[0] == "p0.jpg" and all(not (10 <= k < 13) for k in vids) and n == len(vids) > 0
    assert all(b - a > 1 for a, b in zip(vids, vids[1:]))                  # never two clips in a row
    for c in ("a.mp4", "b.mp4"):
        ks = [k for k in vids if out[k] == c]
        assert len(ks) <= 3 and all(b - a > 4 for a, b in zip(ks, ks[1:]))
    assert footage.mix_into_slots(slots, plan, []) == (slots, 0)


def test_mix_never_replaces_a_chapter_photo():
    slots = [f"p{k}.jpg" for k in range(30)]
    plan = [{"entity": None, "why": "section photo" if k < 15 else "subject reel"} for k in range(30)]
    out, n = footage.mix_into_slots(slots, plan, ["a.mp4", "b.mp4"], every=1, max_uses=9, gap=1)
    vids = [k for k, s in enumerate(out) if s.endswith(".mp4")]
    assert n > 0 and all(k >= 15 for k in vids)
