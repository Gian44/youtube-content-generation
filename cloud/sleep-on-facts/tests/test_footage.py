import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from sof import footage

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


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
    assert out[0]["probe"] == "sd" and out[1]["probe"] == "L"           # the smallest rendition is the one analysed
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




# ---------------------------------------------------------------- sources (Gian 2026-11: NASA, Commons, then named stock)

def test_stock_clips_must_name_the_subject_in_their_own_metadata():
    def handler(req):
        if "pexels" in str(req.url):
            return httpx.Response(200, json={"videos": [
                {"id": i, "duration": 20, "url": f"https://www.pexels.com/video/{slug}-{i}/", "user": {"name": "Ann"},
                 "video_pictures": [{"picture": "p"}],
                 "video_files": [{"file_type": "video/mp4", "width": 1920, "height": 1080, "link": f"hd{i}"}]}
                for i, slug in ((1, "saturn-rings-in-space"), (2, "starry-night-sky"))]})
        return httpx.Response(200, json={"hits": [
            {"id": 9, "duration": 15, "pageURL": "pg", "tags": "planet, saturn, rings", "user": "Bob",
             "videos": {"large": {"url": "L", "width": 1920, "height": 1080, "thumbnail": "T"}}},
            {"id": 10, "duration": 15, "pageURL": "pg", "tags": "galaxy, stars", "user": "Bob",
             "videos": {"large": {"url": "L2", "width": 1920, "height": 1080, "thumbnail": "T"}}}]})
    t = httpx.MockTransport(handler)
    gated = footage.search(["saturn"], pexels_key="k", pixabay_key="k", transport=t, terms=["saturn"])
    assert [o["id"] for o in gated] == ["pexels-1", "pixabay-9"]
    assert len(footage.search(["saturn"], pexels_key="k", pixabay_key="k", transport=t)) == 4    # b-roll callers: ungated


