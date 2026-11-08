"""Subtitles from OpenSubtitles (REST API v1) when the film file comes without an ``.srt``: the file's
OpenSubtitles hash (a hash match is cut-exact, so the act time ranges land on the right scenes) and, as the very
last resort after whisper, title + year (another cut's timings can drift). Needs ``OPENSUBTITLES_API_KEY`` and a login (``OPENSUBTITLES_USERNAME`` / ``PASSWORD``): the free
tier allows a handful of downloads a day, one film needs one. Never raises: no subtitles → the caller falls
back to faster-whisper."""

from __future__ import annotations

import json
import logging
import os
import struct
import urllib.parse
import urllib.request

log = logging.getLogger("cny.subs")
API = "https://api.opensubtitles.com/api/v1"
UA = "cinybe-recaps v1.0"
HASH_CHUNK = 65536


def configured(cfg) -> bool:
    return bool(cfg.opensubtitles_api_key and cfg.opensubtitles_username and cfg.opensubtitles_password)


def movie_hash(path: str) -> str | None:
    """The OpenSubtitles hash: file size + the little-endian uint64 sum of the first and last 64 KB."""
    size = os.path.getsize(path)
    if size < HASH_CHUNK * 2:
        return None
    h = size
    with open(path, "rb") as f:
        for off in (0, size - HASH_CHUNK):
            f.seek(off)
            buf = f.read(HASH_CHUNK)
            for (v,) in struct.iter_unpack("<Q", buf):
                h = (h + v) & 0xFFFFFFFFFFFFFFFF
    return f"{h:016x}"


def rank(items: list[dict]) -> list[dict]:
    """Pure: best subtitle first — hash matches, then not hearing-impaired / not machine-translated, trusted
    uploaders, most downloads."""

    def key(it):
        a = it.get("attributes") or {}
        return (bool(a.get("moviehash_match")), not a.get("hearing_impaired"), not a.get("machine_translated"),
                bool(a.get("from_trusted")), int(a.get("download_count") or 0))

    return sorted([it for it in items if (it.get("attributes") or {}).get("files")], key=key, reverse=True)


class Client:
    def __init__(self, cfg):
        self.key, self.user, self.pw = cfg.opensubtitles_api_key, cfg.opensubtitles_username, cfg.opensubtitles_password
        self.langs = cfg.opensubtitles_languages
        self.token: str | None = None

    def _call(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None) -> dict:
        url = f"{API}{path}" + (("?" + urllib.parse.urlencode(params)) if params else "")
        headers = {"Api-Key": self.key, "User-Agent": UA, "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}")

    def login(self) -> None:
        resp = self._call("POST", "/login", body={"username": self.user, "password": self.pw})
        self.token = resp.get("token")
        if not self.token:
            raise RuntimeError("OpenSubtitles login gave no token")

    def search(self, *, title: str, year: int | None, moviehash: str | None, mode: str = "any") -> list[dict]:
        """``mode``: "hash" = only an exact-file match, "title" = only by title + year, "any" = hash then title."""
        found: list[dict] = []
        if moviehash and mode in ("hash", "any"):
            found = self._call("GET", "/subtitles", params={"moviehash": moviehash, "languages": self.langs}).get("data") or []
            found = [it for it in found if (it.get("attributes") or {}).get("moviehash_match")]
        if not found and mode in ("title", "any"):
            p = {"query": title, "languages": self.langs, "type": "movie", "order_by": "download_count", "order_direction": "desc"}
            if year:
                p["year"] = year
            found = self._call("GET", "/subtitles", params=p).get("data") or []
        return rank(found)

    def download(self, file_id: int, dest: str) -> str:
        resp = self._call("POST", "/download", body={"file_id": file_id, "sub_format": "srt"})
        link = resp.get("link")
        if not link:
            raise RuntimeError(f"OpenSubtitles gave no download link ({resp.get('message') or resp})")
        req = urllib.request.Request(link, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
            f.write(r.read())
        remaining = resp.get("remaining")
        if remaining is not None:
            print(f"opensubtitles: {remaining} downloads left today", flush=True)
        return dest


def fetch_srt(cfg, *, title: str, year: int | None, video_path: str, dest: str, mode: str = "any") -> str | None:
    """The best English .srt for this file, written to ``dest``; None when OpenSubtitles is not configured, finds
    nothing, or fails (the caller transcribes instead). ``mode`` as in ``Client.search``: a "hash" match is timed
    to this very file; a "title" match may be another cut and drift."""
    if not configured(cfg):
        return None
    try:
        c = Client(cfg)
        c.login()
        h = movie_hash(video_path)
        if mode == "hash" and not h:
            return None
        items = c.search(title=title, year=year, moviehash=h, mode=mode)
        if not items:
            print(f"opensubtitles: nothing for {title} ({year}) [{mode}]", flush=True)
            return None
        top = items[0]
        a = top.get("attributes") or {}
        fid = int(a["files"][0]["file_id"])
        c.download(fid, dest)
        how = "hash match" if a.get("moviehash_match") else "title match"
        print(f"opensubtitles: {a.get('release') or a.get('files')[0].get('file_name')} ({how}, "
              f"{int(a.get('download_count') or 0)} downloads)", flush=True)
        return dest if os.path.getsize(dest) > 2000 else None
    except Exception as exc:  # noqa: BLE001
        log.warning("opensubtitles failed: %s", exc)
        print(f"! opensubtitles failed ({str(exc)[:160]}); transcribing instead", flush=True)
        return None
