"""One run = one long video (kind long) or the day's Shorts (kind short):
question → script → fact check → drawings → voice (one Kokoro clip per scene) → frames → mp4 → upload → ledger.

Resumable: ``work/<date>-<kind>/`` keeps script.json (with drawings), audio and frames; the ledger is written only
after the uploads."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml
from sof import ledger, tts, upload

from dx import scenes, video, writer
from dx.config import BRAND, Config

log = logging.getLogger("dx.pipeline")
EXIT_OK, EXIT_FAIL, EXIT_PREFLIGHT, EXIT_WRITER = 0, 1, 2, 4
START = date(2026, 11, 26)          # the re-launch; long videos on START, START+2, ...


def _say(msg: str) -> None:
    print(msg, flush=True)


def _run_url() -> str:
    srv, repo, rid = (os.environ.get(k, "") for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{srv}/{repo}/actions/runs/{rid}" if rid else ""


RECENT_HOURS = 8


def recent_run(rows: list[dict], kind: str, *, now: datetime) -> bool:
    """True when a row of ``kind`` was written less than RECENT_HOURS ago (rows without a time: same UTC date).
    A time window, not the date: a hand-started run in the evening must not cancel the next morning's slot."""
    for r in rows:
        if r.get("kind") != kind:
            continue
        at = r.get("at")
        if at:
            try:
                if now - datetime.fromisoformat(str(at).replace("Z", "+00:00")) < timedelta(hours=RECENT_HOURS):
                    return True
            except ValueError:
                pass
        elif str(r.get("date")) == str(now.date()):
            return True
    return False


def is_long_day(day: date, every: int) -> bool:
    return (day - START).days % max(1, every) == 0


def used_questions(rows: list[dict]) -> list[str]:
    out = []
    for r in rows:
        for v in r.get("videos") or []:
            out += [v.get("question", ""), v.get("title", "")]
    return [q for q in out if q]


def publish_at(k: int, *, spacing_hours: float, now: datetime) -> str | None:
    if k == 0:
        return None
    return (now + timedelta(hours=spacing_hours * k)).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def chapters(script: writer.Script, starts: list[float]) -> str:
    """YouTube chapters: first at 0:00, at least 3, each 10 s or longer."""
    marks: list[tuple[float, str]] = []
    for i, sc in enumerate(script.scenes):
        head = script.sections[sc.section] if sc.section < len(script.sections) else ""
        if head and (not marks or marks[-1][1] != head) and (not marks or starts[i] - marks[-1][0] >= 10):
            marks.append((0.0 if not marks else starts[i], head))
    if len(marks) < 3:
        return ""
    return "\n".join(f"{int(t // 60)}:{int(t % 60):02d} {h}" for t, h in marks)


