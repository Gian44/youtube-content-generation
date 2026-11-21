"""Twitch as a clip source for ranking Shorts.

Lists a streamer's most-viewed clips, downloads them, and makes word timings from the audio (Twitch clips carry no
captions). Unlike YouTube, Twitch serves all of this to cloud servers: checked on a GitHub runner on 2026-11-14.

Listing uses Twitch's official API when TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET are set (environment or .env),
and the public clips page (through yt-dlp) when they are not. Downloads always go through yt-dlp.

A ranking can be limited to one game (Twitch category). With the API keys the category id on each clip decides;
without them the clips page does not say the game, so each of the most viewed clips is looked up on its own page."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .find import ROOT, YTDLP, ask_claude, sentences, sh, short_label, short_title
from .upload import read_env

API = "https://api.twitch.tv/helix"
LEDGER = ROOT / "ledger_twitch.json"      # kept apart from ledger.json: the cloud job commits this one
WHOLE_S = 13.5                            # a clip this short is shown whole
# Games whose clips go only into that game's own ranking (run.py twitch --game), never into a general one: the game
# ranking has the release-day floor (--since) and the cutscene rule. 2026-11-19: a general ranking posted a GTA 6
# story scene clipped four days before the game's release.
GAME_ONLY = "Grand Theft Auto VI,Grand Theft Auto 6,GTA VI,GTA 6"
_token: dict = {}
_model = None


def load_ledger() -> dict:
    """clips: used in a posted ranking. blocked: held back by hand (clip id -> why), never used. rankings: the log."""
    led = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {}
    return {"clips": {}, "blocked": {}, "rankings": [], **led}


def save_ledger(ledger: dict) -> None:
    LEDGER.write_text(json.dumps(ledger, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def creds() -> tuple[str, str]:
    env = {**read_env(), **os.environ}
    return (env.get("TWITCH_CLIENT_ID") or "").strip(), (env.get("TWITCH_CLIENT_SECRET") or "").strip()


def app_token() -> str:
    if _token.get("exp", 0) > time.time() + 60:
        return _token["t"]
    cid, secret = creds()
    body = urllib.parse.urlencode({"client_id": cid, "client_secret": secret, "grant_type": "client_credentials"}).encode()
    with urllib.request.urlopen(urllib.request.Request("https://id.twitch.tv/oauth2/token", data=body), timeout=30) as r:
        data = json.loads(r.read())
    _token.update(t=data["access_token"], exp=time.time() + int(data.get("expires_in") or 3600))
    return _token["t"]


def _get(path: str, params: dict) -> dict:
    req = urllib.request.Request(f"{API}/{path}?{urllib.parse.urlencode(params)}",
                                 headers={"Authorization": "Bearer " + app_token(), "Client-Id": creds()[0]})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _epoch(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def game_names(names: str) -> list[str]:
    return [n.strip() for n in (names or "").split(",") if n.strip()]


def game_ids(names: str) -> dict[str, str]:
    """Twitch category ids for one or more game names separated by commas (exact names first, then a category
    search matched without regard to case). Returns {id: name as Twitch writes it}. Needs the API keys."""
    if not all(creds()):
        raise RuntimeError("category ids need TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET")
    found: dict[str, str] = {}
    for name in game_names(names):
        rows = _get("games", {"name": name}).get("data") or []
        if not rows:
            rows = [g for g in _get("search/categories", {"query": name, "first": 20}).get("data") or []
                    if _norm(g.get("name")) == _norm(name)]
        for g in rows:
            found[str(g["id"])] = g.get("name") or name
    if not found:
        raise RuntimeError(f"no Twitch category named {names}")
    return found


def window_start(now: datetime, days: int, since: str = "") -> datetime:
    """Start of the listing window: `days` back, but never before `since` (YYYY-MM-DD, UTC), so a game's ranking
    cannot reach back before its release day."""
    start = now - timedelta(days=days)
    if since:
        start = max(start, datetime.fromisoformat(since).replace(tzinfo=timezone.utc))
    return start


def clip_page(url: str) -> dict:
    """What a clip's own page says about it (through yt-dlp, no keys): the game it was made in and when."""
    r = sh(YTDLP + ["-J", "--no-playlist", url], timeout=120)
    if r.returncode != 0 or not (r.stdout or "").strip():
        raise RuntimeError(f"could not read {url}: {(r.stderr or '')[-160:]}")
    info = json.loads(r.stdout)
    return {"game": ((info.get("categories") or [""])[0] or ""), "created": float(info.get("timestamp") or 0),
            "clipper": info.get("uploader") or ""}