def test_nasa_videos_are_parsed_named_and_credited():
    def handler(req):
        u = str(req.url)
        if "/search" in u:
            assert req.url.params["media_type"] == "video"
            return httpx.Response(200, json={"collection": {"items": [
                {"data": [{"nasa_id": "GSFC 2017 Saturn", "title": "Saturn's Rings Are Disappearing", "center": "GSFC",
                           "secondary_creator": "David Ladd", "description": "Cassini looks at Saturn.", "keywords": ["Saturn"]}],
                 "links": [{"href": "http://images-assets.nasa.gov/video/GSFC 2017 Saturn/GSFC 2017 Saturn~thumb.jpg"}]},
                {"data": [{"nasa_id": "JSC-1", "title": "Space station tour", "center": "JSC"}]},          # does not name it
                {"data": [{"nasa_id": "X-2", "title": "Saturn © Some Studio", "center": "JPL"}]}]}})      # third-party
        assert "/asset/GSFC%202017%20Saturn" in u
        return httpx.Response(200, json={"collection": {"items": [
            {"href": "http://images-assets.nasa.gov/video/GSFC 2017 Saturn/GSFC 2017 Saturn~orig.mp4"},
            {"href": "http://images-assets.nasa.gov/video/GSFC 2017 Saturn/GSFC 2017 Saturn~large.mp4"},
            {"href": "http://images-assets.nasa.gov/video/GSFC 2017 Saturn/GSFC 2017 Saturn~small.mp4"},
            {"href": "http://images-assets.nasa.gov/video/GSFC 2017 Saturn/metadata.json"}]}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        got = footage.search_nasa(c, "Saturn", ["saturn"])
        assert len(got) == 1
        g = got[0]
        assert (g["id"], g["source"], g["artist"], g["license"]) == (
            "nasa-GSFC 2017 Saturn", "nasa", "NASA/GSFC / David Ladd", "NASA (public domain)")
        assert g["page"] == "https://images.nasa.gov/details/GSFC%202017%20Saturn" and g["title"] == "Saturn's Rings Are Disappearing"
        r = footage.nasa_renditions(c, g["nasa_id"])
    base = "https://images-assets.nasa.gov/video/GSFC%202017%20Saturn/GSFC%202017%20Saturn"
    assert r == {"probe": base + "~small.mp4", "url": base + "~large.mp4", "fallback": [base + "~orig.mp4"]}


def _vi(title, *, w=1920, h=1080, dur=30.0, lic="CC BY 2.0", nonfree="", desc="", derivs=None, size=9_000_000):
    vp9 = 'video/webm; codecs="vp9, opus"'
    return {"title": title, "index": 1, "videoinfo": [{
        "width": w, "height": h, "duration": dur, "size": size, "url": f"https://upload.wikimedia.org/{title}.webm",
        "thumburl": "https://upload.wikimedia.org/thumb.jpg", "descriptionurl": f"https://commons.wikimedia.org/wiki/{title}",
        "extmetadata": {"LicenseShortName": {"value": lic}, "Artist": {"value": "<a href='u'>NASA/JPL</a>"},
                        "NonFree": {"value": nonfree}, "ImageDescription": {"value": desc}},
        "derivatives": derivs if derivs is not None else [
            {"src": "https://upload.wikimedia.org/t/240p.vp9.webm", "type": vp9, "height": 240},
            {"src": "https://upload.wikimedia.org/t/360p.vp9.webm", "type": vp9, "height": 360},
            {"src": "https://upload.wikimedia.org/t/720p.vp9.webm", "type": vp9, "height": 720},
            {"src": "https://upload.wikimedia.org/t/1080p.vp9.webm", "type": vp9, "height": 1080},
            {"src": "https://upload.wikimedia.org/t/360p.mp4", "type": "video/quicktime", "transcodekey": "360p.mp4"}]}]}


def test_commons_videos_keep_free_landscape_named_clips_with_good_renditions():
    pages = [_vi("File:Video - Spinning Saturn - Cassini.webm", h=1332),
             _vi("File:PIA21047 - Saturn Rotation Cassini.ogv", w=500, h=500, lic="Public domain"),   # square
             _vi("File:Saturn Lightning.ogv", w=1280, h=720, lic="Public domain", derivs=[]),       # original only
             _vi("File:Saturn nc.webm", lic="CC BY-NC 2.0"),
             _vi("File:Saturn short.webm", dur=4),
             _vi("File:Jupiter flyby.webm", desc="Jupiter clouds"),                                  # not Saturn
             _vi("File:Map of Saturn missions.webm")]                                                # a map

    def handler(req):
        assert req.url.params["gsrsearch"] == "Saturn filetype:video" and req.url.params["prop"] == "videoinfo"
        assert "derivatives" in req.url.params["viprop"] and req.headers["user-agent"].startswith("SleepOnFacts")
        return httpx.Response(200, json={"query": {"pages": pages}})
    with httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": footage.images.UA}) as c:
        got = footage.search_commons(c, "Saturn", ["saturn"])
    assert [g["title"] for g in got] == ["Video - Spinning Saturn - Cassini", "Saturn Lightning"]
    spin, light = got
    assert spin["url"].endswith("1080p.vp9.webm") and spin["fallback"][0].endswith("720p.vp9.webm")
    assert spin["probe"].endswith("360p.vp9.webm")
    assert (spin["source"], spin["artist"], spin["license"]) == ("commons", "NASA/JPL", "CC BY 2.0")
    assert spin["page"] == "https://commons.wikimedia.org/wiki/File:Video - Spinning Saturn - Cassini.webm"
    assert light["url"] == light["probe"] == "https://upload.wikimedia.org/File:Saturn Lightning.ogv.webm"
    from sof import captions
    assert captions.needs_credit(spin) and not captions.needs_credit(light)


# ---------------------------------------------------------------- calm check (pure)

def _calm(n, motion=0.1):
    return [100.0] * n, [motion * 10] * n, [10.0] * n


def test_calm_window_takes_the_longest_quiet_stretch_away_from_cuts_and_flashes():
    luma, diff, grad = _calm(160)                 # 20 s at 8 samples a second, slow motion everywhere
    diff[40] = 80.0                               # a cut at 5 s
    luma[120:122] = [240.0, 240.0]                # a flash at 15 s
    start, length = footage.calm_window(luma, diff, grad, need=6.0, keep=20.0)
    assert 5.5 < start < 6.5 and 8.0 < length < 10.0          # between the cut and the flash, edges kept clear
    assert footage.calm_window(luma, diff, grad, need=6.0, keep=7.0)[1] == 7.0
    assert footage.calm_window(luma, diff, grad, need=11.0, keep=20.0) is None      # no stretch long enough


def test_calm_window_rejects_busy_frozen_and_black_footage():
    assert footage.calm_window(*_calm(160, motion=0.9), need=6.0, keep=15.0) is None    # fast pan everywhere
    assert footage.calm_window(*_calm(160, motion=0.0), need=6.0, keep=15.0) is None    # a still / title card
    luma, diff, grad = _calm(160)
    luma[:] = [2.0] * 160
    assert footage.calm_window(luma, diff, grad, need=6.0, keep=15.0) is None           # black
    luma, diff, grad = _calm(160)
    diff[60:90] = [9.0] * 30                                                             # a busy stretch mid-clip
    start, length = footage.calm_window(luma, diff, grad, need=6.0, keep=15.0)
    assert start > 10.0 and length >= 6.0


def test_slow_for_fills_a_slot_without_looping():
    assert footage.slow_for(15.0, slot=21.5, slow=1.5, max_slow=2.0) == 1.5
    assert footage.slow_for(11.0, slot=21.5, slow=1.5, max_slow=2.0) == 2.0
    assert footage.slow_for(9.0, slot=21.5, slow=1.5, max_slow=2.0) is None


# ---------------------------------------------------------------- calm check + the whole stage (real ffmpeg)

def _pan(tmp_path, name, *, seconds, speed, size=(320, 180), flash_at=None, colour="planet"):
    """A slow pan across a synthetic picture (``speed`` canvas pixels a frame), optionally with a white flash."""
    from PIL import Image, ImageDraw
    src = tmp_path / f"{colour}.png"
    if not src.exists():
        im = Image.new("RGB", (1280, 720), (8, 8, 12))
        d = ImageDraw.Draw(im)
        for i in range(0, 1280, 40):
            d.ellipse((i, 200 + (i * 7) % 300, i + 30, 230 + (i * 7) % 300), fill=(200, 160 - i % 120, 90 + i % 150))
        im.save(src)
    vf = f"loop=loop=-1:size=1,fps=25,crop=640:360:x='min(630,n*{speed})':y=150,scale={size[0]}:{size[1]},format=yuv420p"
    if flash_at is not None:
        vf += f",drawbox=c=white@1:t=fill:enable='between(t,{flash_at},{flash_at + 0.12})'"
    out = tmp_path / name
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", vf, "-t", str(seconds), "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")
def test_frame_stats_find_the_calm_window_of_a_real_clip_with_a_cut_and_a_flash(tmp_path):
    a = _pan(tmp_path, "a.mp4", seconds=4, speed=1)
    b = _pan(tmp_path, "b.mp4", seconds=14, speed=1, flash_at=8.0, colour="other")
    (tmp_path / "l.txt").write_text(f"file '{a}'\nfile '{b}'\n")
    joined = tmp_path / "cut.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(tmp_path / "l.txt"), "-c", "copy",
                    str(joined)], check=True)
    start, length = footage.calm_window(*footage.frame_stats(str(joined)), need=5.0, keep=20.0)
    assert 4.0 < start < 5.5 and 5.0 <= length < 8.0           # after the cut at 4 s, before the flash at 12 s
    fast = _pan(tmp_path, "fast.mp4", seconds=10, speed=12)
    assert footage.calm_window(*footage.frame_stats(str(fast)), need=5.0, keep=20.0) is None


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")
def test_gather_footage_probes_checks_and_cuts_only_calm_approved_named_clips(tmp_path, monkeypatch):
    monkeypatch.setattr(footage, "WIKIMEDIA_GAP", 0.0)
    low = _pan(tmp_path, "low.mp4", seconds=9, speed=1)                      # the 180p rendition that is analysed
    high = _pan(tmp_path, "high~large.mp4", seconds=9, speed=1, size=(854, 480))   # the one that is shown
    busy = _pan(tmp_path, "busy.mp4", seconds=9, speed=14)
    vp9 = 'video/webm; codecs="vp9, opus"'

    def handler(req):
        u = str(req.url)
        if "commons.wikimedia.org" in u:
            q = req.url.params["gsrsearch"]
            if q.startswith("Titan"):
                return httpx.Response(200, json={"query": {"pages": [
                    _vi("File:Titan flyover.webm", h=480, w=854, dur=9, derivs=[
                        {"src": "https://upload.wikimedia.org/titan/360p.vp9.webm", "type": vp9, "height": 360},
                        {"src": str(high), "type": vp9, "height": 480}])]}})
            return httpx.Response(200, json={"query": {"pages": [
                _vi("File:Saturn busy.webm", h=480, w=854, dur=9, derivs=[
                    {"src": "https://upload.wikimedia.org/busy/360p.vp9.webm", "type": vp9, "height": 360},
                    {"src": str(high), "type": vp9, "height": 480}]),
                _vi("File:Saturn calm.webm", h=480, w=854, dur=9, derivs=[
                    {"src": "https://upload.wikimedia.org/calm/360p.vp9.webm", "type": vp9, "height": 360},
                    {"src": str(high), "type": vp9, "height": 480}]),
                _vi("File:Saturn talk.webm", h=480, w=854, dur=9, lic="Public domain", derivs=[
                    {"src": "https://upload.wikimedia.org/talk/360p.vp9.webm", "type": vp9, "height": 360},
                    {"src": str(high), "type": vp9, "height": 480}])]}})
        if "upload.wikimedia.org" in u:
            assert req.headers["user-agent"].startswith("SleepOnFacts")       # Wikimedia refuses anonymous clients
            return httpx.Response(200, content=(busy if "/busy/" in u else low).read_bytes())
        raise AssertionError(u)

    asked = []

    def classify(tiles, subject):
        asked.append((subject, len(tiles)))
        assert all(Path(t).exists() for t in tiles)
        return {t: ("reject" if i == 1 and subject == "Saturn" else "subject") for i, t in enumerate(tiles)}

    groups = [{"key": "titan", "subject": "Titan (Saturn)", "queries": ["Titan"], "terms": ["titan"], "nasa": False,
               "want": 2, "limit": 12},
              {"key": footage.SUBJECT, "subject": "Saturn", "queries": ["Saturn"], "terms": ["saturn"], "nasa": False,
               "want": 5, "limit": 12}]
    found, stats = footage.gather_footage(groups, str(tmp_path / "footage"), classify=classify, need=4.0, keep=6.0,
                                          slot=6.0, slow=1.5, max_slow=2.0, transport=httpx.MockTransport(handler))
    assert stats["candidates"] == 4 and stats["calm"] == 3 and stats["clips"] == 2      # busy: not calm; talk: rejected
    assert sorted(asked) == [("Saturn", 2), ("Titan (Saturn)", 1)]
    (titan,), (saturn,) = found["titan"], found[footage.SUBJECT]
    skip, seconds, slow = footage.window(saturn)
    assert skip == 0.0 and 4.0 <= seconds <= 6.0 and 1.5 <= slow <= 2.0 and seconds * slow >= 6.5
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=height:format=duration", "-of", "json", saturn],
                           capture_output=True, text=True, check=True).stdout
    info = json.loads(probe)
    assert info["streams"][0]["height"] == 480 and abs(float(info["format"]["duration"]) - seconds) < 0.3
    rows = footage.credits_by_path(str(tmp_path / "footage"))
    assert rows[saturn]["kind"] == "clip" and rows[saturn]["license"] == "CC BY 2.0" and rows[saturn]["source"] == "commons"
    assert rows[saturn]["page"].endswith("File:Saturn calm.webm") and rows[titan]["title"] == "Titan flyover"
    assert footage.kept(str(tmp_path / "footage" / footage.SUBJECT)) == [saturn]
    assert not list((tmp_path / "footage").rglob("*.probe.*"))                  # analysed renditions are deleted


