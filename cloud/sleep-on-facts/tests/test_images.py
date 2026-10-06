import httpx
from sof.images import collect_urls, download_all, GENERIC_QUERIES, PEXELS_WIDTH


def _transport(pexels_per_page=3, pixabay_hits=3, pexels_width=4000):
    def handler(req):
        if "pexels" in req.url.host:
            page, q = int(req.url.params.get("page", "1")), req.url.params.get("query")
            return httpx.Response(200, json={"photos": [
                {"id": f"{q}-{page}-{i}", "width": pexels_width, "height": pexels_width * 2 // 3,
                 "src": {"original": f"https://px/{q}/{page}/{i}.jpeg", "large2x": f"https://px/{q}/{page}/{i}.jpeg?w=940&dpr=2"},
                 "photographer": "A", "url": "https://pexels.com/p"} for i in range(pexels_per_page)]})
        if "pixabay" in req.url.host:
            q = req.url.params.get("q")
            return httpx.Response(200, json={"hits": [{"id": i, "largeImageURL": f"https://pb/{q}/{i}.jpg", "user": "B", "pageURL": "https://pixabay.com/p"} for i in range(pixabay_hits)]})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_collect_pads_with_generic_until_target():
    urls = collect_urls(["egypt", "nile"], target=20, pexels_key="k", pixabay_key="k", transport=_transport(), max_pages=1)
    assert len(urls) == 20 and len({u["url"] for u in urls}) == 20
    assert any(g.split()[0] in u["url"] for g in GENERIC_QUERIES for u in urls)


def test_collect_stops_at_target_and_prefers_pexels():
    urls = collect_urls(["egypt"], target=5, pexels_key="k", pixabay_key="k", transport=_transport(), max_pages=1)
    assert len(urls) == 5 and all(u["url"].startswith("https://px/") for u in urls)
    assert [u["url"] for u in urls[:3]] == [f"https://px/egypt/1/{i}.jpeg?auto=compress&cs=tinysrgb&w={PEXELS_WIDTH}" for i in range(3)]


def test_pexels_requests_2560px_rendition_and_skips_small_photos():
    small = collect_urls(["egypt"], target=5, pexels_key="k", pixabay_key="", transport=_transport(pexels_width=1200), max_pages=1)
    assert small == []  # 1200-px originals would be upscaled → rejected
    urls = collect_urls(["egypt"], target=1, pexels_key="k", pixabay_key="", transport=_transport(), max_pages=1)
    assert urls[0]["url"].endswith(f"w={PEXELS_WIDTH}")


def test_pixabay_is_only_a_top_up_when_pexels_runs_dry():
    urls = collect_urls(["egypt"], target=40, pexels_key="k", pixabay_key="k", transport=_transport(), max_pages=1)
    px = [u for u in urls if u["url"].startswith("https://px/")]
    pb = [u for u in urls if u["url"].startswith("https://pb/")]
    assert len(px) == 18 and len(pb) == 18 and urls[:18] == px  # (1 topic + 5 generics) × 3 per source
    assert len(urls) == 36


def test_download_filters_small_and_dedupes(tmp_path):
    def handler(req):
        return httpx.Response(200, content=(b"\xff\xd8" + b"0" * 60_000) if "big" in str(req.url) else b"tiny")
    urls = [{"url": "https://x/big1.jpg", "credit": "A"}, {"url": "https://x/tiny.jpg", "credit": "B"},
            {"url": "https://x/big2.jpg", "credit": "A"}]  # big2 has identical bytes -> dedupe by hash
    paths = download_all(urls, str(tmp_path), transport=httpx.MockTransport(handler), verify_image=lambda p: True)
    assert len(paths) == 1 and (tmp_path / "credits.json").exists()


def test_download_rejects_narrow_images(tmp_path):
    from io import BytesIO
    from PIL import Image

    def jpeg(w):
        buf = BytesIO(); Image.new("RGB", (w, w * 9 // 16), (10, 20, 30)).save(buf, "JPEG", quality=100)
        data = buf.getvalue()
        return data + b"\x00" * max(0, 60_000 - len(data))  # keep above MIN_BYTES; trailing bytes are ignored by decoders

    def handler(req):
        return httpx.Response(200, content=jpeg(2560 if "wide" in str(req.url) else 800))
    paths = download_all([{"url": "https://x/wide.jpg", "credit": "A"}, {"url": "https://x/narrow.jpg", "credit": "B"}], str(tmp_path),
                         transport=httpx.MockTransport(handler))
    assert [p.endswith("000.jpg") for p in paths] == [True]
