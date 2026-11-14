"""Clip channel pipeline for sylusnix (runs on the PC).

  python run.py find                      # scan the watchlist, render up to 4 clips into out/<date>/ (no upload)
  python run.py find --creator "Jo Koy"   # one creator
  python run.py clip URL --start 123 --end 150 --headline "Bro Really Said That" --emojis "💀🥀"
  python run.py daily                     # find 3 clips, upload headline option 1 of each, spaced through the day
  python run.py daily --count 1 --privacy unlisted   # test the whole path without a public post

  python run.py rank --creator MrBeast    # one ranking Short: five moments from five videos, one theme
  python run.py post FOLDER               # upload a folder that is already rendered
  python run.py auto                      # the unattended daily job: 1 ranking + 2 clips (Task Scheduler runs it)
  python run.py check                     # is everything the daily job needs working?
  python run.py twitch --upload           # one ranking from a streamer's most-viewed Twitch clips (cloud job)

Each clip folder holds option1..3.mp4 (one per headline) and info.json."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from clipper import find, render, upload

MAX_AGE_DAYS = 45
AD_TITLE = re.compile(r"\b(sponsored|paid partnership|presented by|in partnership with|commercial|#ad)\b", re.I)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:30] or "clip"


def skip_reason(info: dict, max_age: int = MAX_AGE_DAYS) -> str:
    """Why a source video must not be clipped automatically ('' = fine)."""
    title = info.get("title") or ""
    if AD_TITLE.search(title):
        return "looks like an ad or sponsored video"
    d = info.get("upload_date") or ""
    if re.fullmatch(r"\d{8}", d):
        age = (dt.date.today() - dt.date(int(d[:4]), int(d[4:6]), int(d[6:]))).days
        if age > max_age:
            return f"too old ({age} days)"
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        return "live or upcoming"
    return ""


def find_clips(a) -> list:
    cfg, ledger = find.load_config(), find.load_ledger()
    creators = [c for c in cfg["creators"] if (c.get("verified") or a.include_unverified)
                and (not a.creator or a.creator.lower() in c["name"].lower())]
    if creators and not a.creator:   # start from a different creator each day so the top of the list is not overused
        k = dt.date.today().toordinal() % len(creators)
        creators = creators[k:] + creators[:k]
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


MIN_GAP_H = 12                       # the unattended job never runs twice within this many hours
LOCK = find.WORK / "auto.lock"


def ranking_meta(items: list) -> tuple[str, list]:
    """Description and tags for a ranking upload: credit and one link per entry."""
    who = ", ".join(dict.fromkeys(it["creator"] for it in items))
    links = "\n".join(f"{it['rank']}. https://www.youtube.com/watch?v={it['video_id']}&t={int(it['start'])}s" for it in items)
    desc = f"Moments from {who}. Full videos:\n{links}\n\nAll credit to {who}. Contact sylusnix@gmail.com for removal requests.\n\n#shorts"
    return desc, [*who.split(", "), "ranking", "top 5", "shorts"]


def clip_meta(info: dict, option: int = 1) -> tuple[str, str, list]:
    """Title, description and tags for a single-clip upload (styles A and B)."""
    who = info["creator"]
    title = (info.get("upload_title") or info["headlines"][option - 1]).replace("|", "-").strip()
    desc = (f"Clip from {who}. Full video: {info['source']}\n\n"
            f"All credit to {who}. Contact sylusnix@gmail.com for removal requests.\n\n#shorts")
    return title, desc, [who, "clips", "shorts", "funny moments" if info.get("style") == "A" else "best moments"]


def build_ranking(creators: list, cfg: dict, ledger: dict, videos: int = 6, max_age: int = 150):
    """Read the creators' recent videos, pick one theme with five moments from five different videos, render it.
    Returns (folder, plan), or (None, reason) when no ranking can be made."""
    per = -(-videos // len(creators))
    sources = []
    for c in creators:
        try:
            _, vids = find.latest_videos(c["url"], per + 4)
        except Exception as exc:
            print(f"[skip] {c['name']}: {exc}")
            continue
        took = 0
        for v in vids:
            if took >= per:
                break
            if (v["duration"] or 0) < 240:
                continue
            try:
                info, words = find.fetch_transcript(v["id"])
            except Exception as exc:
                print(f"[fail] {c['name']} {v['id']}: {str(exc)[-120:]}")
                continue
            why = skip_reason(info, max_age) or ("no usable captions" if len(words) < 300 else "")
            if why:
                print(f"[skip] {v['title'][:50]}: {why}")
                continue
            took += 1
            sources.append({"key": f"V{len(sources) + 1}", "id": v["id"], "creator": c["name"], "info": info, "words": words})
            print(f"[src]  V{len(sources)} {c['name']}: {v['title']} ({v['duration']:.0f}s)")
    if len(sources) < 5:
        return None, f"only {len(sources)} usable source videos (five needed)"
    names = {c["name"] for c in creators}
    rankings = ledger.setdefault("rankings", [])
    used = [r.get("theme", "") for r in rankings if not r.get("creators") or names & set(r["creators"])]
    plan = find.pick_ranking_multi(sources, used)
    if not plan:
        return None, "no single theme covers five different videos"
    day = find.OUT / dt.date.today().isoformat()
    out = day / f"rank-{slug(plan['theme'])}"
    render.make_ranking(out, plan, {s["id"]: s["words"] for s in sources}, cfg)
    rankings.append({"date": day.name, "creators": sorted(names), "theme": plan["theme"],
                     "videos": [it["video_id"] for it in plan["items"]]})
    find.save_ledger(ledger)
    return out, plan


def ranking_candidates(cfg: dict, ledger: dict) -> list:
    """Verified creators in the order the unattended job should try them for a ranking: never ranked first, then
    least recently ranked. Leaves out anyone ranked in the last 3 days or who yielded nothing in the last 7."""
    today = dt.date.today()

    def days_since(day) -> int:
        return (today - dt.date.fromisoformat(day)).days if day else 10_000

    last: dict = {}
    for r in ledger.get("rankings") or []:
        for name in r.get("creators") or []:
            last[name] = max(last.get(name, ""), r.get("date", ""))
    skips = ledger.get("rank_skips") or {}
    ok = [c for c in cfg["creators"] if c.get("verified") and days_since(skips.get(c["name"])) > 7
          and days_since(last.get(c["name"])) > 3]
    return sorted(ok, key=lambda c: last.get(c["name"], ""))


def cmd_rank(a) -> int:
    """One ranking Short: five moments on one theme, each from a different video of the named creator(s)."""
    cfg, ledger = find.load_config(), find.load_ledger()
    if a.redo:
        found = sorted(find.OUT.glob(f"*/{a.redo}"))
        if not found:
            print("no clip folder named", a.redo)
            return 2
        out = found[-1]
        old = json.loads((out / "info.json").read_text(encoding="utf-8"))
        words_by_id = {it["video_id"]: find.cached_words(it["video_id"]) for it in old["items"]}
        keep = ("rank", "key", "video_id", "creator", "start", "end", "label")
        items = [{k: it[k] for k in keep} for it in old["items"]]
        if a.keep_cuts:      # cut points were edited by hand in info.json: render them as they are
            items = [{**it, "refined": True} for it in items]
        else:
            items = find.refine_spans(" ".join(old["title"]), items, words_by_id)
        plan = {"theme": old["theme"], "title_plain": old["title"][0], "title_accent": old["title"][1],
                "upload_title": old["upload_title"], "items": items}
        render.make_ranking(out, plan, words_by_id, cfg)
        print(f"[redo] {out}")
        for it in plan["items"]:
            print(f"         {it['rank']}. {it['label']}  {it['start']:.1f}-{it['end']:.1f}s ({it['end'] - it['start']:.1f}s)"
                  f"{'' if it.get('refined') else '  (fallback cut)'}")
        return 0
    if not a.creator:
        print("give --creator NAME or --redo FOLDER")
        return 2
    names = [n.strip().lower() for n in a.creator.split(",") if n.strip()]
    creators = [c for c in cfg["creators"] if c.get("verified") and any(n in c["name"].lower() for n in names)]
    if not creators:
        print("no verified creator on the watchlist matches", a.creator)
        return 2
    out, plan = build_ranking(creators, cfg, ledger, a.videos, a.max_age)
    if not out:
        print("no ranking made:", plan)
        return 1
    print(f"[done] {out}")
    print(f"       title: {plan['title_plain']} {plan['title_accent']}  |  upload: {plan['upload_title']}")
    for it in plan["items"]:
        print(f"         {it['rank']}. {it['label']}  <- {it['key']} {it['creator']} {it['start']:.1f}-{it['end']:.1f}s")
    if a.upload:
        desc, tags = ranking_meta(plan["items"])
        upload_or_queue(ledger, out, 1, plan["upload_title"], desc, tags, a.privacy)
    return 0


def cmd_post(a) -> int:
    """Upload a video that is already rendered in out/<date>/<folder> (any style) to the sylusnix channel."""
    found = sorted(find.OUT.glob(f"*/{a.folder}"))
    if not found:
        print("no clip folder named", a.folder)
        return 2
    out = found[-1]
    info = json.loads((out / "info.json").read_text(encoding="utf-8"))
    if info.get("style") == "C":
        who = ", ".join(dict.fromkeys(it["creator"] for it in info["items"]))
        links = "\n".join(f"{it['rank']}. {it['source']}" for it in info["items"])
        title = info["upload_title"]
        desc = f"Moments from {who}. Full videos:\n{links}\n\nAll credit to {who}. Contact sylusnix@gmail.com for removal requests.\n\n#shorts"
        tags = [*who.split(", "), "ranking", "top 5", "shorts"]
    else:
        who = info["creator"]
        title = (info.get("upload_title") or info["headlines"][a.option - 1]).replace("|", "-").strip()
        desc = (f"Clip from {who}. Full video: {info['source']}\n\n"
                f"All credit to {who}. Contact sylusnix@gmail.com for removal requests.\n\n#shorts")
        tags = [who, "clips", "shorts"]
    vid = upload.upload(out / f"option{a.option}.mp4", title, desc, tags, privacy=a.privacy)
    ledger = find.load_ledger()
    ledger.setdefault("uploads", {})[vid] = {"date": out.parent.name, "clip": out.name, "title": title, "privacy": a.privacy}
    find.save_ledger(ledger)
    print(f"[up]   https://youtube.com/shorts/{vid}  {title}  ({a.privacy})")
    return 0


def upload_or_queue(ledger: dict, out, option: int, title: str, desc: str, tags: list, privacy: str = "public",
                    publish_at=None):
    """Upload a rendered video. If YouTube refuses (quota, network, expired sign-in) the video is kept in the
    ledger's pending list and the next unattended run posts it. Returns the video id, or None when queued."""
    try:
        vid = upload.upload(out / f"option{option}.mp4", title, desc, tags, privacy=privacy, publish_at=publish_at)
    except Exception as exc:
        print(f"[fail] upload {out.name}: {str(exc)[-200:]}  -> kept for the next run")
        ledger.setdefault("pending", []).append({"folder": out.relative_to(find.OUT).as_posix(), "option": option,
                                                 "title": title, "desc": desc, "tags": tags})
        find.save_ledger(ledger)
        return None
    ledger.setdefault("uploads", {})[vid] = {"date": out.parent.name, "clip": out.name, "title": title,
                                             "privacy": privacy, "publish_at": publish_at}
    find.save_ledger(ledger)
    print(f"[up]   https://youtube.com/shorts/{vid}  {title}  ({publish_at or 'now'})")
    return vid


