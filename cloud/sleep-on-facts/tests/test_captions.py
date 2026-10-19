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


def test_caption_cleans_commons_boilerplate_and_unprintable_names():
    boiler = "No machine-readable author provided. Jungle assumed (based on copyright claims)."
    assert captions.caption_text(_row(artist=boiler)) == "Photo: Jungle · CC BY-SA 4.0 · Wikimedia Commons"
    assert captions.caption_text(_row(artist="राम")) == "Photo: Unknown author · CC BY-SA 4.0 · Wikimedia Commons"
    assert captions.caption_text(_row(artist="山田太郎")) == "Photo: 山田太郎 · CC BY-SA 4.0 · Wikimedia Commons"


def test_caption_runs_switch_fonts_for_cjk_and_never_draw_boxes():
    assert render.caption_runs("Photo: 山田 Taro · CC BY 3.0", cjk=True) == [("Photo: ", False), ("山田", True),
                                                                            (" Taro · CC BY 3.0", False)]
    assert render.caption_runs("Photo: 山田 Taro", cjk=False) == [("Photo: ", False), (" Taro", False)]
    assert render.caption_runs("Photo: Σταῦρος · CC BY 2.0", cjk=True) == [("Photo: Σταῦρος · CC BY 2.0", False)]



def test_commons_talk_contribs_suffix_is_dropped():
    from sof import captions
    row = {"source": "commons", "license": "CC BY-SA 3.0",
           "artist": '<a href="//commons.wikimedia.org/wiki/User:Waugsberg">Waugsberg</a> (<a href="x">talk</a> · <a href="y">contribs</a>)'}
    assert captions.caption_text(row) == "Photo: Waugsberg · CC BY-SA 3.0 · Wikimedia Commons"
    row["artist"] = "Waugsberg ( talk · contribs"
    assert captions.caption_text(row) == "Photo: Waugsberg · CC BY-SA 3.0 · Wikimedia Commons"
    row["artist"] = "Jane Doe (photographer)"
    assert captions.caption_text(row) == "Photo: Jane Doe (photographer) · CC BY-SA 3.0 · Wikimedia Commons"
