from sof import credits


def test_credits_page_groups_sources_and_escapes(tmp_path):
    items = [
        {"source": "commons", "desc": "Mohenjo-daro.jpg The Great Bath", "artist": "Saqib <Qayyum>", "license": "CC BY-SA 3.0",
         "page": "https://commons.wikimedia.org/wiki/File:Mohenjo-daro.jpg"},
        {"source": "nasa", "desc": "Saturn in color", "artist": "NASA/JPL", "license": "NASA (public domain)", "page": "https://images.nasa.gov/details/PIA1"},
        {"source": "pexels", "desc": "rings", "artist": "A", "license": "Pexels License", "page": "https://pexels.com/p"},
    ]
    out = credits.write_page(str(tmp_path / "credits" / "2026-10-12-x.html"), title="3 Hours | X", video_url="https://youtu.be/v", items=items)
    html = open(out, encoding="utf-8").read()
    assert "Wikimedia Commons (1)" in html and "NASA Image and Video Library (1)" in html and "Pexels (1)" in html
    assert "Saqib &lt;Qayyum&gt;" in html and "CC BY-SA 3.0" in html and ">Mohenjo-daro<" in html
    assert "https://youtu.be/v" in html and "../_style.css" in html and "cropping" in html