def make_one(cfg: Config, llm, *, kind: str, question: str, category: str, work: Path, seed: str) -> tuple[writer.Script, str, float, list[float]]:
    """Script (+drawings) → mp4. Returns (script, mp4 path, seconds, scene start times)."""
    w, h = (1920, 1080) if kind == "long" else (1080, 1920)
    work.mkdir(parents=True, exist_ok=True)
    sp = work / "script.json"
    if sp.exists():
        script = writer.Script.from_dict(json.loads(sp.read_text(encoding="utf-8")))
    else:
        lo, hi = cfg.long_words if kind == "long" else cfg.short_words
        raw = writer.write_script(llm, kind=kind, question=question, words_min=lo, words_max=hi)
        raw = writer.fact_check(llm, raw)
        script = writer.build(kind, question, raw, category)
        _say(f"{kind}: \"{script.title}\" · {script.words} words · {len(script.scenes)} scenes · {raw.get('_fact_fixes', 0)} fact fixes")
        sp.write_text(writer.dump(script), encoding="utf-8")
    if any(not s.svg for s in script.scenes):
        fb = writer.draw_all(llm, script, batch_size=cfg.batch_long if kind == "long" else cfg.batch_short)
        _say(f"drawings done · {fb} fallback scene(s)")
        if kind == "long" and not script.thumb_svg:
            try:
                script.thumb_svg = writer.draw_thumbnail(llm, script)
            except Exception as exc:  # noqa: BLE001
                log.warning("thumbnail drawing failed: %s", exc)
        sp.write_text(writer.dump(script), encoding="utf-8")

    # voice: one clip per scene, so each drawing lasts exactly as long as its words
    clips = tts.speak([sc.say for sc in script.scenes], str(work / "tts"), cfg.sof, short=(kind == "short"))
    gap = cfg.gap_long if kind == "long" else cfg.gap_short
    plan: list[tuple[str, float]] = []
    pads: list[str] = []
    starts: list[float] = []
    t = 0.0
    for i, (sc, mp3) in enumerate(zip(script.scenes, clips)):
        length = tts.duration_seconds(mp3) + gap
        starts.append(t)
        t += length
        try:
            states = scenes.render_scene(sc.svg, work / "frames" / f"{i:03d}", width=w, height=h, seed=f"{seed}|{i}")
        except Exception as exc:  # noqa: BLE001 — a drawing that broke after the check: draw the fallback
            log.warning("scene %d render failed (%s); fallback drawing", i, exc)
            sc.svg = writer.fallback_svg(kind, i)
            states = scenes.render_scene(sc.svg, work / "frames" / f"{i:03d}b", width=w, height=h, seed=f"{seed}|{i}")
        plan += video.frame_plan(states, length)
        pads.append(video.pad_audio(mp3, str(work / "tts" / f"pad{i:03d}.wav"), length))
    frames_list = video.concat_file(plan, str(work / "frames.txt"))
    audio_list = work / "audio.txt"
    audio_list.write_text("ffconcat version 1.0\n" + "".join(f"file '{Path(p).resolve()}'\n" for p in pads), encoding="utf-8")
    out = work / ("video.mp4" if kind == "long" else "short.mp4")
    if not out.exists():
        video.assemble(frames_list=frames_list, audio_list=str(audio_list), out=str(out), width=w, height=h,
                       loudness=-16.0 if kind == "long" else -14.0, pre_filter=tts.post_filter(cfg.sof))
    _say(f"rendered {out.name}: {t / 60:.1f} min" if kind == "long" else f"rendered {out.name}: {t:.0f} s")
    return script, str(out), t, starts


def _thumb(script: writer.Script, work: Path) -> str | None:
    if not script.thumb_svg:
        return None
    try:
        kids = scenes.parse_body(script.thumb_svg)
        p = work / "thumb.png"
        scenes.render_png(scenes.compose(kids, width=1920, height=1080, upto=1.0, variant=0, seed=0), str(p), width=1280, height=720)
        jpg = work / "thumb.jpg"
        from PIL import Image

        Image.open(p).convert("RGB").save(jpg, quality=90)
        return str(jpg)
    except Exception as exc:  # noqa: BLE001
        log.warning("thumbnail render failed: %s", exc)
        return None


def _body(meta: dict, *, description: str, privacy: str, at: str | None, short: bool) -> dict:
    tags = meta["tags"] + ["explained", "stick figure", "animation"]
    body = upload.video_body(title=meta["title"], description=description, tags=tags, privacy=privacy, publish_at=at)
    body["snippet"]["categoryId"] = "27"            # Education
    return body


