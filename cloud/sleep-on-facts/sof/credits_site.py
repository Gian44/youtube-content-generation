"""Per-video photo-credits pages on Cloudflare (Workers KV behind a tiny Worker).

Why (Gian, 2026-11-07): the CC BY / CC BY-SA photos used to carry their author on screen, because the
old credits page lived on GitHub Pages and its link gave away the repo (2026-10-11). He wants a clean
frame. Attribution cannot simply be dropped, but the CC licences allow it "in any reasonable manner
based on the medium" and explicitly accept a link to a page that holds it — so every video now gets
its own page here, and the description carries one line with the link. Nothing on it points at the code.

How: one KV namespace (``credits_namespace``) holds ``<run id> → HTML``; one Worker (``credits_worker``)
serves ``https://<worker>.<account subdomain>.workers.dev/<run id>`` from it. The daily run only needs
the Cloudflare REST API (an API token with Workers Scripts + Workers KV edit, and the account id); the
Worker is deployed by :func:`Site.ensure` when the page root does not answer, so the first run — or
``python -m sof.credits_site deploy`` — sets everything up. Every failure here is reported, not raised:
the pipeline falls back to listing the credits in the description.
"""

from __future__ import annotations

import html
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from sof import captions

log = logging.getLogger("sof.credits_site")

API = "https://api.cloudflare.com/client/v4"
LANDING = "Sleep On Facts — photo credits.\nEach video's description links to its own page here.\n"
KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")

# The whole site. ``LANDING`` is also the liveness probe: the root answers from code, never from KV,
# so a fresh deployment can be checked without waiting for KV's eventual consistency.
WORKER_JS = """\
const LANDING = %s;
const text = (body, status = 200) => new Response(body, {status, headers: {"content-type": "text/plain; charset=utf-8", "cache-control": "no-store"}});
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const key = decodeURIComponent(url.pathname).replace(/^\\/+|\\/+$/g, "");
    if (!key) return text(LANDING);
    if (key === "robots.txt") return text("User-agent: *\\nAllow: /\\n");
    if (!/^[A-Za-z0-9._-]{1,120}$/.test(key)) return text("Not found.", 404);
    const page = await env.CREDITS.get(key);
    if (page === null) return text("No credits page at this address.", 404);
    return new Response(page, {headers: {"content-type": "text/html; charset=utf-8", "cache-control": "public, max-age=300"}});
  },
};
""" % json.dumps(LANDING)

LICENCE_URLS = {
    "cc0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "public domain": "https://creativecommons.org/publicdomain/mark/1.0/",
}


def licence_url(lic: str) -> str:
    """The deed for a Commons licence short name ('CC BY-SA 4.0' → creativecommons.org/licenses/by-sa/4.0/)."""
    s = str(lic or "").strip()
    m = re.match(r"^CC[ -]?(BY(?:[ -]SA)?)[ -]?(\d\.\d)$", s, re.I)
    if m:
        return f"https://creativecommons.org/licenses/{m.group(1).lower().replace(' ', '-')}/{m.group(2)}/"
    for k, v in LICENCE_URLS.items():
        if k in s.lower():
            return v
    return ""


def page_key(run_id: str, *, smoke: bool = False) -> str:
    key = re.sub(r"[^A-Za-z0-9._-]+", "-", str(run_id or "")).strip("-.")[:100] or "video"
    return f"smoke-{key}" if smoke else key


# ------------------------------------------------------------------ the page

def _esc(s: object) -> str:
    return html.escape(str(s or ""), quote=True)


