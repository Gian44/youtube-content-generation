from PIL import Image, ImageDraw

from sof import thumbnail


def _photo(path, mean, contrast=False, dark_left=False):
    im = Image.new("RGB", (640, 360), (mean, mean, mean))
    d = ImageDraw.Draw(im)
    if contrast:
        for x in range(0, 640, 40):
            d.rectangle((x, 0, x + 20, 360), fill=(min(255, mean + 70),) * 3)
    if dark_left:
        d.rectangle((0, 0, 280, 360), fill=(max(0, mean - 60),) * 3)
    im.save(path, quality=90)
    return str(path)


def test_pick_prefers_moody_contrasty_dark_left(tmp_path):
    black = _photo(tmp_path / "black.jpg", 8)
    washed = _photo(tmp_path / "washed.jpg", 220)
    flat = _photo(tmp_path / "flat.jpg", 95)
    good = _photo(tmp_path / "good.jpg", 95, contrast=True, dark_left=True)
    assert thumbnail.score_photo(black) == 0.0 and thumbnail.score_photo(washed) == 0.0
    assert thumbnail.score_photo(good) > thumbnail.score_photo(flat)
    assert thumbnail.pick_photo([black, washed, flat, good]) == good


def test_compose_is_1280x720_jpeg_and_fits_long_titles(tmp_path):
    src = _photo(tmp_path / "src.jpg", 90, contrast=True)
    out = thumbnail.compose(src, topic="The Deep Ocean Trenches of the Pacific", hours=3, out=str(tmp_path / "t.jpg"))
    with Image.open(out) as im:
        assert im.size == (1280, 720) and im.format == "JPEG"
    out1 = thumbnail.compose(src, topic="Whales", hours=1, out=str(tmp_path / "t1.jpg"))
    assert (tmp_path / "t1.jpg").stat().st_size > 20_000


def test_make_uses_subject_candidates_and_chooser(tmp_path):
    scenery = _photo(tmp_path / "scenery.jpg", 95, contrast=True, dark_left=True)     # best by heuristic
    subj_a = _photo(tmp_path / "a.jpg", 95)
    subj_b = _photo(tmp_path / "b.jpg", 90)
    seen = {}

    def chooser(paths):
        seen["paths"] = paths
        return subj_b

    out = thumbnail.make([scenery, subj_a, subj_b], topic="Saturn", hours=3, out=str(tmp_path / "t.jpg"),
                         candidates=[subj_a, subj_b], choose=chooser)
    assert seen["paths"] == [subj_a, subj_b] and out.endswith("t.jpg")
    # chooser failing → heuristic, still only among subject candidates
    thumbnail.make([scenery, subj_a], topic="Saturn", hours=3, out=str(tmp_path / "t2.jpg"), candidates=[subj_a],
                   choose=lambda p: None)


def test_square_photo_is_shown_whole_on_the_right(tmp_path):
    from PIL import ImageStat
    sq = tmp_path / "planet.jpg"
    im = Image.new("RGB", (1000, 1000), (0, 0, 0)); ImageDraw.Draw(im).ellipse((200, 200, 800, 800), fill=(200, 170, 120)); im.save(sq)
    out = thumbnail.compose(str(sq), topic="Jupiter", hours=3, out=str(tmp_path / "t.jpg"))
    with Image.open(out) as t:
        g = t.convert("L")
        right = ImageStat.Stat(g.crop((850, 300, 1000, 420))).mean[0]     # middle of the disk, right half of the frame
        assert right > 60                                                 # the whole disk sits on the right, not cropped away


def test_hook_line_replaces_the_tagline_and_is_cleaned(tmp_path):
    assert thumbnail.clean_thumb_text("drifting away") == "DRIFTING AWAY"
    assert thumbnail.clean_thumb_text("SHOCKING SECRET") == "" and thumbnail.clean_thumb_text("WHY SO BIG?") == ""
    assert thumbnail.clean_thumb_text("THE SCARIEST PLANET") == ""                 # fear words are banned too
    assert thumbnail.clean_thumb_text("A HOOK FAR TOO LONG TO READ ON A TILE") == ""
    assert thumbnail.clean_thumb_text("saturn", "Saturn") == ""                    # never just the topic again
    src = _photo(tmp_path / "src.jpg", 40, contrast=True)                        # a dark photo
    plain = thumbnail.compose(src, topic="Saturn", hours=3, out=str(tmp_path / "plain.jpg"))
    hooked = thumbnail.compose(src, topic="Saturn", hours=3, out=str(tmp_path / "hook.jpg"), hook="RINGS OF ICE")
    with Image.open(hooked) as im:
        assert im.size == (1280, 720)
    with Image.open(plain) as a, Image.open(hooked) as b:
        assert a.tobytes() != b.tobytes()


def test_dark_photos_are_lifted(tmp_path):
    from PIL import ImageStat
    src = _photo(tmp_path / "dark.jpg", 40)
    with Image.open(src) as im:
        before = ImageStat.Stat(im.convert("L")).mean[0]
        after = ImageStat.Stat(thumbnail._cool(im.convert("RGB")).convert("L")).mean[0]
    assert after > before * 1.15


def test_marathon_thumbnail_has_one_panel_per_part(tmp_path):
    srcs = [_photo(tmp_path / f"p{i}.jpg", 60 + 40 * i, contrast=True) for i in range(3)]
    out = thumbnail.compose_marathon(srcs, label="Space Facts to fall asleep to", names=["Saturn", "The Moon", "Jupiter"],
                                     hours=9, out=str(tmp_path / "m.jpg"))
    with Image.open(out) as im:
        assert im.size == (1280, 720)


def test_make_drops_picks_the_confirm_step_rejects(tmp_path):
    a, b, c = (_photo(tmp_path / f"{n}.jpg", 90, contrast=True) for n in "abc")
    seen = []

    def choose(paths):
        seen.append(list(paths))
        return paths[0]
    out = thumbnail.make([a, b, c], topic="Honey Bees", hours=3, out=str(tmp_path / "t.jpg"), candidates=[a, b, c],
                         choose=choose, confirm=lambda p: p != a, hook="WINTER STORES")
    assert thumbnail.source_of(out) == b and seen == [[a, b, c], [b, c]]
    out = thumbnail.make([a, b], topic="X", hours=3, out=str(tmp_path / "u.jpg"), candidates=[a, b],
                         choose=lambda ps: ps[0], confirm=lambda p: None)          # check unavailable → keep the pick
    assert thumbnail.source_of(out) == a
