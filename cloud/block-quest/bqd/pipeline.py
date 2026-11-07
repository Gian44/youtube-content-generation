"""Orchestration: draw categories → write the day's stories → voice → b-roll → render → upload on a drip → ledger.

Resumable: ``work/<date>/`` keeps stories.json, the audio, footage and renders; the ledger is written only after
the uploads (one row per day with every Short)."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from cc import broll, segments
from sof import ledger, tts, upload, vision
from sof import render as sof_render
from sof import shorts as sof_shorts

from bqd import seo, stories
from bqd.config import Config

log = logging.getLogger("bqd.pipeline")
EXIT_OK, EXIT_FAIL, EXIT_PREFLIGHT, EXIT_WRITER = 0, 1, 2, 4


def _stage(name: str) -> None:
    log.info("── %s", name)
    print(f"── {name}", flush=True)


def _run_url() -> str:
    srv, repo, rid = (os.environ.get(k, "") for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{srv}/{repo}/actions/runs/{rid}" if rid else ""


def write_stories(cfg: Config, llm, rows: list[dict], *, today: str, work: Path) -> list[stories.Story]:
    path = work / "stories.json"
    if path.exists():
        return [stories.Story.from_dict(d) for d in json.loads(path.read_text(encoding="utf-8"))]
    cats, personas = stories.load_topics(cfg.topics_path)
    avoid = stories.recent_categories(rows, days=cfg.avoid_recent_days)
    picks = stories.draw_categories(cats, count=cfg.count, avoid=avoid, seed=today, force=cfg.category)
    used = stories.recent_hooks(rows, limit=cfg.recent_hooks)
    out: list[stories.Story] = []
    for cat in picks:
        try:
            s = stories.write_story(llm, cat, persona_note=personas.get(cat.persona, ""), used_hooks=used + [x.hook for x in out],
                                    words_min=cfg.words_min, words_max=cfg.words_max, words_target=cfg.words_target,
                                    noslop_rounds=cfg.noslop_rounds, fast_model=cfg.sof.fast_model)
        except stories.StoryError as exc:
            print(f"! {cat.key}: {exc}", flush=True)
            continue
        out.append(s)
        print(f"story {len(out)}: [{cat.key}/{cat.persona}] {s.words} words · \"{s.title}\""
              + (f" · noslop {s.slop.get('before')}→{s.slop.get('after')}" if s.slop else ""), flush=True)
    if out:
        path.write_text(stories.dump(out), encoding="utf-8")
    return out


def run(cfg: Config) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", stream=sys.stdout)
    t0 = time.time()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = ledger.load_ledger(cfg.ledger_path)
    if not cfg.smoke and not cfg.skip_upload and any(str(r.get("date")) == today and r.get("kind") == "shorts" for r in rows):
        print(f"already published today's Shorts ({today} UTC); nothing to do", flush=True)
        return EXIT_OK

    yt = None
    if not cfg.skip_upload:
        _stage("preflight: youtube")
        try:
            yt = upload.build_client(cfg.sof)
            print(f"connected as {upload.health(yt)}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"✗ YouTube preflight failed: {exc}", flush=True)
            return EXIT_PREFLIGHT

    llm = cfg.sof.make_llm()
    why_not = llm.check_claude() if cfg.sof.claude_enabled else "not configured"
    print("writer: " + (f"Claude ({cfg.sof.claude_writer_model}/{cfg.sof.claude_fast_model}) on the subscription"
                        if not why_not else f"OpenAI/Gemini (Claude: {why_not})"), flush=True)
    vis = vision.Vision.from_config(cfg.sof)
    print(f"footage checks: {vis.describe()}", flush=True)
    print(f"voice: {tts.voice_label(cfg.sof)}", flush=True)

    work = Path(cfg.work_dir) / (today + ("-smoke" if cfg.smoke else ""))
    work.mkdir(parents=True, exist_ok=True)

    _stage("stories")
    tales = write_stories(cfg, llm, rows, today=today, work=work)
    if not tales:
        print("✗ no story survived validation", flush=True)
        return EXIT_WRITER

    _stage("tts")
    audio: list[str] = []
    for k, s in enumerate(tales):
        raw = tts.speak([s.body], str(work / f"s{k}" / "tts"), cfg.sof, short=True)[0]
        out = work / f"short{k + 1}.mp3"
        if not out.exists():
            sof_render.run(sof_shorts.loudnorm_cmd(raw, str(out), tts.post_filter(cfg.sof)), 600)
        audio.append(str(out))
        print(f"short {k + 1}: {tts.duration_seconds(str(out)):.0f} s of narration", flush=True)

    _stage("footage")
    visuals: list[dict] = []
    seen: set[str] = set()
    for k, s in enumerate(tales):
        d = work / f"s{k}" / "footage"
        clips = broll.footage_for(queries=s.broll_queries, summary=s.hook + " " + s.body[len(s.hook):len(s.hook) + 200],
                                  out_dir=str(d), vis=vis, pexels_key=cfg.sof.pexels_api_key, pixabay_key=cfg.sof.pixabay_api_key,
                                  want=cfg.footage_per_story, candidates=cfg.footage_candidates, budget=cfg.footage_budget,
                                  exclude_ids=seen, kind="first-person story")
        seen |= broll.used_ids(str(d))
        photos = [] if clips else broll.photos_for(queries=s.broll_queries, out_dir=str(work / f"s{k}" / "photos"),
                                                   pexels_key=cfg.sof.pexels_api_key, pixabay_key=cfg.sof.pixabay_api_key,
                                                   want=cfg.photos_per_story)
        visuals.append({"clips": clips, "photos": photos})
        print(f"short {k + 1}: {len(clips)} clips" + (f", {len(photos)} photos" if photos else "") + f" · {s.broll_queries[0]}", flush=True)

    _stage("render")
    files: list[str] = []
    for k, (s, a, v) in enumerate(zip(tales, audio, visuals)):
        name = f"short{k + 1}"
        out = work / f"{name}.mp4"
        if not out.exists():
            card = None
            if not v["clips"] and not v["photos"]:
                card = segments.title_card_png(str(work / f"s{k}" / "card.png"),       # the title band already names it
                                               title=seo.CATEGORY_LABELS.get(s.category, s.category.replace("_", " ")),
                                               kicker="TODAY'S STORY", footer=cfg.brand, width=cfg.width, height=cfg.height)
            try:
                segments.build_vertical_short(work=work, name=name, text=s.body, title=s.title, brand=cfg.brand, audio=a,
                                              clips=v["clips"], photos=v["photos"], width=cfg.width, height=cfg.height, fps=cfg.fps,
                                              clip_seconds=cfg.clip_seconds, slow=cfg.clip_slow, hold_card=card)
            except Exception as exc:  # noqa: BLE001 — one bad render must not cost the other Shorts
                log.exception("short %d failed", k + 1)
                print(f"! short {k + 1} render failed ({exc})", flush=True)
                files.append("")
                continue
        files.append(str(out))
        try:
            sof_render.sample_frames(str(out), str(work / "frames"), [2.0, tts.duration_seconds(a) / 2])
        except Exception as exc:  # noqa: BLE001
            log.warning("frames skipped: %s", exc)
    if not any(files):
        print("✗ nothing rendered", flush=True)
        return EXIT_FAIL
    if cfg.skip_upload:
        print(f"skip-upload set; done in {(time.time() - t0) / 60:.1f} min", flush=True)
        return EXIT_OK

    _stage("upload")
    now = datetime.now(timezone.utc)
    entries: list[dict] = []
    k_live = 0
    for k, (s, path) in enumerate(zip(tales, files)):
        if not path:
            continue
        at = seo.publish_at(k_live, spacing_hours=cfg.spacing_hours, now=now) if cfg.privacy == "public" and not cfg.smoke else None
        body = seo.video_body(title=seo.short_title(s.title, s.category),
                              description=seo.short_description(hook=s.hook, category=s.category, tags=s.tags),
                              tags=seo.short_tags(s.category, s.tags), privacy=cfg.privacy, publish_at=at)
        try:
            vid = upload.upload_video(yt, path, body, None)
            state, reason = upload.wait_until_accepted(yt, vid, timeout=cfg.upload_check_seconds) if k_live == 0 else ("unchecked", "")
        except Exception as exc:  # noqa: BLE001
            log.exception("upload failed")
            print(f"! short {k + 1} upload failed ({exc})", flush=True)
            continue
        if state in upload.BAD_STATES:
            print(f"✗ YouTube did not accept short {k + 1}: {state} ({reason})", flush=True)
            continue
        entries.append({"video_id": vid, "title": body["snippet"]["title"], "category": s.category, "persona": s.persona, "hook": s.hook,
                        "words": s.words, "seconds": round(tts.duration_seconds(audio[k])), "publish_at": at,
                        "slop": s.slop or None})
        print(f"short {k + 1} uploaded https://youtu.be/{vid}" + (f" · live at {at}" if at else f" · {cfg.privacy}"), flush=True)
        k_live += 1
    if not entries:
        return EXIT_FAIL
    entry = {"date": today, "kind": "shorts", "topic": f"{len(entries)} shorts: " + ", ".join(e["category"] for e in entries),
             "video_id": entries[0]["video_id"], "stories": entries, "voice": tts.voice_label(cfg.sof),
             "llm_calls": dict(getattr(llm, "stats", {}) or {}), "vision_calls": dict(vis.stats), "run_url": _run_url()}
    if cfg.smoke:
        print(f"smoke run: {len(entries)} unlisted Short(s); ledger not written", flush=True)
        return EXIT_OK
    ledger.append_entry(cfg.ledger_path, entry)
    if os.environ.get("GITHUB_ACTIONS"):
        ledger.git_commit_and_push(cfg.ledger_path, entry)
    print(f"✓ {len(entries)} Shorts · {(time.time() - t0) / 60:.1f} min total", flush=True)
    return EXIT_OK