def clips_in_game(login: str, days: int, names: str, since: str = "", limit: int = 60, look: int = 40) -> list[dict]:
    """Without the API keys: the clips page lists by views but not by game, so the `look` most viewed clips are each
    read on their own page and kept when the category is one of `names`. A clip whose game or date cannot be read
    is left out (when in doubt, not a clip of this game)."""
    want = {_norm(n) for n in game_names(names)}
    start = window_start(datetime.now(timezone.utc), days, since).timestamp()
    out, seen = [], {}
    listed = clips_page(login, days, 100)
    # old clips are dropped before the `look` cut, so launch-hype clips from before `since` cannot crowd new ones out
    inside = [c for c in listed if not c["created"] or c["created"] >= start]
    print(f"[twitch] {login}: the clips page listed {len(listed)} clips for {days} days, {len(inside)} inside the window")
    for c in inside[:look]:
        try:
            page = clip_page(c["url"])
        except Exception as exc:
            print(f"[skip] clip {c['id']}: {str(exc)[-120:]}")
            continue
        seen[page["game"] or "(none)"] = seen.get(page["game"] or "(none)", 0) + 1
        made = c["created"] or page["created"]
        if _norm(page["game"]) in want and made >= start:
            out.append({**c, "created": made, "clipper": c["clipper"] or page["clipper"], "game": page["game"]})
    print(f"[twitch] {login}: categories of the most viewed clips in {days} days: "
          + (", ".join(f"{g} x{n}" for g, n in sorted(seen.items(), key=lambda kv: -kv[1])) or "none read"))
    return out[:limit]


def outside_games(clips: list[dict], names: str = GAME_ONLY, want: int = 9, look: int = 25) -> list[dict]:
    """For a general ranking: the first `want` of `clips` (given most viewed first) that were not made in one of the
    games `names`. Each clip's own page is read for its category, at most `look` pages; a clip whose page cannot be
    read is left out (when in doubt, it may be a clip of that game)."""
    held = {_norm(n) for n in game_names(names)}
    out: list[dict] = []
    for c in clips[:look]:
        if len(out) >= want:
            break
        try:
            game = clip_page(c["url"])["game"]
        except Exception as exc:
            print(f"[skip] clip {c['id']}: {str(exc)[-120:]}")
            continue
        if _norm(game) in held:
            print(f"[skip] clip {c['id']}: made in {game}, kept for that game's own ranking")
            continue
        out.append({**c, "game": game})
    return out


