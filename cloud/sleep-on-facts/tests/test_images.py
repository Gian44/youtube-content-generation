import json
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


# ---------------------------------------------------------------- subject names & free sources

from sof import images as I


def test_name_terms_drop_generic_words_and_match_whole_words():
    terms = I.name_terms("Saturn", "Saturn (planet)", ["saturn", "planet", "rings", "ringed", "cassini", "titan"])
    assert terms == ["saturn", "cassini", "titan"]
    assert not I.mentions("Detailed image of Uranus showcasing its rings", terms)
    assert I.mentions("A view of Saturn's rings", terms) and not I.mentions("Titanic wreck", terms)
    indus = I.name_terms("Indus Valley Civilization", "Indus Valley Civilisation", ["indus", "mohenjo-daro", "archaeological"])
    assert "archaeological" not in indus and "mohenjodaro" in indus
    assert not I.mentions("industrial textures of a brick wall", indus)        # 'indus' ⊄ 'industrial'
    assert I.mentions("Dice Mohenjodaro EAMd25", indus) and I.mentions("Harappan beads - Mohenjo-daro.", indus)
    assert "octopus" in I.name_terms("Octopuses", "Octopus")


def test_license_filter():
    assert all(I.license_ok(x) for x in ["CC BY-SA 4.0", "CC BY 2.0", "Public domain", "CC0", "Attribution"])
    assert not any(I.license_ok(x) for x in ["GFDL", "CC BY-NC 2.0", "CC BY-ND 3.0", ""])
    assert not I.license_ok("CC BY-SA 4.0", nonfree="true")


def _ii(title, *, mime="image/jpeg", w=4000, h=3000, lic="CC BY-SA 4.0", artist="<a href='x'>Jane</a>"):
    return {"title": title, "imageinfo": [{"mime": mime, "width": w, "height": h, "thumburl": f"https://up/{title}",
            "descriptionurl": f"https://commons/{title}", "extmetadata": {"LicenseShortName": {"value": lic},
            "Artist": {"value": artist}, "ImageDescription": {"value": "desc"}}}]}


def test_commons_candidate_filters():
    ok = I.commons_candidate(_ii("File:Mohenjo-daro.jpg"), query="q")
    assert ok["source"] == "commons" and ok["artist"] == "Jane" and ok["license"] == "CC BY-SA 4.0"
    assert I.commons_candidate(_ii("File:Map of sites.jpg"), query="q") is None                 # maps
    assert I.commons_candidate(_ii("File:Logo.svg", mime="image/svg+xml"), query="q") is None   # vector art
    assert I.commons_candidate(_ii("File:Small.jpg", w=900, h=600), query="q") is None           # too small
    assert I.commons_candidate(_ii("File:NC.jpg", lic="CC BY-NC 2.0"), query="q") is None        # not free
    assert I.commons_candidate(_ii("File:Statue.jpg", w=1945, h=3172), query="q") is not None    # portrait, shown whole