def cmd_auto(a) -> int:
    """The unattended daily job (Windows Task Scheduler starts it through auto_task.pyw): post anything left from a
    failed upload, then make and post the day's rankings and clips. The first video goes public at once, the
    others are scheduled `--gap` hours apart."""
    find.WORK.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    ledger = find.load_ledger()
    last = (ledger.get("auto") or {}).get("last_run")
    if last and not a.force and (now - dt.datetime.fromisoformat(last)).total_seconds() < MIN_GAP_H * 3600:
        print(f"last run {last}: under {MIN_GAP_H} h ago, nothing to do")
        return 0
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 3 * 3600:
        print("another run is in progress")
        return 0
    LOCK.write_text(str(os.getpid()), encoding="utf-8")
    try:
        ledger.setdefault("auto", {})["last_run"] = now.isoformat(timespec="seconds")
        find.save_ledger(ledger)
        cfg, slot, posted = find.load_config(), 0, []

        def next_slot():
            nonlocal slot
            slot += 1
            return None if slot == 1 else (now + dt.timedelta(hours=a.gap * (slot - 1))).strftime("%Y-%m-%dT%H:%M:%SZ")

        pending, ledger["pending"] = ledger.get("pending") or [], []
        for p in pending:
            out = find.OUT / p["folder"]
            if not (out / f"option{p['option']}.mp4").exists():
                print(f"[drop] {p['folder']}: file is gone")
                continue
            if len(posted) >= 3:        # a backlog must not flood the channel in one day
                ledger["pending"].append(p)
                continue
            posted.append(upload_or_queue(ledger, out, p["option"], p["title"], p["desc"], p["tags"], "public", next_slot()))

        for _ in range(a.rankings):
            out = plan = None
            for c in ranking_candidates(cfg, ledger)[:3]:
                print(f"[rank] trying {c['name']}")
                try:
                    out, plan = build_ranking([c], cfg, ledger, a.videos, a.max_age)
                except Exception as exc:
                    out, plan = None, f"error: {str(exc)[-200:]}"
                if out:
                    break
                print(f"       nothing from {c['name']}: {plan}")
                ledger.setdefault("rank_skips", {})[c["name"]] = dt.date.today().isoformat()
                find.save_ledger(ledger)
            if out:
                desc, tags = ranking_meta(plan["items"])
                posted.append(upload_or_queue(ledger, out, 1, plan["upload_title"], desc, tags, "public", next_slot()))

        if a.clips:
            find.save_ledger(ledger)
            try:
                made = find_clips(argparse.Namespace(creator="", include_unverified=False, limit=a.clips, videos=4, clips=1))
            except Exception as exc:
                made = []
                print(f"[fail] clips: {str(exc)[-200:]}")
            ledger = find.load_ledger()          # find_clips keeps its own copy of the ledger
            for out in made:
                title, desc, tags = clip_meta(json.loads((out / "info.json").read_text(encoding="utf-8")))
                posted.append(upload_or_queue(ledger, out, 1, title, desc, tags, "public", next_slot()))

        done = [v for v in posted if v]
        runs = ledger.setdefault("auto", {}).setdefault("runs", [])
        runs.append({"at": now.isoformat(timespec="seconds"), "uploaded": done, "queued": len(ledger.get("pending") or [])})
        del runs[:-30]
        find.save_ledger(ledger)
        print(f"run finished: {len(done)} uploaded, {len(ledger.get('pending') or [])} waiting for the next run")
        return 0
    finally:
        LOCK.unlink(missing_ok=True)