def test_gather_footage_asks_one_sheet_at_a_time_and_cuts_the_subject_first(tmp_path, monkeypatch):
    """2026-11-21: vision stops once a group has its clips; the group with the most tiles (the subject) is checked
    and cut first, each group's clips right after its own check."""
    cands = {"titan": [{"id": f"t{i}", "source": "commons"} for i in range(3)],
             footage.SUBJECT: [{"id": f"s{i}", "source": "commons"} for i in range(30)]}
    monkeypatch.setattr(footage, "_candidates", lambda g, **kw: cands[g["key"]])
    monkeypatch.setattr(footage, "probe_clip", lambda c, cand, folder, **kw: {**cand, "skip": 1.0, "seconds": 12.0,
                                                                              "check": f"tile-{cand['id']}"})
    cut_order = []

    def fake_cut(cand, dst, **kw):
        cut_order.append(cand["id"])
        dst.write_bytes(b"x")
        return True
    monkeypatch.setattr(footage, "cut_window", fake_cut)
    asked = []

    def classify(tiles, subject):
        asked.append((subject, len(tiles)))
        return {t: "subject" for t in tiles}

    groups = [{"key": "titan", "subject": "Titan", "want": 2, "limit": 12},
              {"key": footage.SUBJECT, "subject": "Saturn", "want": 7, "limit": 80}]
    found, stats = footage.gather_footage(groups, str(tmp_path), classify=classify, need=11.0, keep=15.0, slot=21.5,
                                          slow=1.5, max_slow=2.0, workers=1)
    assert sorted(asked) == [("Saturn", 12), ("Titan", 3)]   # 14 calm (2 x want): the first sheet already gives 7
    assert cut_order == ["s0", "s1", "s2", "s3", "s4", "s5", "s6", "t0", "t1"]
    assert [Path(p).stem for p in found[footage.SUBJECT]] == [footage._stem({"id": f"s{i}"}) for i in range(7)]
    assert stats["clips"] == 9


