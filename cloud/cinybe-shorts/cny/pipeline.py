"""Orchestration, recap-channel style: the next queued film whose file is in the store — or a public-domain print
the run fetches from the Internet Archive itself — → its subtitles become the transcript (OpenSubtitles when the
file has none, faster-whisper last) → Wikipedia plot as optional reference → acts with their time
ranges + the narration (Claude) → shots cut from the film, in order, before any narration is spent → voice →
long recap + Shorts → upload → ledger.

Gian, 2026-11-08: "If it is not from the movie itself, do not post anything." There is no stock fallback: a queued
film without a file is passed over (no ledger row, so it is tried again next run); a film whose file yields no
shots fails the run before the voice is spent. Each stage writes into ``work/<run-id>/`` and is skipped when its
output exists, so a re-dispatched run resumes. The ledger is written only after a successful upload."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from sof import ledger, music, tts, upload
from sof import render as sof_render
from sof import shorts as sof_shorts

from cny import align, cut, films, meta as meta_mod, plot as plot_mod, planner, render, seo, store, subs, thumbnail, visuals
from cny.config import Config

log = logging.getLogger("cny.pipeline")
EXIT_OK, EXIT_FAIL, EXIT_PREFLIGHT, EXIT_QUEUE, EXIT_PLAN, EXIT_NO_FILM = 0, 1, 2, 3, 4, 5


def _stage(name: str) -> None:
    log.info("── %s", name)
    print(f"── {name}", flush=True)


def _run_url() -> str:
    srv, repo, rid = (os.environ.get(k, "") for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{srv}/{repo}/actions/runs/{rid}" if rid else ""


def pick_film(cfg: Config, rows: list[dict], keys: list[str]) -> tuple[films.Film, store.FilmFiles] | None:
    """The first queued film the run can have: its file is in the store (``keys``), or it is an ``archive:`` film
    the run downloads itself. None when no queued film has either."""
    all_films = films.load_films(cfg.films_path)
    cands = [films.find_film(all_films, cfg.film, cfg.year)] if cfg.film else films.next_films(all_films, rows, limit=cfg.max_candidates)
    missing: list[str] = []
    for f in cands:
        files = store.match_keys(f.slug, keys)
        if not files.video_key and f.archive:
            files = store.archive_files(f.slug, f.archive)
        if files.video_key:
            if missing:
                print("queued, no file in the store yet: " + "; ".join(missing[:8]), flush=True)
            return f, files
        missing.append(f.label)
    if missing:
        print("queued, no file in the store yet: " + "; ".join(missing[:8]) + (" …" if len(missing) > 8 else ""), flush=True)
    return None


def give_up(cfg: Config, film: films.Film, files: store.FilmFiles, *, today: str, reason: str) -> int:
    """A dead end (too few cues, no shots, planning failed). An Archive film gets a ``kind: skipped`` ledger row so
    the queue moves on instead of downloading it again tomorrow; Gian's own file stays queued (he fixes the file,
    the next run tries again). Always EXIT_NO_FILM: nothing was posted."""
    print(f"✗ {reason}; nothing posted", flush=True)
    if files.source == "archive" and not cfg.film and not cfg.is_smoke:
        entry = {"date": today, "kind": "skipped", "film": film.title, "year": film.year, "theme": film.theme, "topic": film.label,
                 "video_id": f"skipped-{film.slug}-{today}", "reason": reason, "film_file": files.video_key, "run_url": _run_url()}
        ledger.append_entry(cfg.ledger_path, entry)
        if os.environ.get("GITHUB_ACTIONS"):
            ledger.git_commit_and_push(cfg.ledger_path, entry)
        print(f"ledger: {film.label} skipped ({reason}); remove the row to retry", flush=True)
    return EXIT_NO_FILM


def reference_plot(cfg: Config, film: films.Film, work: Path) -> tuple[meta_mod.MovieMeta, plot_mod.PlotText | None]:
    """Metadata and, when Wikipedia has a usable plot, that plot as reference for the planner (cached in ``work``)."""
    mp = work / "meta.json"
    if mp.exists():
        meta = meta_mod.MovieMeta.from_dict(json.loads(mp.read_text(encoding="utf-8")))
    else:
        meta = meta_mod.resolve(film.title, film.year)
        mp.write_text(json.dumps(meta.to_dict(), indent=1, ensure_ascii=False), encoding="utf-8")
    pp = work / "plot.json"
    if pp.exists():
        d = json.loads(pp.read_text(encoding="utf-8"))
        return meta, (plot_mod.PlotText(**d) if d else None)
    plot = plot_mod.operator_plot(cfg.plots_dir, film.slug, title=film.title)
    if plot is None:
        try:
            titles = ([meta.wikipedia_title] if meta.wikipedia_title else []) + plot_mod.candidate_titles(film.title, film.year)
            plot = plot_mod.WikiPlot().fetch_any(titles)
        except Exception as exc:  # noqa: BLE001 — reference only
            log.warning("wikipedia unavailable: %s", exc)
            plot = None
    if plot is not None and plot.word_count < cfg.min_plot_words:
        plot = None
    pp.write_text(json.dumps(plot.__dict__ if plot else {}, indent=1, ensure_ascii=False), encoding="utf-8")
    return meta, plot


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
    print(f"voice: {tts.voice_label(cfg.sof)}", flush=True)

    _stage("film")
    keys: list[str] = []
    if store.configured(cfg):
        try:
            keys = store.list_keys(cfg)
            print(f"store {store.label(cfg)}: {len(keys)} files", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"! cannot list {store.label(cfg)}: {str(exc)[:200]} — only archive films can run", flush=True)
    else:
        print("no film store configured (GDRIVE_SERVICE_ACCOUNT_JSON or R2_*): only archive films can run", flush=True)
    picked = pick_film(cfg, rows, keys)
    if picked is None:
        print("no queued film has a file (store or archive); nothing posted — upload <slug>.mkv + <slug>.srt to the store", flush=True)
        return EXIT_NO_FILM
    film, files = picked
    work = Path(cfg.work_dir) / f"{today}-{film.slug}"
    work.mkdir(parents=True, exist_ok=True)
    film_files = store.fetch_film(cfg, files, work)
    if not film_files or not film_files.video_path:
        return give_up(cfg, film, files, today=today, reason=f"could not download {files.video_key}")
    duration_film = cut.duration_seconds(film_files.video_path)
    print(f"film: {film.label} · {film_files.video_key} ({film_files.size / 1e9:.2f} GB, {duration_film / 60:.0f} min)"
          + (f" + {film_files.srt_key}" if film_files.srt_key else " (no .srt)"), flush=True)

    _stage("transcript")
    cues: list[align.Cue] = []
    how = "srt"
    # timed to THIS file first (the act ranges become shot ranges, so drift puts shots in the wrong scenes):
    # own human .srt → OpenSubtitles hash match → the Archive's ASR .srt made from this file → whisper on the file
    # → an OpenSubtitles title match only when everything else came up short
    own_srt = film_files.srt_path if film_files.srt_path and Path(film_files.srt_path).exists() else None
    if own_srt and not film_files.srt_asr:
        cues = align.parse_srt(Path(own_srt).read_text(encoding="utf-8", errors="replace"))
    if not cues and subs.configured(cfg):
        got = subs.fetch_srt(cfg, title=film.title, year=film.year, video_path=film_files.video_path,
                             dest=str(work / "film" / "opensubtitles.srt"), mode="hash")
        if got:
            cues = align.parse_srt(Path(got).read_text(encoding="utf-8", errors="replace"))
            how = "opensubtitles-hash"
    if not cues and own_srt:                                    # the Archive's own machine transcript of this file
        cues = align.parse_srt(Path(own_srt).read_text(encoding="utf-8", errors="replace"))
        how = "archive-asr"
    if not cues:
        print("transcribing on the CPU (faster-whisper)…", flush=True)
        cues = align.transcribe(film_files.video_path, work, model=cfg.whisper_model)
        how = "whisper"
    if len(cues) < cfg.min_cues and subs.configured(cfg):
        got = subs.fetch_srt(cfg, title=film.title, year=film.year, video_path=film_files.video_path,
                             dest=str(work / "film" / "opensubtitles-title.srt"), mode="title")
        if got:
            more = align.parse_srt(Path(got).read_text(encoding="utf-8", errors="replace"))
            if len(more) > len(cues):
                cues, how = more, "opensubtitles-title"
    if len(cues) < cfg.min_cues:
        return give_up(cfg, film, film_files, today=today, reason=f"only {len(cues)} dialogue cues ({how}); the recap is written from the dialogue")
    transcript = align.transcript_lines(cues, max_chars=cfg.transcript_max_chars)
    (work / "transcript.txt").write_text(transcript, encoding="utf-8")
    print(f"transcript: {len(cues)} cues ({how}) · {len(transcript) // 1000}k chars", flush=True)
    meta, plot = reference_plot(cfg, film, work)
    print(f"reference: {meta.source} metadata" + (f" · Wikipedia plot {plot.word_count} words" if plot else " · no plot reference"), flush=True)

    _stage("plan")
    plan_path = work / "plan.json"
    if plan_path.exists():
        plan = planner.FilmPlan.from_dict(json.loads(plan_path.read_text(encoding="utf-8")))
    else:
        try:
            plan = planner.plan_film(llm, title=film.title, year=film.year or meta.year, transcript=transcript, duration=duration_film,
                                     plot_text=plot_mod.trim_plot(plot.text, cfg.max_plot_chars) if plot else "",
                                     target_words=cfg.target_words, acts_range=cfg.acts_range,
                                     shorts_per_film=cfg.shorts_per_film if shorts_enabled else 1,
                                     short_max_words=cfg.short_max_words, noslop_rounds=cfg.noslop_rounds,
                                     fast_model=cfg.sof.fast_model)
        except planner.PlanError as exc:
            if film_files.source == "archive":
                return give_up(cfg, film, film_files, today=today, reason=f"planning failed: {str(exc)[:200]}")
            print(f"✗ planning failed: {exc}", flush=True)
            return EXIT_PLAN
        plan_path.write_text(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    sections = plan.sections
    words = sum(len(s["body"].split()) for s in sections)
    print(f"plan: {len(plan.acts)} acts · {words} words · {len(plan.shorts)} shorts · \"{plan.long_form.get('title')}\"", flush=True)
    if plan.slop:
        print("noslop: " + " · ".join(f"{k} {v['before']}→{v['after']} ({v['rewritten']} rewritten)" for k, v in plan.slop.items()), flush=True)

    _stage("film shots")                      # before any narration is spent: a bad file must cost nothing
    vis_plan = visuals.film_visuals(cfg, acts=plan.acts, sections=sections, work=work, video=film_files.video_path,
                                    vertical_for={int(s["act_index"]) for s in (plan.shorts if shorts_enabled else [])})
    n_clips = sum(len(v.get("clips") or []) for v in vis_plan.values())
    print(f"shots: {n_clips} over {len(plan.acts)} acts · {sum(1 for v in vis_plan.values() if v.get('borrowed'))} borrowed", flush=True)
    if n_clips == 0 or any(not v.get("clips") for v in vis_plan.values()):
        return give_up(cfg, film, film_files, today=today, reason="the film yielded no usable shots for every act")

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

    _stage("render")
    final = work / "final.mp4"
    if not final.exists():
        reel = work / "reel.mp4"
        if not reel.exists():
            render.build_long(cfg, work=work, sections=sections, section_seconds=sec_secs, visuals=vis_plan)
        sof_render.assemble(str(reel), str(soundtrack), str(final), audio_duration=duration, fps=cfg.fps)
        reel.unlink(missing_ok=True)
    thumb = work / "thumb.jpg"
    if not thumb.exists():
        hook_act = int(plan.shorts[0]["act_index"]) if plan.shorts else 0
        cands = (vis_plan.get(hook_act) or {}).get("clips") or next((v["clips"] for v in vis_plan.values() if v.get("clips")), [])
        base = thumbnail.grab_frame(cands[len(cands) // 2], str(work / "thumb_base.jpg"), at=1.0) if cands else None
        thumbnail.compose(base=base, out=str(thumb), hook=str(plan.long_form.get("thumb_text") or ""))
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
                                       clips=list(v.get("clips_v") or v.get("clips") or []))
                except Exception as exc:  # noqa: BLE001 — a Short is a bonus; never lose the recap over it
                    log.exception("short %d failed", k + 1)
                    print(f"! short {k + 1} skipped ({exc})", flush=True)
                    continue
            short_files.append(str(out))
            print(f"short {k + 1}: {tts.duration_seconds(audio):.0f} s · \"{s['title']}\"", flush=True)
    if Path(film_files.video_path).exists():
        Path(film_files.video_path).unlink(missing_ok=True)        # 2-4 GB on a small runner disk; the shots are cut

    if cfg.skip_upload:
        print(f"skip-upload set; done in {(time.time() - t0) / 60:.1f} min", flush=True)
        return EXIT_OK

    _stage("upload")
    title = seo.long_title(str(plan.long_form.get("title") or ""), fallback=f"{film.title} explained")
    footage = "public-domain" if film_files.source == "archive" else "film"
    description = seo.long_description(film=film.title, year=film.year or meta.year, blurb=str(plan.long_form.get("description") or ""),
                                       chapters=chapters, director=meta.director, theme=film.theme, plot_url=plot.url if plot else "",
                                       footage=footage, archive=film_files.archive)
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
                                                                     hook=str(s.get("hook") or ""), tags=list(s.get("tags") or []), footage=footage),
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
        "acts": len(plan.acts), "transcript": how, "cues": len(cues), "plot_reference": bool(plot),
        "shots": n_clips, "film_file": film_files.video_key, "source": film_files.source, "shorts": short_entries, "slop": plan.slop or None,
        "llm_calls": dict(getattr(llm, "stats", {}) or {}), "voice": tts.voice_label(cfg.sof), "run_url": _run_url(),
    }
    if cfg.is_smoke:
        print(f"smoke run uploaded as https://youtu.be/{video_id} (unlisted); ledger not written", flush=True)
        return EXIT_OK
    ledger.append_entry(cfg.ledger_path, entry)
    if os.environ.get("GITHUB_ACTIONS"):
        ledger.git_commit_and_push(cfg.ledger_path, entry)
    if cfg.delete_after and store.delete_film(cfg, film_files):
        print(f"removed {film_files.video_key} from the store (published)", flush=True)
    print(f"✓ {film.label} → https://youtu.be/{video_id} · {(time.time() - t0) / 60:.1f} min total", flush=True)
    return EXIT_OK