def run(cfg: Config) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", stream=sys.stdout)
    t0 = time.time()
    now = datetime.now(timezone.utc)
    today = now.date()
    rows = ledger.load_ledger(cfg.ledger_path)
    kind = cfg.kind
    if not cfg.smoke and not cfg.skip_upload and not cfg.force:
        if kind == "long" and not is_long_day(today, cfg.long_every_days):
            _say(f"{today}: not a long-video day (every {cfg.long_every_days} days from {START}); nothing to do")
            return EXIT_OK
        if recent_run(rows, kind, now=now):
            _say(f"a {kind} run already published in the last {RECENT_HOURS} h; nothing to do")
            return EXIT_OK

    yt = None
    if not cfg.skip_upload:
        try:
            yt = upload.build_client(cfg.sof)
            _say(f"connected as {upload.health(yt)}")
        except Exception as exc:  # noqa: BLE001
            _say(f"✗ YouTube preflight failed: {exc}")
            return EXIT_PREFLIGHT

    llm = cfg.sof.make_llm()
    why_not = llm.check_claude() if cfg.sof.claude_enabled else "not configured"
    _say("writer: " + ("Claude on the subscription" if not why_not else f"OpenAI/Gemini (Claude: {why_not})"))
    _say(f"voice: {tts.voice_label(cfg.sof)}")
    topics = yaml.safe_load(Path(cfg.topics_path).read_text(encoding="utf-8"))
    cats = list(topics.get("categories") or [])
    used = used_questions(rows)
    base = Path(cfg.work_dir) / f"{today}-{kind}{'-smoke' if cfg.smoke else ''}"
    n = 1 if kind == "long" else cfg.count
    made: list[dict] = []
    for k in range(n):
        work = base / f"v{k + 1}"
        qp = work / "question.json"
        try:
            if qp.exists():
                q = json.loads(qp.read_text(encoding="utf-8"))
            elif cfg.question and k == 0:
                q = {"question": cfg.question, "category": "forced"}
            else:
                q = writer.pick_question(llm, kind=kind, categories=cats, used=used, seed=f"{today}|{k}",
                                         fast_model=cfg.sof.fast_model)
            work.mkdir(parents=True, exist_ok=True)
            qp.write_text(json.dumps(q), encoding="utf-8")
            used.append(q["question"])
            _say(f"── {kind} {k + 1}/{n}: {q['question']} [{q['category']}]")
            script, path, secs, starts = make_one(cfg, llm, kind=kind, question=q["question"], category=q["category"],
                                                   work=work, seed=f"{today}|{kind}|{k}")
            meta = writer.metadata(llm, script, fast_model=cfg.sof.fast_model)
        except Exception as exc:  # noqa: BLE001 — one failed Short must not cost the other
            log.exception("%s %d failed", kind, k + 1)
            _say(f"! {kind} {k + 1} failed: {exc}")
            continue
        made.append({"q": q, "script": script, "path": path, "secs": secs, "starts": starts, "meta": meta, "work": work})
    if not made:
        _say("✗ nothing made")
        return EXIT_WRITER
    if cfg.skip_upload:
        _say(f"skip-upload set; {len(made)} made in {(time.time() - t0) / 60:.1f} min")
        return EXIT_OK

    entries = []
    for k, m in enumerate(made):
        s: writer.Script = m["script"]
        desc = m["meta"]["description"]
        if kind == "long":
            ch = chapters(s, m["starts"])
            desc = desc + ("\n\n" + ch if ch else "")
        desc += f"\n\nWritten, drawn and narrated for {BRAND}. Animation made with code; narration voice is synthetic."
        at = publish_at(k, spacing_hours=cfg.spacing_hours, now=now) if cfg.privacy == "public" and not cfg.smoke else None
        body = _body(m["meta"], description=desc, privacy=cfg.privacy, at=at, short=kind == "short")
        try:
            vid = upload.upload_video(yt, m["path"], body, _thumb(s, m["work"]) if kind == "long" else None)
            state, reason = upload.wait_until_accepted(yt, vid, timeout=cfg.upload_check_seconds)
        except Exception as exc:  # noqa: BLE001
            log.exception("upload failed")
            _say(f"! upload {k + 1} failed: {exc}")
            continue
        if state in upload.BAD_STATES:
            _say(f"✗ YouTube did not accept {k + 1}: {state} ({reason})")
            continue
        entries.append({"video_id": vid, "title": body["snippet"]["title"], "question": m["q"]["question"],
                        "category": m["q"]["category"], "words": s.words, "scenes": len(s.scenes),
                        "seconds": round(m["secs"]), "publish_at": at})
        _say(f"{kind} {k + 1} uploaded https://youtu.be/{vid}" + (f" · live at {at}" if at else f" · {cfg.privacy}"))
    if not entries:
        return EXIT_FAIL
    entry = {"date": str(today), "at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "kind": kind, "topic": " / ".join(e["title"] for e in entries),
             "video_id": entries[0]["video_id"], "videos": entries, "voice": tts.voice_label(cfg.sof),
             "llm_calls": dict(getattr(llm, "stats", {}) or {}), "run_url": _run_url()}
    if cfg.smoke:
        _say(f"smoke run: {len(entries)} unlisted; ledger not written")
        return EXIT_OK
    ledger.append_entry(cfg.ledger_path, entry)
    if os.environ.get("GITHUB_ACTIONS"):
        ledger.git_commit_and_push(cfg.ledger_path, entry)
    _say(f"✓ {len(entries)} {kind} · {(time.time() - t0) / 60:.1f} min total")
    return EXIT_OK