def test_cut_window_bounds_all_attempts_and_waits_out_one_wikimedia_429(tmp_path, monkeypatch):
    monkeypatch.setattr(footage, "WIKIMEDIA_GAP", 0.0)
    monkeypatch.setattr(footage.time, "sleep", lambda s: None)
    calls = []

    def fake_run(cmd, **kw):
        url = cmd[cmd.index("-i") + 1]
        calls.append((url, kw["timeout"]))
        if len(calls) == 1:
            return subprocess.CompletedProcess(cmd, 1, "", "Error opening input files: Server returned 429 Too Many Requests")
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])
    monkeypatch.setattr(footage.subprocess, "run", fake_run)
    cand = {"id": "c", "url": "https://upload.wikimedia.org/a.webm", "fallback": ["https://upload.wikimedia.org/a.ogv"],
            "skip": 1.0, "seconds": 12.0}
    assert not footage.cut_window(cand, tmp_path / "c.mp4", timeout=100.0)
    assert [u for u, _ in calls] == [cand["url"], cand["url"], cand["fallback"][0]]     # same URL again after the 429
    assert all(t <= 100.0 for _, t in calls)


def test_wikimedia_requests_are_paced_across_callers(monkeypatch):
    waits = []
    monkeypatch.setattr(footage.time, "sleep", waits.append)
    monkeypatch.setattr(footage, "_pace_at", [0.0])
    footage._pace("https://images-assets.nasa.gov/x~large.mp4", 4)            # other hosts: no wait, no turn taken
    footage._pace("https://upload.wikimedia.org/a.webm", 4)                   # one ffmpeg cut: 4 turns
    footage._pace("https://upload.wikimedia.org/b.mov")
    assert len(waits) == 2 and waits[0] <= 0.0 and 7.9 <= waits[1] <= footage.WIKIMEDIA_GAP * 4


