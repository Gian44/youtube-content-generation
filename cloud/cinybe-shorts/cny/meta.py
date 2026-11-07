"""Film metadata: Wikidata (open, no key) — director, cast, genres, year and the English Wikipedia title —
with TMDB first when ``TMDB_API_KEY`` is set. Ported from StoryFactory's ``services/movie_meta.py``; no
posters or stills are fetched (Gian 2026-11-07: no copyrighted film images on the channel).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import asdict, dataclass, field

import httpx

log = logging.getLogger("cny.meta")
TMDB_BASE = "https://api.themoviedb.org/3"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
USER_AGENT = "CinybeShorts/1.0 (+https://github.com/Gian44/youtube-content-generation) python-httpx"
P_DIRECTOR, P_CAST, P_GENRE, P_PUBLISHED = "P57", "P161", "P136", "P577"


@dataclass
class MovieMeta:
    title: str
    year: int | None = None
    source: str = "wikidata"            # tmdb | wikidata | none
    tmdb_id: int | None = None
    wikidata_id: str | None = None
    imdb_id: str | None = None
    wikipedia_title: str | None = None
    overview: str = ""
    runtime_minutes: int | None = None
    genres: list[str] = field(default_factory=list)
    director: str | None = None
    cast: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MovieMeta":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def _year_of(date_str: str | None) -> int | None:
    m = re.match(r"^\+?(\d{4})", date_str or "")
    return int(m.group(1)) if m else None


class TMDB:
    def __init__(self, api_key: str, *, transport=None, timeout: float = 20.0):
        self.api_key = api_key
        headers = {"User-Agent": USER_AGENT}
        self._bearer = len(api_key) > 60                     # v4 read tokens are long JWTs
        if self._bearer:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(base_url=TMDB_BASE, headers=headers, timeout=timeout, transport=transport)

    def _get(self, path: str, **params) -> dict:
        if not self._bearer:
            params["api_key"] = self.api_key
        resp = self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    def resolve(self, title: str, year: int | None = None) -> MovieMeta | None:
        params = {"query": title, "include_adult": "false"}
        if year:
            params["year"] = str(year)
        results = (self._get("/search/movie", **params) or {}).get("results") or []
        if not results and year:
            results = (self._get("/search/movie", query=title, include_adult="false") or {}).get("results") or []
        if not results:
            return None
        norm = " ".join(title.split()).lower()

        def score(r):
            s = float(r.get("popularity") or 0)
            if (r.get("title") or "").lower() == norm:
                s += 1000
            if year and _year_of(r.get("release_date")) == year:
                s += 500
            return s

        best = max(results, key=score)
        d = self._get(f"/movie/{best['id']}", append_to_response="external_ids,credits")
        credits = d.get("credits") or {}
        director = next((c.get("name") for c in credits.get("crew") or [] if c.get("job") == "Director"), None)
        cast = [c.get("name") for c in (credits.get("cast") or [])[:5] if c.get("name")]
        ext = d.get("external_ids") or {}
        return MovieMeta(title=d.get("title") or title, year=_year_of(d.get("release_date")) or year, source="tmdb",
                         tmdb_id=int(d["id"]), wikidata_id=ext.get("wikidata_id"), imdb_id=ext.get("imdb_id"),
                         overview=d.get("overview") or "", runtime_minutes=d.get("runtime"),
                         genres=[g.get("name") for g in d.get("genres") or [] if g.get("name")],
                         director=director, cast=cast)


class Wikidata:
    def __init__(self, *, transport=None, timeout: float = 20.0):
        self._client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=timeout, transport=transport)

    def _get(self, **params) -> dict:
        params.setdefault("format", "json")
        resp = self._client.get(WIKIDATA_API, params=params)
        resp.raise_for_status()
        return resp.json()

    def sitelink(self, wikidata_id: str) -> str | None:
        data = self._get(action="wbgetentities", ids=wikidata_id, props="sitelinks", sitefilter="enwiki")
        ent = (data.get("entities") or {}).get(wikidata_id) or {}
        return ((ent.get("sitelinks") or {}).get("enwiki") or {}).get("title")

    @staticmethod
    def _ids(claims: dict, prop: str, limit: int) -> list[str]:
        out = []
        for c in (claims.get(prop) or [])[:limit]:
            try:
                out.append(c["mainsnak"]["datavalue"]["value"]["id"])
            except (KeyError, TypeError):
                continue
        return out

    def labels(self, ids: list[str]) -> dict[str, str]:
        ids = [i for i in dict.fromkeys(ids) if i]
        if not ids:
            return {}
        data = self._get(action="wbgetentities", ids="|".join(ids[:50]), props="labels", languages="en")
        return {k: ((v.get("labels") or {}).get("en") or {}).get("value", "") for k, v in (data.get("entities") or {}).items()}

    def resolve(self, title: str, year: int | None = None) -> MovieMeta | None:
        hits = (self._get(action="wbsearchentities", search=title, language="en", type="item", limit="10") or {}).get("search") or []
        films = [h for h in hits if "film" in (h.get("description") or "").lower()]
        if year:
            films = [h for h in films if str(year) in (h.get("description") or "")] or films
        hit = (films or hits or [None])[0]
        if not hit:
            return None
        ent = ((self._get(action="wbgetentities", ids=hit["id"], props="sitelinks|claims", languages="en") or {})
               .get("entities") or {}).get(hit["id"]) or {}
        claims = ent.get("claims") or {}
        enwiki = ((ent.get("sitelinks") or {}).get("enwiki") or {}).get("title")
        pub = None
        try:
            pub = claims[P_PUBLISHED][0]["mainsnak"]["datavalue"]["value"]["time"]
        except (KeyError, IndexError, TypeError):
            pass
        d_ids, c_ids, g_ids = self._ids(claims, P_DIRECTOR, 2), self._ids(claims, P_CAST, 6), self._ids(claims, P_GENRE, 4)
        names = {}
        try:
            names = self.labels(d_ids + c_ids + g_ids)
        except httpx.HTTPError as exc:
            log.warning("wikidata labels failed: %s", exc)
        return MovieMeta(title=hit.get("label") or title, year=_year_of(pub) or year, source="wikidata",
                         wikidata_id=hit["id"], wikipedia_title=enwiki,
                         director=", ".join(n for i in d_ids if (n := names.get(i))) or None,
                         cast=[n for i in c_ids if (n := names.get(i))][:5],
                         genres=[n for i in g_ids if (n := names.get(i))][:4])


def resolve(title: str, year: int | None = None, *, tmdb_key: str | None = None) -> MovieMeta:
    """TMDB when a key exists, else Wikidata; never raises (a film with no metadata still gets a recap)."""
    key = tmdb_key if tmdb_key is not None else os.environ.get("TMDB_API_KEY", "")
    if key:
        try:
            meta = TMDB(key).resolve(title, year)
            if meta is not None:
                if not meta.wikipedia_title and meta.wikidata_id:
                    try:
                        meta.wikipedia_title = Wikidata().sitelink(meta.wikidata_id)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("wikidata sitelink failed: %s", exc)
                return meta
        except Exception as exc:  # noqa: BLE001
            log.warning("tmdb resolve failed for %r: %s", title, exc)
    try:
        meta = Wikidata().resolve(title, year)
        if meta is not None:
            return meta
    except Exception as exc:  # noqa: BLE001
        log.warning("wikidata resolve failed for %r: %s", title, exc)
    return MovieMeta(title=title, year=year, source="none")
