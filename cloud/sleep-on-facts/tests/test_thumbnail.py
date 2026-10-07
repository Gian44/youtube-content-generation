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