def _free_transport():
    def handler(req):
        host, params = req.url.host, req.url.params
        if host == "en.wikipedia.org" and params.get("prop") == "images":
            return httpx.Response(200, json={"query": {"pages": [{"title": "Saturn", "images": [
                {"title": "File:Saturn eclipse.jpg"}, {"title": "File:Saturn diagram.svg"}]}]}})
        if host == "en.wikipedia.org" and params.get("prop") == "pageprops":
            return httpx.Response(200, json={"query": {"pages": [{"pageprops": {"wikibase_item": "Q193"}}]}})
        if host == "www.wikidata.org":
            return httpx.Response(200, json={"claims": {"P373": [{"mainsnak": {"datavalue": {"value": "Saturn (planet)"}}}]}})
        if host == "commons.wikimedia.org" and params.get("titles"):
            return httpx.Response(200, json={"query": {"pages": [_ii(t) if t.endswith(".jpg") else _ii(t, mime="image/svg+xml")
                                                                 for t in params["titles"].split("|")]}})
        if host == "commons.wikimedia.org" and params.get("generator") == "categorymembers":
            return httpx.Response(200, json={"query": {"pages": [_ii(f"File:Cat {i}.jpg") for i in range(3)]}})
        if host == "commons.wikimedia.org" and params.get("list") == "categorymembers":
            return httpx.Response(200, json={"query": {"categorymembers": []}})
        if host == "images-api.nasa.gov":
            items = [{"data": [{"nasa_id": "PIA1", "title": "Saturn in color", "description": "", "secondary_creator": "NASA/JPL"}]},
                     {"data": [{"nasa_id": "S1", "title": "Pluto", "description": "not the subject"}]},
                     {"data": [{"nasa_id": "C1", "title": "Saturn art", "description": "© Someone"}]}]
            return httpx.Response(200, json={"collection": {"items": items if params.get("page") == "1" else []}})
        if "pexels" in host:
            return httpx.Response(200, json={"photos": [{"id": 1, "width": 4000, "height": 2600, "alt": "Saturn rings",
                                                         "src": {"original": "https://px/1.jpeg"}, "photographer": "A", "url": "u"}]})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_gather_puts_article_then_nasa_then_category_then_stock():
    terms = I.name_terms("Saturn", "Saturn")
    urls = I.gather_urls(topic="Saturn", wiki_title="Saturn", terms=terms, queries=["saturn"], setting_queries=[],
                         target=10, pexels_key="k", pixabay_key="", use_nasa=True, transport=_free_transport())
    assert [u["source"] for u in urls] == ["commons", "nasa", "commons", "commons", "commons", "pexels"]
    assert urls[0]["url"] == "https://up/File:Saturn eclipse.jpg"                 # article photo first, SVG skipped
    assert urls[1]["url"].endswith("PIA1/PIA1~large.jpg") and urls[1]["fallback"][0].endswith("~orig.jpg")
    assert not any("S1" in u["url"] or "C1" in u["url"] for u in urls)          # off-subject and © items dropped
    assert I.is_space_topic("", "Saturn is the sixth planet ... its moons orbit") and not I.is_space_topic("nature & animals")


def test_download_uses_fallback_url(tmp_path):
    def handler(req):
        if "~large" in str(req.url):
            return httpx.Response(403)
        return httpx.Response(200, content=b"\xff\xd8" + b"1" * 30_000)
    urls = [{"url": "https://n/a~large.jpg", "fallback": ["https://n/a~orig.jpg"], "credit": "NASA", "source": "nasa"}]
    paths = I.download_all(urls, str(tmp_path), transport=httpx.MockTransport(handler), verify_image=lambda p: True)
    assert len(paths) == 1
    credits = json.loads((tmp_path / "credits.json").read_text())
    assert credits[0]["source"] == "nasa" and "fallback" not in credits[0]


def _imgs(tmp_path, n):
    from PIL import Image
    out = []
    for i in range(n):
        p = tmp_path / f"{i}.jpg"; Image.new("RGB", (64, 36), (i * 20, 90, 120)).save(p); out.append(str(p))
    return out


def test_classify_labels_and_reel_selection(tmp_path):
    paths = _imgs(tmp_path, 4)
    t = httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content":
        '{"subject": [1, 3], "setting": [2], "reject": [4]}'}}]}))
    labels = I.classify(paths, subject="Saturn", api_key="k", transport=t)
    assert [labels[p] for p in paths] == ["subject", "setting", "subject", "reject"]
    assert I.select_reel(paths, labels, target=3, floor=1, setting_share=0.34) == [paths[0], paths[2], paths[1]]
    assert I.select_reel(paths, labels, target=3, floor=1, setting_share=0.0) == [paths[0], paths[2]]
    assert I.select_reel(paths, {p: "reject" for p in paths}, target=3, floor=2) == paths[:2]     # floor protects the video
    down = I.classify(paths, subject="Saturn", api_key="k", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert set(down.values()) == {"unknown"}


def test_pick_iconic(tmp_path):
    paths = _imgs(tmp_path, 3)
    t = httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": '{"best": 2}'}}]}))
    assert I.pick_iconic(paths, subject="Saturn", api_key="k", transport=t) == paths[1]
    assert I.pick_iconic(paths, subject="Saturn", api_key="k", transport=httpx.MockTransport(lambda r: httpx.Response(500))) is None