def _dedupe(rows: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in rows:
        k = (str(r.get("page") or r.get("url") or r.get("file") or ""), str(r.get("artist") or ""), str(r.get("license") or ""))
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def render_page(rows: list[dict], *, title: str, date: str = "", video_url: str = "", shorts: int = 0) -> str:
    """One self-contained HTML page: the credited photos first (author, licence, Commons file page), then
    the sources that need no credit, then what was changed (the CC licences ask for that too)."""
    rows = _dedupe([r for r in rows if isinstance(r, dict)])
    credited = [r for r in rows if captions.needs_credit(r)]
    commons_free = [r for r in rows if r.get("source") == "commons" and r not in credited]
    counts = {s: sum(1 for r in rows if r.get("source") == s) for s in ("nasa", "pexels", "pixabay")}

    def item(r: dict) -> str:
        name = _esc(str(r.get("title") or r.get("file") or "Photo").strip()[:120])
        page = str(r.get("page") or "").strip()
        author = _esc(captions._short(captions._clean(r.get("artist")), 120) or "Unknown author")
        lic = str(r.get("license") or "").strip()
        lic_html = f'<a href="{_esc(licence_url(lic))}">{_esc(lic)}</a>' if licence_url(lic) else _esc(lic)
        link = f' — <a href="{_esc(page)}">file page</a>' if page.startswith("http") else ""
        return f"<li><span class=t>{name}</span><br>{author} · {lic_html} · Wikimedia Commons{link}</li>"

    parts = [f"<h1>Photo credits</h1>", f"<p class=sub>{_esc(title)}" + (f" · {_esc(date)}" if date else "") + "</p>"]
    if video_url:
        parts.append(f'<p><a href="{_esc(video_url)}">Watch the video</a>' + (f" (and its {shorts} Shorts)" if shorts else "") + "</p>")
    if credited:
        parts.append(f"<h2>Photos that require attribution ({len(credited)})</h2><ol>" + "".join(item(r) for r in credited) + "</ol>")
    else:
        parts.append("<h2>Photos that require attribution</h2><p>None in this video.</p>")
    rest = []
    if commons_free:
        rest.append(f"{len(commons_free)} public-domain or CC0 photo{'s' if len(commons_free) != 1 else ''} via "
                    '<a href="https://commons.wikimedia.org/">Wikimedia Commons</a>')
    if counts["nasa"]:
        rest.append(f'{counts["nasa"]} image{"s" if counts["nasa"] != 1 else ""} from the '
                    '<a href="https://images.nasa.gov/">NASA Image and Video Library</a> (public domain)')
    if counts["pexels"]:
        rest.append(f'{counts["pexels"]} photo{"s" if counts["pexels"] != 1 else ""} from <a href="https://www.pexels.com/license/">Pexels</a>')
    if counts["pixabay"]:
        rest.append(f'{counts["pixabay"]} photo{"s" if counts["pixabay"] != 1 else ""} from '
                    '<a href="https://pixabay.com/service/license-summary/">Pixabay</a>')
    rest.append('Footage clips, where used, come from <a href="https://www.pexels.com/license/">Pexels</a> and '
                '<a href="https://pixabay.com/service/license-summary/">Pixabay</a> under their own licences')
    parts.append("<h2>Also used</h2><ul>" + "".join(f"<li>{x}.</li>" for x in rest) + "</ul>")
    parts.append("<h2>Changes</h2><p>Photos are shown cropped, resized and dimmed, with a slow zoom and a blurred "
                 "copy of the photo as background; some carry narration captions. The licence links above lead to the "
                 "full licence texts.</p>")
    parts.append("<p class=foot>Sleep On Facts</p>")
    css = ("body{margin:0;padding:24px 16px 48px;background:#0f1117;color:#e6e6e6;font:16px/1.5 system-ui,-apple-system,"
           "Segoe UI,Roboto,sans-serif;max-width:720px;margin:0 auto}h1{font-size:28px;margin:0 0 4px}h2{font-size:18px;"
           "margin:28px 0 8px;color:#fff}.sub{color:#9aa0a6;margin:0 0 16px}ol,ul{padding-left:22px}li{margin:0 0 10px}"
           ".t{color:#fff}a{color:#8ab4f8}.foot{margin-top:40px;color:#9aa0a6;font-size:14px}")
    return ("<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content=\"width=device-width,"
            f"initial-scale=1\"><meta name=robots content=\"noindex\"><title>Photo credits — {_esc(title)}</title>"
            f"<style>{css}</style></head><body>" + "".join(parts) + "</body></html>")


# ------------------------------------------------------------------ Cloudflare

class SiteError(RuntimeError):
    pass


@dataclass
class Site:
    token: str
    account_id: str
    worker: str = "sleep-on-facts-credits"
    namespace_title: str = "sof-credits"
    timeout: float = 30.0
    transport: httpx.BaseTransport | None = field(default=None, repr=False)     # tests
    _client: httpx.Client | None = field(default=None, repr=False)
    _public: httpx.Client | None = field(default=None, repr=False)
    _namespace_id: str = field(default="", repr=False)
    _subdomain: str = field(default="", repr=False)

    @classmethod
    def from_config(cls, cfg) -> "Site | None":
        """None when the site is switched off or the Cloudflare secrets are missing."""
        if not getattr(cfg, "credits_site", False):
            return None
        token, account = str(getattr(cfg, "cloudflare_api_token", "") or ""), str(getattr(cfg, "cloudflare_account_id", "") or "")
        if not (token and account):
            return None
        return cls(token=token, account_id=account, worker=getattr(cfg, "credits_worker", cls.worker),
                   namespace_title=getattr(cfg, "credits_namespace", cls.namespace_title))

    # -- http
    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout, transport=self.transport,
                                        headers={"Authorization": f"Bearer {self.token}"})
        return self._client

    @property
    def public(self) -> httpx.Client:
        """No token: what a viewer's browser sees."""
        if self._public is None:
            self._public = httpx.Client(timeout=15.0, transport=self.transport, follow_redirects=True)
        return self._public

    def _api(self, method: str, path: str, **kw) -> dict:
        r = self.client.request(method, f"{API}/accounts/{self.account_id}{path}", **kw)
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400 or data.get("success") is False:
            errs = "; ".join(str(e.get("message") or e) for e in (data.get("errors") or [])) or r.text[:200]
            raise SiteError(f"{method} {path}: HTTP {r.status_code} {errs}")
        return data

    # -- namespace / subdomain
    def namespace_id(self) -> str:
        if self._namespace_id:
            return self._namespace_id
        page = 1
        while True:
            data = self._api("GET", "/storage/kv/namespaces", params={"per_page": 100, "page": page})
            for ns in data.get("result") or []:
                if ns.get("title") == self.namespace_title:
                    self._namespace_id = str(ns["id"])
                    return self._namespace_id
            info = data.get("result_info") or {}
            if page >= int(info.get("total_pages") or 1):
                break
            page += 1
        data = self._api("POST", "/storage/kv/namespaces", json={"title": self.namespace_title})
        self._namespace_id = str((data.get("result") or {})["id"])
        log.info("created KV namespace %s (%s)", self.namespace_title, self._namespace_id)
        return self._namespace_id

    def subdomain(self) -> str:
        """The account's workers.dev subdomain; an account without one gets ``sleeponfacts`` (or fails with
        the dashboard path when that name is taken)."""
        if not self._subdomain:
            try:
                data = self._api("GET", "/workers/subdomain")
            except SiteError:                 # 404 on an account that never registered one
                data = {}
            self._subdomain = str((data.get("result") or {}).get("subdomain") or "")
            if not self._subdomain:
                try:
                    data = self._api("PUT", "/workers/subdomain", json={"subdomain": "sleeponfacts"})
                    self._subdomain = str((data.get("result") or {}).get("subdomain") or "")
                except SiteError as exc:
                    raise SiteError("the Cloudflare account has no workers.dev subdomain yet and 'sleeponfacts' could not "
                                    f"be registered ({exc}) — set one once in the dashboard (Workers & Pages → Your subdomain)")
        return self._subdomain

    @property
    def base_url(self) -> str:
        return f"https://{self.worker}.{self.subdomain()}.workers.dev"

    def url(self, key: str) -> str:
        return f"{self.base_url}/{key}"

    # -- deploy
    def is_live(self) -> bool:
        try:
            r = self.public.get(self.base_url + "/")
            return r.status_code == 200 and LANDING.splitlines()[0] in r.text
        except httpx.HTTPError as exc:
            log.info("credits site not answering: %s", exc)
            return False

    def deploy(self) -> None:
        """Upload the Worker (bound to the namespace) and make sure its workers.dev route is on."""
        meta = {"main_module": "worker.js", "compatibility_date": "2026-01-01",
                "bindings": [{"type": "kv_namespace", "name": "CREDITS", "namespace_id": self.namespace_id()}]}
        self._api("PUT", f"/workers/scripts/{self.worker}",
                  files={"metadata": ("metadata", json.dumps(meta), "application/json"),
                         "worker.js": ("worker.js", WORKER_JS, "application/javascript+module")})
        try:                                  # new scripts get their workers.dev route by default; this pins it
            self._api("POST", f"/workers/scripts/{self.worker}/subdomain", json={"enabled": True, "previews_enabled": False})
        except SiteError as exc:
            log.warning("could not set the workers.dev route explicitly (%s); relying on the default", exc)
        log.info("deployed worker %s → %s", self.worker, self.base_url)

    def ensure(self, *, wait: float = 90.0, sleep=time.sleep) -> str:
        """The site's base URL, deploying the Worker when the root does not answer (then waiting up to
        ``wait`` seconds for the new route — a fresh workers.dev host can 523 for a minute)."""
        if self.is_live():
            return self.base_url
        self.deploy()
        t0 = time.monotonic()
        while time.monotonic() - t0 < wait:
            sleep(5)
            if self.is_live():
                return self.base_url
        raise SiteError(f"{self.base_url} still not answering {wait:.0f} s after deploying")

    # -- pages
    def put(self, key: str, page: str) -> None:
        if not KEY_RE.match(key):
            raise SiteError(f"bad page key {key!r}")
        # The KV REST write is a multipart form: ``value`` (+ optional ``metadata`` JSON).
        self._api("PUT", f"/storage/kv/namespaces/{self.namespace_id()}/values/{key}",
                  files={"value": (None, page.encode("utf-8")),
                         "metadata": (None, json.dumps({"written": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}))})

    def publish(self, key: str, page: str, **ensure_kw) -> str:
        """Write the page and return its public URL (raises SiteError when anything is off)."""
        base = self.ensure(**ensure_kw)
        self.put(key, page)
        return f"{base}/{key}"