def test_probe_download_waits_out_one_429(tmp_path, monkeypatch):
    monkeypatch.setattr(footage, "WIKIMEDIA_GAP", 0.0)
    slept = []
    monkeypatch.setattr(footage.time, "sleep", slept.append)
    hits = []

    def handler(req):
        hits.append(1)
        if len(hits) == 1:
            return httpx.Response(429, headers={"retry-after": "11"})
        return httpx.Response(200, content=b"v" * 5000)
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        assert footage._get(c, "https://upload.wikimedia.org/t/360p.mov", tmp_path / "p.mov", max_bytes=10_000)
    assert len(hits) == 2 and 11.0 in slept


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")
def test_cut_window_gives_anamorphic_film_square_pixels(tmp_path):
    src = tmp_path / "film.mp4"                       # NASA ~orig film transfers: 720x486 at SAR 6:5 (16:9 on screen)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=720x486:r=25:d=4", "-vf", "setsar=6/5",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(src)], check=True)
    assert footage.cut_window({"id": "f", "url": str(src), "skip": 0.5, "seconds": 2.0}, tmp_path / "f.mp4")
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height,sample_aspect_ratio",
                                      "-of", "json", str(tmp_path / "f.mp4")], capture_output=True, text=True).stdout)
    st = info["streams"][0]
    assert (st["width"], st["height"]) == (864, 486) and st.get("sample_aspect_ratio", "1:1") in ("1:1", "0:1")


