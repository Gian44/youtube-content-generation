import json

import httpx

from sof import captions, credits_site
from sof.config import Config
from sof.credits_site import Site


def _row(**kw):
    return {"file": "001.jpg", "source": "commons", "artist": "Prof Ranga Sai", "license": "CC BY-SA 4.0",
            "title": "Well in Hampi", "page": "https://commons.wikimedia.org/wiki/File:Well.jpg", **kw}


def test_licence_urls_and_page_keys():
    assert credits_site.licence_url("CC BY-SA 4.0") == "https://creativecommons.org/licenses/by-sa/4.0/"
    assert credits_site.licence_url("CC BY 2.0") == "https://creativecommons.org/licenses/by/2.0/"
    assert credits_site.licence_url("cc-by-sa-3.0") == "https://creativecommons.org/licenses/by-sa/3.0/"
    assert credits_site.licence_url("CC0") == "https://creativecommons.org/publicdomain/zero/1.0/"
    assert credits_site.licence_url("Attribution") == "" and credits_site.licence_url("") == ""
    assert credits_site.page_key("2026-11-07-archytas-of-tarentum") == "2026-11-07-archytas-of-tarentum"
    assert credits_site.page_key("2026-11-07-x", smoke=True) == "smoke-2026-11-07-x"
    assert credits_site.page_key("odd key/with spaces") == "odd-key-with-spaces" and credits_site.page_key("") == "video"
    assert credits_site.KEY_RE.match(credits_site.page_key("x" * 300))


def test_page_lists_credited_photos_with_links_and_counts_the_rest():
    rows = [_row(), _row(file="002.jpg", license="Public domain", title="Old map"),
            _row(file="003.jpg", source="nasa", license="NASA (public domain)", artist="NASA/JPL"),
            _row(file="004.jpg", source="pexels", license="Pexels License"), _row(file="005.jpg", source="pixabay", license="Pixabay Content License"),
            _row(file="006.jpg", artist="<a href='x'>Jane & Co</a> (talk · contribs)", license="CC BY 2.0", title="A <b>tagged</b> title",
                 page="https://commons.wikimedia.org/wiki/File:Tagged.jpg"),
            _row()]                                                    # a duplicate of the first
    html = credits_site.render_page(rows, title="Hampi & Co", date="2026-11-07", video_url="https://youtu.be/abc", shorts=3)
    assert html.startswith("<!doctype html>") and "<title>Photo credits — Hampi &amp; Co</title>" in html
    assert "Photos that require attribution (2)" in html
    assert ('<span class=t>Well in Hampi</span><br>Prof Ranga Sai · <a href="https://creativecommons.org/licenses/by-sa/4.0/">'
            'CC BY-SA 4.0</a> · Wikimedia Commons — <a href="https://commons.wikimedia.org/wiki/File:Well.jpg">file page</a>') in html
    assert "Jane &amp; Co · <a href=\"https://creativecommons.org/licenses/by/2.0/\">CC BY 2.0</a>" in html
    assert "<b>" not in html and "A &lt;b&gt;tagged&lt;/b&gt; title" in html and "talk" not in html
    assert "1 public-domain or CC0 photo via" in html and "1 image from the" in html and "NASA" in html
    assert "1 photo from <a href=\"https://www.pexels.com/license/\">Pexels</a>" in html and "Pixabay" in html
    assert '<a href="https://youtu.be/abc">Watch the video</a> (and its 3 Shorts)' in html
    assert "Changes" in html and "cropped, resized and dimmed" in html
    assert "github" not in html.lower()
    assert "None in this video." in credits_site.render_page([_row(license="CC0")], title="T")


def test_from_config_needs_both_secrets_and_the_switch():
    assert Site.from_config(Config()) is None
    assert Site.from_config(Config(cloudflare_api_token="t")) is None
    assert Site.from_config(Config(cloudflare_api_token="t", cloudflare_account_id="a", credits_site=False)) is None
    s = Site.from_config(Config(cloudflare_api_token="t", cloudflare_account_id="acc", credits_worker="w", credits_namespace="ns"))
    assert s and (s.token, s.account_id, s.worker, s.namespace_title) == ("t", "acc", "w", "ns")