def publish_credits(site: "Site | None", *, key: str, rows: list[dict], title: str, date: str = "", video_url: str = "",
                    shorts: int = 0, quiet: bool = False) -> str:
    """URL of the published page, or '' (with the reason logged and printed) — never raises."""
    if site is None:
        return ""
    try:
        url = site.publish(key, render_page(rows, title=title, date=date, video_url=video_url, shorts=shorts))
        if not quiet:
            print(f"credits page: {url} · {sum(1 for r in rows if captions.needs_credit(r))} credited of {len(rows)} photos",
                  flush=True)
        return url
    except (SiteError, httpx.HTTPError, KeyError, ValueError) as exc:
        log.warning("credits page failed: %s", exc)
        if not quiet:
            print(f"! credits page unavailable ({exc}); credits go in the description", flush=True)
        return ""


def main(argv: list[str] | None = None) -> int:
    """``python -m sof.credits_site deploy`` (sets up the Worker + namespace and writes a test page)."""
    from sof.config import Config

    args = list(sys.argv[1:] if argv is None else argv)
    site = Site.from_config(Config.from_env())
    if site is None:
        print("credits site off: set CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID", file=sys.stderr)
        return 2
    if not args or args[0] == "deploy":
        site.deploy()
        base = site.ensure()
        demo = render_page([{"source": "commons", "artist": "Example Author", "license": "CC BY-SA 4.0", "title": "Example photo",
                             "page": "https://commons.wikimedia.org/wiki/File:Example.jpg"}],
                           title="Test page", date=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        site.put("test", demo)
        print(f"credits site live: {base}/  (test page: {base}/test — may take a minute to appear)")
        return 0
    if args[0] == "url":
        print(site.base_url)
        return 0
    print("usage: python -m sof.credits_site [deploy|url]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
