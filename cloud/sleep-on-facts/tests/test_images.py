import httpx
from sof.images import collect_urls, download_all, GENERIC_QUERIES


def _transport(pexels_per_page=3, pixabay_hits=3):
    def handler(req):
        if "pexels" in req.url.host:
            page, q = int(req.url.params.get("page", "1")), req.url.params.get("query")
            return httpx.Response(200, json={"photos": [{"id": f"{q}-{page}-{i}", "src": {"large2x": f"https://px/{q}/{page}/{i}.jpg"},
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


def test_collect_stops_at_target_without_generics():
    urls = collect_urls(["egypt"], target=5, pexels_key="k", pixabay_key="k", transport=_transport(), max_pages=1)
    assert len(urls) == 5 and all("egypt" in u["url"] for u in urls)


def test_download_filters_small_and_dedupes(tmp_path):
    def handler(req):
        return httpx.Response(200, content=(b"\xff\xd8" + b"0" * 60_000) if "big" in str(req.url) else b"tiny")
    urls = [{"url": "https://x/big1.jpg", "credit": "A"}, {"url": "https://x/tiny.jpg", "credit": "B"},
            {"url": "https://x/big2.jpg", "credit": "A"}]  # big2 has identical bytes -> dedupe by hash
    paths = download_all(urls, str(tmp_path), transport=httpx.MockTransport(handler), verify_image=lambda p: True)
    assert len(paths) == 1 and (tmp_path / "credits.json").exists()
