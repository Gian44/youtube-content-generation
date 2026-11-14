"""YouTube access for the sylusnix channel: one-time OAuth (auth_url / exchange) and resumable upload.
Credentials live in cloud/clip-channel/.env (git-ignored): YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
REDIRECT = "http://127.0.0.1:8765/"
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def read_env() -> dict:
    out = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def set_env(key: str, value: str) -> None:
    env = read_env()
    env[key] = value
    ENV.write_text("".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf-8")


def auth_url() -> str:
    q = {"client_id": read_env()["YT_CLIENT_ID"], "redirect_uri": REDIRECT, "response_type": "code", "scope": SCOPES,
         "access_type": "offline", "prompt": "consent"}
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(q)


def _post(url: str, data: dict) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{url} -> {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")


def exchange(code: str) -> None:
    env = read_env()
    tok = _post(TOKEN_URL, {"code": code, "client_id": env["YT_CLIENT_ID"], "client_secret": env["YT_CLIENT_SECRET"],
                            "redirect_uri": REDIRECT, "grant_type": "authorization_code"})
    if not tok.get("refresh_token"):
        raise RuntimeError("no refresh token returned")
    set_env("YT_REFRESH_TOKEN", tok["refresh_token"])


def access_token() -> str:
    env = read_env()
    tok = _post(TOKEN_URL, {"client_id": env["YT_CLIENT_ID"], "client_secret": env["YT_CLIENT_SECRET"],
                            "refresh_token": env["YT_REFRESH_TOKEN"], "grant_type": "refresh_token"})
    return tok["access_token"]


def _api(method: str, url: str, token: str, body: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(url, data=body, method=method, headers={"Authorization": f"Bearer {token}", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            return r.headers, r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {url.split('?')[0]} -> {e.code}: {e.read().decode('utf-8', 'replace')[:400]}")


def whoami() -> dict:
    _, body = _api("GET", "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true", access_token())
    items = json.loads(body).get("items") or []
    return {"id": items[0]["id"], "title": items[0]["snippet"]["title"]} if items else {}


def upload(path: Path, title: str, description: str, tags: list[str], privacy: str = "public",
           publish_at: str | None = None, category: str = "24") -> str:
    """Uploads one video and returns its id. publish_at (RFC3339 UTC) schedules it: YouTube needs privacy private for that."""
    status = {"privacyStatus": "private" if publish_at else privacy, "selfDeclaredMadeForKids": False}
    if publish_at:
        status["publishAt"] = publish_at
    meta = {"snippet": {"title": title[:100], "description": description[:4900], "tags": tags[:15], "categoryId": category},
            "status": status}
    token = access_token()
    data = Path(path).read_bytes()
    headers, _ = _api("POST", "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                      token, json.dumps(meta).encode(),
                      {"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": "video/mp4",
                       "X-Upload-Content-Length": str(len(data))})
    _, body = _api("PUT", headers["Location"], token, data, {"Content-Type": "video/mp4"})
    return json.loads(body)["id"]