def twitch_meta(plan: dict, login: str, days: int) -> tuple[str, list]:
    """Description and tags for a Twitch ranking: one link per entry, and the viewer who made each clip."""
    name = plan["items"][0]["creator"]
    lines = "\n".join(f"{it['rank']}. {it['source_url']}" + (f" (clipped by {it['clipper']})" if it.get("clipper") else "")
                      for it in plan["items"])
    desc = (f"{name}'s most viewed Twitch clips of the last {days} days, ranked by views.\n{lines}\n\n"
            f"All credit to {name}: https://www.twitch.tv/{login}. Contact sylusnix@gmail.com for removal requests.\n\n#shorts")
    return desc, [name, "twitch clips", "top clips", "ranking", "top 5", "shorts"]


def cmd_twitch(a) -> int:
    """One ranking Short from a streamer's most-viewed Twitch clips, ranked by real view counts. Needs nothing from
    YouTube until the upload, so it runs on GitHub Actions as well as on the PC."""
    from clipper import twitch
    cfg, ledger, today = find.load_config(), twitch.load_ledger(), dt.date.today()
    listed = cfg.get("twitch") or []
    if a.streamer:
        want = a.streamer.lower()
        streamers = [s for s in listed if want in (s["login"].lower(), s["name"].lower())] or [{"name": a.streamer, "login": want}]
    else:
        streamers = [s for s in listed if s.get("verified") or a.include_unverified]
    last = {r["login"]: r["date"] for r in ledger.get("rankings") or []}
    streamers.sort(key=lambda s: last.get(s["login"], ""))            # least recently ranked first
    for s in streamers:
        login, name = s["login"], s["name"]
        if not a.force and login in last and (today - dt.date.fromisoformat(last[login])).days < a.every:
            print(f"[skip] {name}: ranked on {last[login]}, at most one every {a.every} days")
            continue
        plan, words, days = None, {}, 0
        for days in (7, 30):
            try:
                clips, how = twitch.top_clips(login, days)
            except Exception as exc:
                print(f"[skip] {name}: {str(exc)[-160:]}")
                break
            floor = int(s.get("min_views") or a.min_views)
            fresh = [c for c in clips if c["id"] not in ledger["clips"] and c["id"] not in ledger["blocked"]
                     and c["duration"] >= 5 and c["views"] >= floor]
            cands = twitch.distinct(fresh)[:9]
            print(f"[twitch] {name}: {len(clips)} clips in {days} days (from the {how}), {len(cands)} unused candidates")
            if len(cands) < 5:
                continue
            ready = []
            for c in cands:
                try:
                    c["file"] = str(twitch.download(c, find.WORK / "twitch" / login / f"{c['id']}.mp4"))
                    words[c["id"]] = twitch.transcribe(Path(c["file"]))
                    ready.append(c)
                except Exception as exc:
                    print(f"[skip] clip {c['id']}: {str(exc)[-160:]}")
            used = {**ledger["clips"], **ledger["blocked"]}
            partial = len(ready) >= 5 and any(c["id"] in used and c["views"] > ready[4]["views"] for c in clips)
            plan = twitch.plan_ranking(name, login, days, ready, words, partial=partial) if len(ready) >= 5 else None
            if plan:
                break
            print(f"[skip] {name}: fewer than 5 clips with a usable label in the last {days} days")
        if not plan:
            continue
        out = find.OUT / today.isoformat() / f"twitch-{slug(login)}-{days}d"
        plan["captions"] = s.get("captions", True) is not False
        render.make_ranking(out, plan, {k: [{**w, "w": twitch.mask(w["w"])} for w in v] for k, v in words.items()}, cfg)
        print(f"[done] {out}  {plan['upload_title']}")
        for it in plan["items"]:
            print(f"       {it['rank']}. {it['label']}  ({it['views']:,} views, {it['end'] - it['start']:.1f}s)  {it['source_url']}")
        vid = None
        if a.upload:
            desc, tags = twitch_meta(plan, login, days)
            vid = upload.upload(out / "option1.mp4", plan["upload_title"], desc, tags, privacy=a.privacy)
            print(f"[up]   https://youtube.com/shorts/{vid}  {plan['upload_title']}")
        if vid and a.privacy == "public":          # test uploads do not use up the clips
            for it in plan["items"]:
                ledger["clips"][it["video_id"]] = {"date": today.isoformat(), "login": login, "video": vid}
            ledger.setdefault("rankings", []).append({"date": today.isoformat(), "login": login, "days": days, "video": vid,
                                                      "title": plan["upload_title"],
                                                      "clips": [it["video_id"] for it in plan["items"]]})
            twitch.save_ledger(ledger)
        return 0
    print("nothing to make today")
    return 0