class _Cloud:
    """A fake Cloudflare: namespaces, the account subdomain, the Worker (live once deployed) and KV values."""

    def __init__(self, *, namespaces=(), subdomain="gian", live=False, fail_put=False):
        self.namespaces = [{"id": f"ns{i}", "title": t} for i, t in enumerate(namespaces)]
        self.subdomain = subdomain
        self.live = live
        self.fail_put = fail_put
        self.calls: list[tuple[str, str]] = []
        self.kv: dict[str, str] = {}
        self.deployed: dict = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append((method, path))
        ok = lambda result: httpx.Response(200, json={"success": True, "result": result, "errors": [], "result_info": {"total_pages": 1}})
        if request.url.host.endswith("workers.dev"):
            assert "authorization" not in request.headers
            return httpx.Response(200, text=credits_site.LANDING) if self.live else httpx.Response(523, text="origin unreachable")
        assert request.headers["authorization"] == "Bearer tok" and path.startswith("/client/v4/accounts/acc/")
        p = path.removeprefix("/client/v4/accounts/acc")
        if p == "/storage/kv/namespaces" and method == "GET":
            return ok(self.namespaces)
        if p == "/storage/kv/namespaces" and method == "POST":
            self.namespaces.append({"id": "ns-new", "title": json.loads(request.content)["title"]})
            return ok(self.namespaces[-1])
        if p == "/workers/subdomain" and method == "GET":
            return ok({"subdomain": self.subdomain}) if self.subdomain else httpx.Response(404, json={"success": False, "errors": [{"message": "no subdomain"}]})
        if p == "/workers/subdomain" and method == "PUT":
            self.subdomain = json.loads(request.content)["subdomain"]
            return ok({"subdomain": self.subdomain})
        if p.startswith("/workers/scripts/") and p.endswith("/subdomain") and method == "POST":
            self.deployed["route"] = json.loads(request.content)
            self.live = True
            return ok({"enabled": True})
        if p.startswith("/workers/scripts/") and method == "PUT":
            body = request.content.decode()
            assert request.headers["content-type"].startswith("multipart/form-data")
            assert 'name="metadata"' in body and 'name="worker.js"' in body and "application/javascript+module" in body
            self.deployed["script"] = body
            return ok({"id": p.rsplit("/", 1)[1]})
        if "/storage/kv/namespaces/" in p and "/values/" in p and method == "PUT":
            if self.fail_put:
                return httpx.Response(500, json={"success": False, "errors": [{"message": "kv down"}]})
            assert request.headers["content-type"].startswith("multipart/form-data")
            body = request.content.decode()
            value = body.split('name="value"\r\n\r\n', 1)[1].split("\r\n--", 1)[0]
            assert 'name="metadata"' in body and "written" in body
            self.kv[p.rsplit("/values/", 1)[1]] = value
            return ok(None)
        raise AssertionError(f"unexpected {method} {path}")


def _site(cloud: _Cloud, **kw) -> Site:
    return Site(token="tok", account_id="acc", transport=httpx.MockTransport(cloud), **kw)


def test_first_publish_creates_the_namespace_deploys_the_worker_and_writes_the_page():
    cloud = _Cloud(namespaces=["STATE"])
    site = _site(cloud)
    url = site.publish("2026-11-07-whales", "<html>credits</html>", sleep=lambda s: None)
    assert url == "https://sleep-on-facts-credits.gian.workers.dev/2026-11-07-whales"
    assert [n["title"] for n in cloud.namespaces] == ["STATE", "sof-credits"]
    meta = json.loads(cloud.deployed["script"].split('filename="metadata"')[1].split("\r\n\r\n", 1)[1].split("\r\n", 1)[0])
    assert meta["main_module"] == "worker.js" and meta["bindings"] == [{"type": "kv_namespace", "name": "CREDITS", "namespace_id": "ns-new"}]
    assert "env.CREDITS.get(key)" in cloud.deployed["script"] and cloud.deployed["route"] == {"enabled": True, "previews_enabled": False}
    assert cloud.kv == {"2026-11-07-whales": "<html>credits</html>"}
    # a second page on a live site: no deploy, one KV write
    before = len(cloud.calls)
    site.publish("2026-11-08-moon", "<html>2</html>")
    assert [c for c in cloud.calls[before:] if c[0] in ("PUT", "POST")] == [("PUT", "/client/v4/accounts/acc/storage/kv/namespaces/ns-new/values/2026-11-08-moon")]


