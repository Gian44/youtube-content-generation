"""Orchestration: film → metadata + plot → plan → narration → b-roll → long recap + Shorts → upload → ledger.

Each stage writes into ``work/<run-id>/`` and is skipped when its output exists, so a re-dispatched run on the
same checkout resumes. The ledger is written only after a successful upload (skipped films are recorded so the
queue moves on)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from sof import ledger, music, tts, upload, vision
from sof import render as sof_render
from sof import shorts as sof_shorts

from cny import films, meta as meta_mod, plot as plot_mod, planner, render, seo, thumbnail, visuals
from cny.config import Config

log = logging.getLogger("cny.pipeline")
EXIT_OK, EXIT_FAIL, EXIT_PREFLIGHT, EXIT_QUEUE, EXIT_PLAN = 0, 1, 2, 3, 4


def _stage(name: str) -> None:
    log.info("── %s", name)
    print(f"── {name}", flush=True)


def _run_url() -> str:
    srv, repo, rid = (os.environ.get(k, "") for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{srv}/{repo}/actions/runs/{rid}" if rid else ""


def _plot_overrides(cfg: Config) -> set[str]:
    d = Path(cfg.plots_dir)
    return {p.stem for p in d.glob("*.md")} if d.exists() else set()


def pick_film(cfg: Config, rows: list[dict], work_root: Path, today: str) -> tuple[tuple | None, list[dict]]:
    """(film, meta, plot, work) for the first candidate with a usable plot, plus ledger rows for the skipped ones."""
    all_films = films.load_films(cfg.films_path)
    if cfg.film:
        cands = [films.find_film(all_films, cfg.film, cfg.year)]
    else:
        cands = films.next_films(all_films, rows, limit=cfg.max_tries, retry_skipped=_plot_overrides(cfg))
    skipped: list[dict] = []
    for film in cands:
        work = work_root / f"{today}-{film.slug}"
        work.mkdir(parents=True, exist_ok=True)
        mp = work / "meta.json"
        if mp.exists():
            meta = meta_mod.MovieMeta.from_dict(json.loads(mp.read_text(encoding="utf-8")))
        else:
            meta = meta_mod.resolve(film.title, film.year)
            mp.write_text(json.dumps(meta.to_dict(), indent=1, ensure_ascii=False), encoding="utf-8")
        pp = work / "plot.json"
        if pp.exists():
            plot = plot_mod.PlotText(**json.loads(pp.read_text(encoding="utf-8")))
        else:
            plot = plot_mod.operator_plot(cfg.plots_dir, film.slug, title=film.title)
            if plot is None:
                titles = ([meta.wikipedia_title] if meta.wikipedia_title else []) + plot_mod.candidate_titles(film.title, film.year)
                plot = plot_mod.WikiPlot().fetch_any(titles)
            if plot is not None:
                pp.write_text(json.dumps(plot.__dict__, indent=1, ensure_ascii=False), encoding="utf-8")
        words = plot.word_count if plot else 0
        if plot and words >= cfg.min_plot_words:
            print(f"film: {film.label} · {meta.source} metadata · plot {words} words from {plot.source}"
                  + (f" ({plot.url})" if plot.url else ""), flush=True)
            return (film, meta, plot, work), skipped
        reason = f"plot too thin ({words} words; need {cfg.min_plot_words}) — add plots/{film.slug}.md" if plot else \
                 "no Wikipedia plot section found — add plots/" + film.slug + ".md"
        print(f"! skipping {film.label}: {reason}", flush=True)
        skipped.append({"date": today, "kind": "skipped", "film": film.title, "year": film.year, "theme": film.theme,
                        "topic": film.label, "reason": reason})
    return None, skipped


def run(cfg: Config, *, shorts_enabled: bool = True) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", stream=sys.stdout)
    t0 = time.time()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = ledger.load_ledger(cfg.ledger_path)
    live = [r for r in rows if r.get("kind", "video") == "video" and r.get("video_id")]

    if not cfg.is_smoke and not cfg.film and not cfg.skip_upload:
        why = films.ran_recently(live, gap_hours=cfg.gap_hours)
        if why:
            print(f"{why}; nothing to do", flush=True)
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

    _stage("film")
    picked, skipped = pick_film(cfg, rows, Path(cfg.work_dir), today)
    if picked is None:
        if skipped and not cfg.is_smoke and not cfg.skip_upload:
            for r in skipped:
                ledger.append_entry(cfg.ledger_path, r)
            if os.environ.get("GITHUB_ACTIONS"):
                ledger.git_commit_and_push(cfg.ledger_path, {**skipped[-1], "video_id": "skipped"})
        print("no film with a usable plot in the queue; add films to films.yml or synopses to plots/", flush=True)
        return EXIT_QUEUE
    film, meta, plot, work = picked
    plot_text = plot_mod.trim_plot(plot.text, cfg.max_plot_chars)

    _stage("plan")
    plan_path = work / "plan.json"
    if plan_path.exists():
        plan = planner.FilmPlan.from_dict(json.loads(plan_path.read_text(encoding="utf-8")))
    else:
        try:
            plan = planner.plan_film(llm, title=film.title, year=film.year or meta.year, plot_text=plot_text, lead=plot.lead,
                                     meta=meta.to_dict(), target_words=cfg.target_words, acts_range=cfg.acts_range,
                                     shorts_per_film=cfg.shorts_per_film if shorts_enabled else 1,
                                     short_max_words=cfg.short_max_words, noslop_rounds=cfg.noslop_rounds,
                                     fast_model=cfg.sof.fast_model)
        except planner.PlanError as exc:
            print(f"✗ planning failed: {exc}", flush=True)
            return EXIT_PLAN
        plan_path.write_text(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    sections = plan.sections
    words = sum(len(s["body"].split()) for s in sections)
    print(f"plan: {len(plan.acts)} acts · {words} words · {len(plan.shorts)} shorts · \"{plan.long_form.get('title')}\"", flush=True)
    if plan.slop:
        print("noslop: " + " · ".join(f"{k} {v['before']}→{v['after']} ({v['rewritten']} rewritten)" for k, v in plan.slop.items()), flush=True)

    _stage("tts")
    narration = work / "narration.mp3"
    chunks, owner = tts.chunk_sections(sections, cfg.sof.tts_chunk_chars)
    parts = tts.speak(chunks, str(work / "tts"), cfg.sof)
    if not narration.exists():
        tts.assemble(parts, str(work), pre=tts.post_filter(cfg.sof))
    duration = tts.duration_seconds(str(narration))
    sec_secs = render.scaled_durations(parts, owner, len(sections), duration)
    chapters = render.chapter_rows(sections, render.section_starts(sec_secs))
    (work / "chapters.json").write_text(json.dumps(chapters, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"narration: {duration / 60:.1f} min · {len(chunks)} chunks · {len(chapters)} chapters", flush=True)
    short_audio: list[str] = []
    for k, s in enumerate(plan.shorts if shorts_enabled else []):
        d = work / "tts_short" / f"s{k}"
        raw = tts.speak([str(s["text"]).strip()], str(d), cfg.sof, short=True)[0]
        out = work / f"short{k + 1}.mp3"
        if not out.exists():
            sof_render.run(sof_shorts.loudnorm_cmd(raw, str(out), tts.post_filter(cfg.sof)), 600)
        short_audio.append(str(out))

    soundtrack = narration
    if cfg.music_enabled:
        _stage("music")
        mixed = work / "soundtrack.mp3"
        if not mixed.exists():
            variant = int(hashlib.sha1(film.slug.encode()).hexdigest(), 16) % 997
            music.mix(str(narration), str(mixed), work_dir=str(work), bed_lufs=cfg.music_lufs, variant=variant)
        soundtrack = mixed

    _stage("footage")
    vis_plan = visuals.gather_all(cfg, acts=plan.acts, work=work, vis=vis)
    n_clips = sum(len(v.get("clips") or []) for v in vis_plan.values())
    print(f"footage: {n_clips} clips over {len(plan.acts)} acts · "
          f"{sum(1 for v in vis_plan.values() if v.get('photos'))} acts on photos · "
          f"{sum(1 for v in vis_plan.values() if v.get('borrowed'))} borrowed", flush=True)

    _stage("render")
    final = work / "final.mp4"
    if not final.exists():
        reel = work / "reel.mp4"
        if not reel.exists():
            render.build_long(cfg, work=work, sections=sections, section_seconds=sec_secs, visuals=vis_plan, film=film.label)
        sof_render.assemble(str(reel), str(soundtrack), str(final), audio_duration=duration, fps=cfg.fps)
        reel.unlink(missing_ok=True)
    thumb = work / "thumb.jpg"
    if not thumb.exists():
        first = next((c for v in (vis_plan.get(i) for i in sorted(vis_plan)) for c in (v or {}).get("clips") or []), None)
        base = thumbnail.grab_frame(first, str(work / "thumb_base.jpg")) if first else None
        if base is None:
            photo = next((p for v in vis_plan.values() for p in v.get("photos") or []), None)
            base = photo
        thumbnail.compose(base=base, out=str(thumb), film=film.title, year=film.year or meta.year,
                          hook=str(plan.long_form.get("thumb_text") or ""))
    try:
        sof_render.sample_frames(str(final), str(work / "frames"), render.frames_to_check(duration))
    except Exception as exc:  # noqa: BLE001
        log.warning("sample frames skipped: %s", exc)
    print(f"video: {final} ({final.stat().st_size / 1e6:.0f} MB)", flush=True)

    short_files: list[str] = []
    if shorts_enabled:
        _stage("shorts")
        for k, (s, audio) in enumerate(zip(plan.shorts, short_audio)):
            name = f"short{k + 1}"
            out = work / f"{name}.mp4"
            if not out.exists():
                v = vis_plan.get(int(s["act_index"]), {})
                try:
                    render.build_short(cfg, work=work, name=name, text=str(s["text"]), title=str(s["title"]), audio=audio,
                                       clips=list(v.get("clips") or []), photos=list(v.get("photos") or []))
                except Exception as exc:  # noqa: BLE001 — a Short is a bonus; never lose the recap over it
                    log.exception("short %d failed", k + 1)
                    print(f"! short {k + 1} skipped ({exc})", flush=True)
                    continue
            short_files.append(str(out))
            print(f"short {k + 1}: {tts.duration_seconds(audio):.0f} s · \"{s['title']}\"", flush=True)

    if cfg.skip_upload:
        print(f"skip-upload set; done in {(time.time() - t0) / 60:.1f} min", flush=True)
        return EXIT_OK

    _stage("upload")
    variant = len(live) % seo.TITLE_VARIANTS
    title = seo.long_title(film.title, film.year or meta.year, minutes=seo.fmt_minutes(duration),
                           writer_title=str(plan.long_form.get("title") or ""), variant=variant)
    description = seo.long_description(film=film.title, year=film.year or meta.year, blurb=str(plan.long_form.get("description") or ""),
                                       chapters=chapters, plot_url=plot.url, director=meta.director, theme=film.theme)
    tags = seo.long_tags(film.title, director=meta.director, genres=meta.genres, cast=meta.cast,
                         extra=list(plan.long_form.get("tags") or []), theme=film.theme)
    body = seo.video_body(title=title, description=description, tags=tags, privacy=cfg.privacy)
    video_id = upload.upload_video(yt, str(final), body, str(thumb))
    try:
        state, reason = upload.wait_until_accepted(yt, video_id, timeout=cfg.upload_check_seconds)
    except Exception as exc:  # noqa: BLE001
        state, reason = "unknown", str(exc)
    if state in upload.BAD_STATES:
        print(f"✗ YouTube did not accept the upload: {state} ({reason or 'no reason given'}) — "
              f"https://studio.youtube.com/video/{video_id}/edit", flush=True)
        return EXIT_FAIL
    uploaded_at = datetime.now(timezone.utc)
    print(f"uploaded https://youtu.be/{video_id}: {title} · {cfg.privacy}", flush=True)
    short_entries: list[dict] = []
    for k, (s, path) in enumerate(zip(plan.shorts, short_files)):
        try:
            at = seo.short_publish_at(None, delay_minutes=cfg.shorts_delay_minutes, index=k, spacing_hours=cfg.shorts_spacing_hours,
                                      now=uploaded_at) if cfg.privacy == "public" and not cfg.is_smoke else None
            sbody = seo.video_body(title=seo.short_title(str(s["title"]), film.title),
                                   description=seo.short_description(film=film.title, year=film.year or meta.year, long_video_id=video_id,
                                                                     hook=str(s.get("hook") or ""), tags=list(s.get("tags") or [])),
                                   tags=seo.short_tags(film.title, list(s.get("tags") or [])), privacy=cfg.privacy, publish_at=at)
            sid = upload.upload_video(yt, path, sbody, None)
            short_entries.append({"video_id": sid, "title": sbody["snippet"]["title"], "seconds": round(tts.duration_seconds(short_audio[k])),
                                  "publish_at": at, "act_index": int(s["act_index"])})
            print(f"short {k + 1} uploaded https://youtu.be/{sid}" + (f" · live at {at}" if at else ""), flush=True)
        except Exception as exc:  # noqa: BLE001
            log.exception("short upload failed")
            print(f"! short {k + 1} upload failed ({exc}); the recap is unaffected", flush=True)
    entry = {
        "date": today, "kind": "video", "film": film.title, "year": film.year or meta.year, "theme": film.theme,
        "topic": film.label, "video_id": video_id, "title": title, "privacy": cfg.privacy,
        "uploaded_at": uploaded_at.isoformat(timespec="seconds"), "duration_seconds": round(duration), "words": words,
        "acts": len(plan.acts), "plot_source": plot.source, "plot_url": plot.url, "plot_words": plot.word_count,
        "footage_clips": n_clips, "shorts": short_entries, "slop": plan.slop or None,
        "llm_calls": dict(getattr(llm, "stats", {}) or {}), "vision_calls": dict(vis.stats), "voice": tts.voice_label(cfg.sof),
        "run_url": _run_url(),
    }
    if cfg.is_smoke:
        print(f"smoke run uploaded as https://youtu.be/{video_id} (unlisted); ledger not written", flush=True)
        return EXIT_OK
    for r in skipped:
        ledger.append_entry(cfg.ledger_path, r)
    ledger.append_entry(cfg.ledger_path, entry)
    if os.environ.get("GITHUB_ACTIONS"):
        ledger.git_commit_and_push(cfg.ledger_path, entry)
    print(f"✓ {film.label} → https://youtu.be/{video_id} · {(time.time() - t0) / 60:.1f} min total", flush=True)
    return EXIT_OK