def cmd_check(a) -> int:
    """Health check of everything the unattended run depends on. One line per item."""
    ok = True

    def step(name: str, fn) -> None:
        nonlocal ok
        try:
            print(f"[ok]   {name}: {fn()}")
        except Exception as exc:
            ok = False
            print(f"[FAIL] {name}: {str(exc)[-200:]}")

    step("ffmpeg", lambda: subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=30).stdout.splitlines()[0][:40])
    step("YouTube sign-in", lambda: upload.whoami()["title"])
    step("Claude CLI", lambda: find.ask_claude('Return {"ok": true}', timeout=180))
    step("YouTube listing", lambda: find.latest_videos("https://www.youtube.com/@MrBeast", 1)[0])
    return 0 if ok else 1


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
    r = sub.add_parser("rank")
    r.add_argument("--creator", default="", help="one name, or several separated by commas")
    r.add_argument("--redo", default="", help="re-cut and re-render an existing ranking folder (same entries, new cut points)")
    r.add_argument("--keep-cuts", action="store_true", help="with --redo: use the start/end times in info.json as they are")
    r.add_argument("--videos", type=int, default=6, help="source videos to read")
    r.add_argument("--max-age", type=int, default=150, help="oldest source video, in days")
    r.add_argument("--upload", action="store_true")
    r.add_argument("--privacy", default="public", choices=["public", "unlisted", "private"])
    r.set_defaults(fn=cmd_rank)
    po = sub.add_parser("post")
    po.add_argument("folder", help="clip folder name under out/<date>/")
    po.add_argument("--option", type=int, default=1, help="which optionN.mp4 to upload")
    po.add_argument("--privacy", default="public", choices=["public", "unlisted", "private"])
    po.set_defaults(fn=cmd_post)
    au = sub.add_parser("auto")
    au.add_argument("--rankings", type=int, default=1, help="ranking videos per run")
    au.add_argument("--clips", type=int, default=2, help="single clips per run")
    au.add_argument("--gap", type=float, default=4.0, help="hours between the run's posts")
    au.add_argument("--videos", type=int, default=7, help="source videos read for a ranking")
    au.add_argument("--max-age", type=int, default=150)
    au.add_argument("--force", action="store_true", help="run even if the last run was under 12 hours ago")
    au.set_defaults(fn=cmd_auto)
    tw = sub.add_parser("twitch")
    tw.add_argument("--streamer", default="", help="Twitch login or name (default: the verified streamer ranked least recently)")
    tw.add_argument("--every", type=int, default=6, help="days between two rankings of the same streamer")
    tw.add_argument("--min-views", type=int, default=10000,
                    help="ignore clips with fewer views (a streamer's min_views in watchlist.yml overrides it)")
    tw.add_argument("--include-unverified", action="store_true")
    tw.add_argument("--force", action="store_true", help="ignore the days-between rule")
    tw.add_argument("--upload", action="store_true")
    tw.add_argument("--privacy", default="public", choices=["public", "unlisted", "private"])
    tw.set_defaults(fn=cmd_twitch)
    ch = sub.add_parser("check")
    ch.set_defaults(fn=cmd_check)
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