def test_publish_reuses_an_existing_namespace_and_registers_a_missing_subdomain():
    cloud = _Cloud(namespaces=["sof-credits"], subdomain="", live=True)
    site = _site(cloud)
    assert site.publish("k", "v") == "https://sleep-on-facts-credits.sleeponfacts.workers.dev/k"
    assert ("POST", "/client/v4/accounts/acc/storage/kv/namespaces") not in cloud.calls
    assert ("PUT", "/client/v4/accounts/acc/workers/subdomain") in cloud.calls and site.namespace_id() == "ns0"


def test_publish_credits_never_raises_and_reports(capsys):
    cloud = _Cloud(namespaces=["sof-credits"], live=True, fail_put=True)
    assert credits_site.publish_credits(_site(cloud), key="k", rows=[_row()], title="T") == ""
    out = capsys.readouterr().out
    assert "! credits page unavailable" in out and "kv down" in out
    assert credits_site.publish_credits(None, key="k", rows=[], title="T") == ""
    cloud.fail_put = False
    url = credits_site.publish_credits(_site(cloud), key="k", rows=[_row(), _row(file="2.jpg", license="CC0")], title="T & Co")
    out = capsys.readouterr().out
    assert url.endswith("/k") and "credits page: " in out and "1 credited of 2 photos" in out
    assert "T &amp; Co" in cloud.kv["k"] and "Prof Ranga Sai" in cloud.kv["k"]


def test_deploy_that_never_comes_up_fails_softly():
    cloud = _Cloud(namespaces=["sof-credits"])
    site = _site(cloud)
    real_live = site.is_live
    site.is_live = lambda: False                                   # 523 forever
    try:
        site.publish("k", "v", wait=10, sleep=lambda s: None)
    except credits_site.SiteError as exc:
        assert "still not answering" in str(exc)
    else:
        raise AssertionError("expected SiteError")
    site.is_live = real_live
    assert credits_site.KEY_RE.match("ok") and not credits_site.KEY_RE.match("bad key")
    try:
        site.put("bad key", "v")
    except credits_site.SiteError:
        pass
    else:
        raise AssertionError("bad keys must be refused")


def test_description_lines_point_at_the_page_or_name_the_authors():
    assert captions.description_line(None) == captions.DESCRIPTION_LINE and "on screen" in captions.DESCRIPTION_LINE
    one = captions.description_line("https://x.workers.dev/k")
    assert one.startswith(captions.SOURCES) and one.endswith("Photo credits (authors and licences): https://x.workers.dev/k")
    assert "on screen" not in one
    assert captions.description_line(["https://a/1", "", "https://a/2"]).endswith("Photo credits for each part: https://a/1 · https://a/2")
    rows = [_row(), _row(file="2.jpg", artist="Bo", license="CC BY 2.0"), _row(file="3.jpg", license="Public domain"),
            _row(file="4.jpg", source="pexels", license="Pexels License"), _row()]
    line = captions.inline_credits(rows)
    assert line == (f"{captions.SOURCES} Photo credits (Wikimedia Commons): Prof Ranga Sai (CC BY-SA 4.0); Bo (CC BY 2.0). "
                    f"{captions.LICENCES}")
    many = [_row(file=f"{i}.jpg", artist=f"Photographer Number {i}") for i in range(80)]
    short = captions.inline_credits(many, budget=400)
    assert len(short) <= 400 and short.endswith(f"; and others. {captions.LICENCES}") and "Photographer Number 0 (CC BY-SA 4.0)" in short
    assert "on screen" not in captions.inline_credits([_row(license="CC0")])