# ---------------------------------------------------------------- placement (pure)

def test_mix_puts_things_clips_on_their_slots_and_subject_clips_everywhere_else():
    n = 120
    slots = [f"p{k}.jpg" for k in range(n)]
    plan = [{"entity": "titan" if 10 <= k < 30 else "moon" if 30 <= k < 40 else None,
             "cue": "shot" if 60 <= k < 70 else None, "why": "section photo" if 80 <= k < 90 else "subject reel"}
            for k in range(n)]
    titan = ["t1.mp4", "t2.mp4", "t3.mp4"]
    mine = [f"s{i}.mp4" for i in range(16)]
    out, placed = footage.mix_into_slots(slots, plan, mine, entity_clips={"titan": titan, "moon": []},
                                         every=2, max_uses=3, gap=30)
    vids = [k for k, s in enumerate(out) if s.endswith(".mp4")]
    assert out[0] == "p0.jpg" and placed == len(vids)
    assert all(out[k] in titan for k in vids if 10 <= k < 30)              # a thing's slots show only its own clips
    assert all(out[k] not in titan for k in vids if not 10 <= k < 30)
    assert not [k for k in vids if 30 <= k < 40]                            # a thing without clips keeps its photos
    assert any(60 <= k < 70 for k in vids) and any(80 <= k < 90 for k in vids)   # passage and chapter slots get clips
    assert all(b - a >= 2 for a, b in zip(vids, vids[1:]))                  # never two footage slots in a row
    for c in titan + mine:
        ks = [k for k in vids if out[k] == c]
        assert len(ks) <= 3 and all(b - a > 30 for a, b in zip(ks, ks[1:]))
    assert placed >= 0.4 * n                                                # a real share, not 1 slot in 394
    assert footage.mix_into_slots(slots, plan, []) == (slots, 0)


def test_mix_keeps_photos_when_clips_run_out():
    slots = [f"p{k}.jpg" for k in range(40)]
    out, placed = footage.mix_into_slots(slots, [{} for _ in slots], ["a.mp4"], every=2, max_uses=2, gap=5)
    assert placed == 2 and [k for k, s in enumerate(out) if s == "a.mp4"] == [1, 7]


def test_window_reads_the_row_beside_the_clip(tmp_path):
    (tmp_path / "c.mp4").write_bytes(b"x")
    (tmp_path / "old.mp4").write_bytes(b"x")
    (tmp_path / "footage.json").write_text(json.dumps([{"file": "c.mp4", "skip": 0.0, "seconds": 12.5, "slow": 1.72},
                                                       {"file": "old.mp4", "seconds": 30}]))
    assert footage.window(str(tmp_path / "c.mp4")) == (0.0, 12.5, 1.72)
    assert footage.window(str(tmp_path / "old.mp4")) is None and footage.window(str(tmp_path / "x.mp4")) is None
