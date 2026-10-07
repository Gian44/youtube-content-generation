import httpx
from sof.images import collect_urls, download_all, verify_relevance, PEXELS_WIDTH


def _transport(pexels_per_page=3, pixabay_hits=3, pexels_width=4000):
    def handler(req):
        if "pexels" in req.url.host:
            page, q = int(req.url.params.get("page", "1")), req.url.params.get("query")
            return httpx.Response(200, json={"photos": [
                {"id": f"{q}-{page}-{i}", "width": pexels_width, "height": pexels_width * 2 // 3,
                 "src": {"original": f"https://px/{q}/{page}/{i}.jpeg", "large2x": f"https://px/{q}/{page}/{i}.jpeg?w=940&dpr=2"},
                 "alt": f"A photo of {q} number {i}" if i % 3 else "An aquarium fish swimming",   # every third result is off-topic
                 "photographer": "A", "url": "https://pexels.com/p"} for i in range(pexels_per_page)]})
        if "pixabay" in req.url.host:
            q = req.url.params.get("q")
            return httpx.Response(200, json={"hits": [{"id": i, "largeImageURL": f"https://pb/{q}/{i}.jpg", "tags": f"{q}, sea, water", "user": "B", "pageURL": "https://pixabay.com/p"} for i in range(pixabay_hits)]})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_collect_prefers_subject_shots_and_caps_scenery():
    urls = collect_urls(["octopus underwater", "octopus reef"], target=20, pexels_key="k", pixabay_key="k", transport=_transport(),
                        max_pages=3, keywords=["octopus"], setting_queries=["coral reef"])
    tiers = [u["tier"] for u in urls]
    assert len(urls) == 20 and len({u["url"] for u in urls}) == 20
    assert tiers.count("setting") <= 6                                   # ≤ 30 % scenery
    assert all("fish" not in u["desc"] for u in urls if u["tier"] == "subject")   # caption gate held
    assert not any(u["tier"] == "generic" for u in urls)                 # no generic padding above the floor


def test_collect_uses_generic_only_to_reach_the_floor():
    urls = collect_urls(["egypt"], target=40, pexels_key="k", pixabay_key="", transport=_transport(pexels_per_page=2),
                        max_pages=1, keywords=["egypt"], generic_floor=5)
    assert len(urls) == 5 and sum(u["tier"] == "generic" for u in urls) >= 1
    assert all(u["tier"] != "generic" for u in urls[:1])                 # on-topic first


def test_collect_without_keywords_takes_everything_pexels_first_then_pixabay():
    urls = collect_urls(["egypt"], target=5, pexels_key="k", pixabay_key="k", transport=_transport(), max_pages=1)
    assert len(urls) == 5 and all(u["tier"] == "subject" for u in urls)
    assert [u["url"] for u in urls[:3]] == [f"https://px/egypt/1/{i}.jpeg?auto=compress&cs=tinysrgb&w={PEXELS_WIDTH}" for i in range(3)]
    assert all(u["url"].startswith("https://pb/") for u in urls[3:])


def test_pixabay_is_a_top_up_after_pexels():
    urls = collect_urls(["egypt"], target=40, pexels_key="k", pixabay_key="k", transport=_transport(), max_pages=1, keywords=["egypt"])
    subject = [u for u in urls if u["tier"] == "subject"]
    px = [u for u in subject if u["url"].startswith("https://px/")]
    pb = [u for u in subject if u["url"].startswith("https://pb/")]
    assert px and pb and subject[:len(px)] == px        # among subject shots, Pexels comes first


def test_pexels_requests_2560px_rendition_and_skips_small_photos():
    small = collect_urls(["egypt"], target=5, pexels_key="k", pixabay_key="", transport=_transport(pexels_width=1200), max_pages=1)
    assert small == []  # 1200-px originals would be upscaled → rejected
    urls = collect_urls(["egypt"], target=1, pexels_key="k", pixabay_key="", transport=_transport(), max_pages=1)
    assert urls[0]["url"].endswith(f"w={PEXELS_WIDTH}")


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


def test_download_stops_at_want_and_survives_cdn_errors(tmp_path):
    def handler(req):
        if "bad" in str(req.url):
            return httpx.Response(522)
        return httpx.Response(200, content=(b"\xff\xd8" + str(req.url).encode() + b"0" * 60_000))
    urls = [{"url": f"https://x/{'bad' if i % 3 == 0 else 'ok'}{i}.jpg", "credit": "A"} for i in range(12)]
    paths = download_all(urls, str(tmp_path), transport=httpx.MockTransport(handler), verify_image=lambda p: True, want=5)
    assert len(paths) == 5


def test_vision_gate_drops_rejects_and_fails_open(tmp_path):
    from PIL import Image
    paths = []
    for i in range(3):
        p = tmp_path / f"{i}.jpg"; Image.new("RGB", (64, 36), (i * 40, 90, 120)).save(p); paths.append(str(p))
    ok = httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": '{"keep": [1, 3]}'}}]}))
    assert verify_relevance(paths, subject="Octopuses", api_key="k", transport=ok) == [paths[0], paths[2]]
    boom = httpx.MockTransport(lambda r: httpx.Response(500))
    assert verify_relevance(paths, subject="Octopuses", api_key="k", transport=boom) == paths          # fail open
    strict = httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": '{"keep": []}'}}]}))
    assert verify_relevance(paths, subject="Octopuses", api_key="k", transport=strict, keep_min=2) == paths   # floor protects the video
    assert verify_relevance(paths, subject="Octopuses", api_key="", transport=strict) == paths            # no key → skip
