"""Clip channel pipeline for sylusnix (runs on the PC).

  python run.py find                      # scan the watchlist, render up to 4 clips into out/<date>/ (no upload)
  python run.py find --creator "Jo Koy"   # one creator
  python run.py clip URL --start 123 --end 150 --headline "Bro Really Said That" --emojis "💀🥀"
  python run.py daily                     # find 3 clips, upload headline option 1 of each, spaced through the day
  python run.py daily --count 1 --privacy unlisted   # test the whole path without a public post

Each clip folder holds option1..3.mp4 (one per headline) and info.json."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys

from clipper import find, render, upload

MAX_AGE_DAYS = 45
AD_TITLE = re.compile(r"\b(sponsored|paid partnership|presented by|in partnership with|commercial|#ad)\b", re.I)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:30] or "clip"


def skip_reason(info: dict) -> str:
    """Why a source video must not be clipped automatically ('' = fine)."""
    title = info.get("title") or ""
    if AD_TITLE.search(title):
        return "looks like an ad or sponsored video"
    d = info.get("upload_date") or ""
    if re.fullmatch(r"\d{8}", d):
        age = (dt.date.today() - dt.date(int(d[:4]), int(d[4:6]), int(d[6:]))).days
        if age > MAX_AGE_DAYS:
            return f"too old ({age} days)"
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        return "live or upcoming"
    return ""


def find_clips(a) -> list:
    cfg, ledger = find.load_config(), find.load_ledger()
    creators = [c for c in cfg["creators"] if (c.get("verified") or a.include_unverified)
                and (not a.creator or a.creator.lower() in c["name"].lower())]
    day, made = find.OUT / dt.date.today().isoformat(), []
    for c in creators:
        if len(made) >= a.limit:
            break
        try:
            channel, vids = find.latest_videos(c["url"], a.videos)
        except Exception as exc:
            print(f"[skip] {c['name']}: {exc}")
            continue
        new = [v for v in vids if v["id"] not in ledger["videos"] and (v["duration"] or 0) >= 150]
        if not new:
            print(f"[none] {c['name']} ({channel}): nothing new")
            continue
        v = new[0]
        print(f"[scan] {c['name']} ({channel}): {v['title']} ({v['duration']:.0f}s)")
        entry = {"date": day.name, "creator": c["name"], "clips": 0}
        try:
            info, words = find.fetch_transcript(v["id"])
            why = skip_reason(info) or ("no usable captions" if len(words) < 80 else "")
            if why:
                ledger["videos"][v["id"]] = {**entry, "note": why}
                find.save_ledger(ledger)
                print(f"       skipped: {why}")
                continue
            clips = find.pick_moments(c["name"], info, words, a.clips)
        except Exception as exc:
            print(f"[fail] {c['name']}: {exc}")
            continue
        for clip in clips:
            if len(made) >= a.limit:
                break
            out = day / f"{slug(c['name'])}-{v['id']}-{int(clip['start'])}"
            try:
                outs = render.make_clip(out, c, v["id"], info, words, clip, cfg)
            except Exception as exc:
                print(f"[fail] render {out.name}: {exc}")
                continue
            made.append(out)
            entry["clips"] += 1
            print(f"[done] {out}  style {clip['style']}  {clip['end'] - clip['start']:.0f}s  {len(outs)} options")
            for i, h in enumerate(clip["headlines"], 1):
                print(f"         {i}. {h}")
        ledger["videos"][v["id"]] = entry
        find.save_ledger(ledger)
    print(f"{len(made)} clip(s) in {day}")
    return made


def cmd_find(a) -> int:
    find_clips(a)
    return 0


def cmd_daily(a) -> int:
    a.limit, a.creator, a.include_unverified = a.count, "", False
    made = find_clips(a)
    ledger = find.load_ledger()
    ups = ledger.setdefault("uploads", {})
    now = dt.datetime.now(dt.timezone.utc)
    for i, out in enumerate(made):
        info = json.loads((out / "info.json").read_text(encoding="utf-8"))
        title = (info.get("upload_title") or info["headlines"][0]).replace("|", "-").strip()
        desc = (f"Clip from {info['creator']}. Full video: {info['source']}\n\n"
                f"All credit to {info['creator']}. Contact sylusnix@gmail.com for removal requests.\n\n#shorts")
        tags = [info["creator"], "clips", "shorts", "funny moments" if info["style"] == "A" else "best moments"]
        when = None if (i == 0 or a.privacy != "public") else (now + dt.timedelta(hours=a.gap * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            vid = upload.upload(out / "option1.mp4", title, desc, tags, privacy=a.privacy, publish_at=when)
        except Exception as exc:
            print(f"[fail] upload {out.name}: {exc}")
            continue
        ups[vid] = {"date": out.parent.name, "clip": out.name, "title": title, "privacy": a.privacy, "publish_at": when}
        find.save_ledger(ledger)
        print(f"[up]   https://youtube.com/shorts/{vid}  {title}  ({'now' if not when else when})")
    return 0


def cmd_clip(a) -> int:
    m = re.search(r"(?:v=|shorts/|youtu\.be/|live/)([\w-]{11})", a.url)
    if not m:
        print("could not read a video id from that link")
        return 2
    cfg = find.load_config()
    info, words = find.fetch_transcript(m.group(1))
    clip = {"start": a.start, "end": a.end, "style": a.style.upper(), "headlines": a.headline, "emojis": a.emojis, "title": "", "why": "manual"}
    out = find.OUT / dt.date.today().isoformat() / f"manual-{m.group(1)}-{int(a.start)}"
    render.make_clip(out, {"name": info.get("channel") or "manual", "verified": False}, m.group(1), info, words, clip, cfg)
    print(out)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find")
    f.add_argument("--creator", default="")
    f.add_argument("--videos", type=int, default=4, help="latest uploads to look at per creator")
    f.add_argument("--clips", type=int, default=2, help="clips per video")
    f.add_argument("--limit", type=int, default=4, help="clips per run")
    f.add_argument("--include-unverified", action="store_true")
    f.set_defaults(fn=cmd_find)
    d = sub.add_parser("daily")
    d.add_argument("--count", type=int, default=3, help="clips to post")
    d.add_argument("--videos", type=int, default=4)
    d.add_argument("--clips", type=int, default=1, help="clips per source video (1 keeps the day's posts varied)")
    d.add_argument("--privacy", default="public", choices=["public", "unlisted", "private"])
    d.add_argument("--gap", type=float, default=4.0, help="hours between scheduled posts")
    d.set_defaults(fn=cmd_daily)
    c = sub.add_parser("clip")
    c.add_argument("url")
    c.add_argument("--start", type=float, required=True)
    c.add_argument("--end", type=float, required=True)
    c.add_argument("--style", default="A")
    c.add_argument("--headline", action="append", required=True)
    c.add_argument("--emojis", default="")
    c.set_defaults(fn=cmd_clip)
    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
