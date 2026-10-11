from sof import captions, render


def _row(**kw):
    return {"file": "001.jpg", "source": "commons", "artist": "Prof Ranga Sai", "license": "CC BY-SA 4.0",
            "page": "https://commons.wikimedia.org/wiki/File:Well.jpg", **kw}


def test_only_attribution_licences_need_a_credit():
    assert captions.needs_credit(_row())
    assert captions.needs_credit(_row(license="CC BY 2.0")) and captions.needs_credit(_row(license="Attribution"))
    for lic in ("Public domain", "CC0", "No restrictions", "Copyrighted free use"):
        assert not captions.needs_credit(_row(license=lic)), lic
    assert not captions.needs_credit(_row(source="nasa", license="NASA (public domain)"))
    assert not captions.needs_credit(_row(source="pexels", license="Pexels License"))


def test_caption_text_is_short_and_clean():
    assert captions.caption_text(_row()) == "Photo: Prof Ranga Sai · CC BY-SA 4.0 · Wikimedia Commons"
    long = captions.caption_text(_row(artist="<a href='x'>Someone</a> " + "very long name " * 10))
    assert "<" not in long and long.startswith("Photo: Someone very long") and "…" in long and len(long) < 110
    assert captions.caption_text(_row(artist="")).startswith("Photo: Unknown author")


def test_captions_for_maps_reel_paths_by_file_name():
    rows = [_row(file="001.jpg"), _row(file="002.jpg", license="Public domain"), _row(file="003.jpg", source="pixabay")]
    got = captions.captions_for(["/w/images/001.jpg", "/w/images/002.jpg", "/w/images/003.jpg", "/w/images/004.jpg"], rows)
    assert got == {"/w/images/001.jpg": "Photo: Prof Ranga Sai · CC BY-SA 4.0 · Wikimedia Commons"}


def test_thumbnail_line():
    assert captions.thumbnail_line(_row()) == ("Thumbnail photo: Prof Ranga Sai, CC BY-SA 4.0, via Wikimedia Commons — "
                                               "https://commons.wikimedia.org/wiki/File:Well.jpg")
    assert captions.thumbnail_line(_row(license="Public domain")) == "" and captions.thumbnail_line(None) == ""


def test_caption_png_is_transparent_except_the_bottom_left_line(tmp_path):
    from PIL import Image
    out = render.caption_png("Photo: Someone · CC BY 2.0 · Wikimedia Commons", str(tmp_path / "c.png"), width=640, height=360)
    im = Image.open(out)
    assert im.mode == "RGBA" and im.size == (640, 360)
    box = im.getchannel("A").getbbox()
    assert box and box[0] < 40 and box[3] > 330 and box[1] > 300 and box[2] < 640   # one line, bottom left


def test_ken_burns_cmd_overlays_a_caption():
    plain = render.ken_burns_cmd("a.jpg", "o.mp4", dwell=2, crossfade=0.5, width=640, height=360, fps=24)
    cap = render.ken_burns_cmd("a.jpg", "o.mp4", dwell=2, crossfade=0.5, width=640, height=360, fps=24, caption="c.png")
    assert "-vf" in plain and "overlay" not in " ".join(plain)
    assert cap.count("-i") == 2 and "overlay=0:0" in " ".join(cap) and cap[-1] == "o.mp4"