def clips_api(login: str, days: int, limit: int = 60, since: str = "") -> list[dict]:
    users = _get("users", {"login": login}).get("data") or []
    if not users:
        raise RuntimeError(f"no Twitch user named {login}")
    now = datetime.now(timezone.utc)
    params = {"broadcaster_id": users[0]["id"], "first": 100,
              "started_at": window_start(now, days, since).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "ended_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    out: list[dict] = []
    for _ in range(3):
        data = _get("clips", params)
        for c in data.get("data") or []:
            out.append({"id": c["id"], "url": c["url"], "title": c.get("title") or "",
                        "views": int(c.get("view_count") or 0), "duration": float(c.get("duration") or 0),
                        "created": _epoch(c.get("created_at") or ""), "vod": c.get("video_id") or "",
                        "vod_offset": c.get("vod_offset"), "clipper": c.get("creator_name") or "",
                        "game_id": str(c.get("game_id") or ""), "streamer": users[0].get("display_name") or login})
        cursor = (data.get("pagination") or {}).get("cursor")
        if not cursor:
            break
        params["after"] = cursor
    out.sort(key=lambda c: -c["views"])
    return out[:limit]


def clips_page(login: str, days: int, limit: int = 60) -> list[dict]:
    rng = "24hr" if days <= 1 else "7d" if days <= 7 else "30d" if days <= 30 else "all"
    r = sh(YTDLP + ["--flat-playlist", "--playlist-end", str(limit), "-J",
                    f"https://www.twitch.tv/{login}/clips?filter=clips&range={rng}"], timeout=180)
    if r.returncode != 0 or not (r.stdout or "").strip():
        raise RuntimeError(f"could not list clips for {login}: {(r.stderr or '')[-200:]}")
    out = []
    for e in json.loads(r.stdout).get("entries") or []:
        if not e or not e.get("url"):
            continue
        out.append({"id": e["url"].rstrip("/").rsplit("/", 1)[-1], "url": e["url"], "title": e.get("title") or "",
                    "views": int(e.get("view_count") or 0), "duration": float(e.get("duration") or 0),
                    "created": float(e.get("timestamp") or 0), "vod": "", "vod_offset": None, "clipper": "",
                    "streamer": login})
    out.sort(key=lambda c: -c["views"])
    return out


def top_clips(login: str, days: int, limit: int = 60, game: str = "", since: str = "") -> tuple[list[dict], str]:
    """Most-viewed clips first. Returns (clips, where the list came from). With `game` (one or more category names
    separated by commas) only clips made in that game are returned, and none from before `since`."""
    if game:
        if all(creds()):
            try:
                ids = game_ids(game)
                return [c for c in clips_api(login, days, 300, since) if c["game_id"] in ids][:limit], "api"
            except Exception as exc:
                print(f"[twitch] API game listing failed ({str(exc)[-120:]}); reading each clip's page")
        return clips_in_game(login, days, game, since, limit), "clip pages"
    if all(creds()):
        try:
            return clips_api(login, days, limit), "api"
        except Exception as exc:
            print(f"[twitch] API listing failed ({str(exc)[-120:]}); using the clips page")
    return clips_page(login, days, limit), "page"


SAME_TITLE_S = 20 * 60                    # two clips with one title made this close together are one moment


def same_moment(a: dict, b: dict) -> bool:
    """Viewers often clip one moment several times. Same stream within a minute, or made within 2.5 minutes, or
    given the same title (whatever the capitals and punctuation) within 20 minutes: on 2026-11-20 a ranking showed
    "HE TRYNA IGNORE IT" and "He tryna ignore it" as two entries. Further apart, a shared title is not enough: a
    streamer's viewers reuse joke titles ("copyright test") for different moments of a long stream."""
    if a["vod"] and a["vod"] == b["vod"] and a["vod_offset"] is not None and b["vod_offset"] is not None:
        return abs(a["vod_offset"] - b["vod_offset"]) < 60
    gap = abs(a["created"] - b["created"])
    if gap < 150:
        return True
    title = _norm(a.get("title") or "")
    return bool(title) and title == _norm(b.get("title") or "") and bool(a["created"] and b["created"]) and gap < SAME_TITLE_S


def distinct(clips: list[dict]) -> list[dict]:
    kept: list[dict] = []
    for c in clips:
        if not any(same_moment(c, k) for k in kept):
            kept.append(c)
    return kept


# Subjects an unattended job must not repost, whatever the view count. Checked against the title and the speech.
_HEAVY = re.compile(r"(?i)\b(rap(e|ed|ist)|molest\w*|pedo\w*|groom(ed|ing|er)|sexual(ly)? assault\w*|suicid\w*|"
                    r"kill (yo)?urself|overdos\w*)\b")
_ROUGH = re.compile(r"(?i)fuck|shit|bitch|nigg|asshole|dick|pussy|cunt|fag|retard")


def mask(token: str) -> str:
    """Caption text only: keep the first letter of a swear word and star the rest."""
    return _ROUGH.sub(lambda m: m.group(0)[0] + "*" * (len(m.group(0)) - 1), token)


def download(clip: dict, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 50_000:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = sh(YTDLP + ["--force-overwrites", "-o", str(dest), clip["url"]], timeout=300)
    if not dest.exists() or dest.stat().st_size < 50_000:
        raise RuntimeError(f"download failed for {clip['url']}: {(r.stderr or '')[-200:]}")
    return dest


def transcribe(path: Path) -> list[dict]:
    """Word timings from the clip's audio: [{"t": start, "e": end, "w": text}], the same shape the YouTube caption
    reader returns. Cached next to the file."""
    cache = path.with_suffix(".words.json")
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(os.environ.get("WHISPER_MODEL", "small.en"), device="cpu", compute_type="int8")
    # Decode with ffmpeg and hand the model raw samples: its own file reader broke on a library version
    # mismatch (2026-11-14), and ffmpeg is needed for rendering anyway.
    dec = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True, timeout=300)
    audio = np.frombuffer(dec.stdout, dtype=np.float32)
    if dec.returncode != 0 or audio.size < 1600:       # no sound track, or under a tenth of a second
        cache.write_text("[]", encoding="utf-8")
        return []
    segments, _ = _model.transcribe(audio, language="en", word_timestamps=True, vad_filter=True,
                                    condition_on_previous_text=False)
    words = []
    for seg in segments:
        for w in seg.words or []:
            tok = (w.word or "").strip()
            if tok[:1] == "," and tok[1:].isdigit() and words:   # "3" + ",000" comes back as two words
                words[-1]["w"] += tok
                words[-1]["e"] = round(float(w.end), 3)
            elif tok and (w.probability or 0) >= 0.3:      # low-confidence words are mostly noise read as speech
                words.append({"t": round(float(w.start), 3), "e": round(float(w.end), 3), "w": tok})
    cache.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
    return words


def frame_sheet(src: Path, dur: float, dest: Path) -> Path:
    """Six frames spread over the clip on one image (3 x 2, left to right then down), so the model that writes the
    labels can see what happens. Titles and speech alone were too thin: 6 of 9 clips came back unlabelled."""
    if dest.exists() and dest.stat().st_size > 5_000:
        return dest
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf",
                        f"fps=6/{max(dur, 1.0):.3f},scale=448:252,tile=3x2", "-frames:v", "1", "-q:v", "4", str(dest)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    if r.returncode != 0 or not dest.exists():
        raise RuntimeError(f"no frame sheet for {src.name}: {(r.stderr or '')[-200:]}")
    return dest


LOOK_SYSTEM = ("You are a short-form video editor. Use the Read tool to look at the image files you are told about, "
               "then answer with one JSON object and nothing else.")


def ask_claude_look(prompt: str, images: list[Path], model: str = "sonnet", timeout: int = 900) -> dict:
    """ask_claude(), but the model first looks at images (copied into a neutral folder as clip1.jpg, clip2.jpg...)."""
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude CLI not found on PATH")
    with tempfile.TemporaryDirectory(prefix="clip-look-") as td:
        for i, p in enumerate(images, 1):
            shutil.copyfile(p, Path(td) / f"clip{i}.jpg")
        cmd = [exe, "-p", "--output-format", "json", "--model", model, "--tools", "Read", "--allowedTools", "Read",
               "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence", "--max-turns", "14",
               "--add-dir", td, "--system-prompt", LOOK_SYSTEM]
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, cwd=td)
    try:
        data = json.loads((r.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        data = {}
    if r.returncode != 0 or data.get("is_error") or not data.get("result"):
        raise RuntimeError(f"claude exit {r.returncode}: {(data.get('result') or r.stderr or r.stdout or '')[-300:]}")
    text = str(data["result"])
    a, b = text.find("{"), text.rfind("}")
    return json.loads(text[a:b + 1]) if a >= 0 and b > a else {}


GAME_NOTE = """Every clip below was made while {name} was playing {game}. Say so in the titles ("{name}'s" and
"top {game}", upload title for example "{name}'s top 5 {game} moments"), and keep labels about what {name}
does or how they react. Also mark "skip": true for a clip that is mostly a story cutscene, shows an ending, a
mission's outcome or another story reveal, or shows an in-game TV show, radio or comedy act on its own: the
publisher takes those down, and they spoil the game.

"""

GENERAL_NOTE = """Also mark "skip": true for a clip that mostly shows a video game's story cutscene, a game trailer or
other footage of a game that is not out yet, rather than {name} playing or doing something on camera.

"""

LOOK_NOTE = """First look at the image files clip1.jpg to clip{n}.jpg in this folder with the Read tool, one per clip below.
Each shows six frames from that clip in time order (left to right, then the second row), spread evenly from its
start to near its end.

"""

PLAN_PROMPT = """You are preparing a "top clips" ranking Short for a clip channel. Below are {name}'s most viewed Twitch
clips of the last {days} days, in order of real view count. Do not reorder them.

For each clip you get its views, its length, the title a viewer gave it, and what is said in it as sentences
written [start-end] text (seconds from the start of the clip). The speech-to-text is rough and clip titles are often
jokes.

{blocks}

For every clip write:
- "label": 1 or 2 plain words for what happens, the way a viewer would blurt it out ("Rage", "Jumpscared",
  "Wiped out"), then optionally one emoji. Never a description of the clip. Use only what the frames, the title
  and the speech show. No view counts and nothing you cannot back up.
- "start" and "end": the part to show, 7 to 12 seconds. Where there is speech, start at a sentence's start time and
  end at a sentence's end time, and keep the line that makes the label true plus the reaction after it. A clip of
  13 seconds or less is shown whole: start 0, end its length. If a longer clip has little or no speech, use its
  last 11 seconds.
- "weak": true only when the frames, the title and the speech together still do not tell you what happens.
- "skip": true when the clip should not be reposted: hate aimed at a person or group, sexual content, someone
  getting hurt for real, a private person being humiliated, or an accusation of a crime or of sexual misconduct
  against anyone, including a rumour being repeated.

Also write:
- "title_plain" and "title_accent": the on-screen title in two parts, at most 5 words together, for example
  "{name}'s" and "top clips".
- "upload_title": a YouTube title under 70 characters, plain and true. It may say what number 1 is.
The Short shows only the five most viewed clips that you did not mark weak or skip, in the order given. So a title
may say "top 5" but no other count, and "number 1" means the first clip you kept.

{game_note}Return JSON only: {{"title_plain": "", "title_accent": "", "upload_title": "", "clips": [{{"n": 1, "label": "", "start": 0.0, "end": 0.0, "weak": false, "skip": false}}]}}"""


def _span(clip: dict, words: list[dict], s, e) -> tuple[float, float, bool]:
    """Checked cut points for one clip. Returns (start, end, came from the model)."""
    dur = clip["duration"]
    if dur <= WHOLE_S:
        return 0.0, dur, False
    sents = sentences(words)
    try:
        s, e = float(s), float(e)
        ok = 0 <= s < e <= dur + 0.5 and 4.0 <= e - s <= 14.5
    except (TypeError, ValueError):
        ok = False
    if ok and sents:      # snap to real sentence edges so a rounded number cannot cut a word
        s2 = min((x[0] for x in sents), key=lambda v: abs(v - s))
        e2 = min((x[1] for x in sents), key=lambda v: abs(v - e))
        if 4.0 <= e2 - s2 <= 14.5:
            s, e = max(0.0, s2 - 0.06), min(dur, e2 + 0.25)
    if not ok:            # viewers clip right after the moment, so it sits near the end
        s, e = max(0.0, dur - 11.3), max(dur - 0.3, 1.0)
    return round(s, 2), round(min(e, dur), 2), ok


def plan_ranking(name: str, login: str, days: int, cands: list[dict], words_by_id: dict, count: int = 5,
                 partial: bool = False, game: str = "") -> dict | None:
    """Labels, cut points and titles for the `count` most-viewed usable clips. `cands` is already in view order.
    partial = a clip with more views was left out before this call (held back, or used before)."""
    blocks = []
    for n, c in enumerate(cands, 1):
        sents = sentences(words_by_id.get(c["id"]) or [])
        said = "\n".join(f"[{a:.1f}-{b:.1f}] {t}" for a, b, t in sents) or "(no speech picked up)"
        blocks.append(f"Clip {n}: {c['views']:,} views, {c['duration']:.0f} s, title \"{c['title']}\"\n{said}")
    period = "week" if days <= 7 else "month" if days <= 31 else "year"
    prompt = PLAN_PROMPT.format(name=name, days=days, period=period, blocks="\n\n".join(blocks),
                                game_note=(GAME_NOTE.format(name=name, game=game) if game
                                           else GENERAL_NOTE.format(name=name)))
    kind = f"{game} clips" if game else "clips"
    try:
        sheets = [frame_sheet(Path(c["file"]), c["duration"], Path(c["file"]).with_suffix(".sheet.jpg")) for c in cands]
        data = ask_claude_look(LOOK_NOTE.format(n=len(cands)) + prompt, sheets)
    except Exception as exc:        # no frames or no answer: plan from titles and speech alone
        print(f"[twitch] planning without frames ({str(exc)[-140:]})")
        data = ask_claude(prompt, timeout=300)
    got = {}
    for row in data.get("clips") or []:
        try:
            got[int(row["n"])] = row
        except (KeyError, TypeError, ValueError):
            continue
    rows = []
    for n, c in enumerate(cands, 1):
        row = got.get(n) or {}
        label = short_label(str(row.get("label") or ""))
        s, e, from_model = _span(c, words_by_id.get(c["id"]) or [], row.get("start"), row.get("end"))
        rows.append({"clip": c, "label": label, "start": s, "end": e, "refined": from_model,
                     "weak": bool(row.get("skip")) or not label})    # the model's own "weak" is advice only:
        # it flipped between runs on clips it had labelled well, and dropped a 113K-view clip that way
        heavy = _HEAVY.search(c["title"] + " " + " ".join(w["w"] for w in words_by_id.get(c["id"]) or []))
        if heavy:
            rows[-1]["weak"], row = True, {**row, "skip": True}
        why = "skip" if row.get("skip") else "no label" if not label else "ok, unsure" if row.get("weak") else "ok"
        print(f"         {n}. [{why}] {c['views']:,} views, {c['duration']:.0f}s, \"{c['title'][:40]}\" -> {label or '(no label)'}")
    strong = [r for r in rows if not r["weak"]]
    if len(strong) < count:
        return None
    items = []
    for rank, r in enumerate(strong[:count], 1):       # still in view order: rank 1 is the most viewed
        c = r["clip"]
        items.append({"rank": rank, "label": r["label"], "video_id": c["id"], "start": r["start"], "end": r["end"],
                      "refined": r["refined"], "creator": name, "views": c["views"], "clip_title": c["title"],
                      "clipper": c["clipper"], "source_url": c["url"], "src_file": c["file"]})
    plain, accent = short_title(str(data.get("title_plain") or f"{name}'s").strip(),
                                str(data.get("title_accent") or f"top {game or 'clips'}").strip(),
                                (f"{name}'s", f"top {game or 'clips'}"))
    upload_title = str(data.get("upload_title") or f"{name}'s {count} most viewed {kind} this {period}").strip()[:95]
    if partial or any(r["weak"] for r in rows[:rows.index(strong[count - 1])]):
        # a clip with more views is missing, so "top 5" and "number 1" would not be true
        upload_title = f"{count} of {name}'s most viewed {'Twitch ' if not game else ''}{kind} this {period}"
    # the model sees every candidate and once wrote "Top 9" over a five-clip video
    upload_title = re.sub(r"(?i)\b(top|best)\s+\d+\b", lambda m: f"{m.group(1)} {count}", upload_title)
    return {"theme": f"twitch:{login}:top:{days}d" + (f":{game}" if game else ""), "game": game,
            "title_plain": plain, "title_accent": accent,
            "upload_title": upload_title, "items": items}
