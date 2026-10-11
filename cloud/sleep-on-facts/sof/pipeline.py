"""Orchestration: topic → script → tts → images → render → upload → ledger.

Each stage writes into ``work/<run-id>/`` and is skipped when its output already
exists, so a re-dispatched run on the same checkout resumes where it stopped. The
ledger is written only after a successful upload.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from sof import captions, images, ledger, music, render, research, script, seo, thumbnail, topicgen, topics, tts, upload
from sof.config import Config
from sof.llm import LLM
from sof.prompts import STORY_FORMATS, STORY_ORDER

log = logging.getLogger("sof.pipeline")

EXIT_OK, EXIT_FAIL, EXIT_PREFLIGHT, EXIT_IMAGES = 0, 1, 2, 3


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def _stage(name: str):
    log.info("── %s", name)
    print(f"── {name}", flush=True)


def run(cfg: Config) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", stream=sys.stdout)
    t0 = time.time()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # One public video per UTC day: a manual run earlier in the day makes the scheduled one a no-op
    # (forced topics and smoke tests are deliberate and always run).
    if not cfg.is_smoke and not cfg.topic and not cfg.skip_upload and any(
            str(r.get("date")) == today for r in ledger.load_ledger(cfg.ledger_path)):
        print(f"already published a video today ({today} UTC); nothing to do", flush=True)
        return EXIT_OK

    # 0. Preflight: a dead YouTube token must fail before we pay for TTS.
    yt = None
    if not cfg.skip_upload:
        _stage("preflight: youtube")
        try:
            yt = upload.build_client(cfg)
            print(f"connected as {upload.health(yt)}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"✗ YouTube preflight failed: {exc}", flush=True)
            return EXIT_PREFLIGHT
        _check_recent_uploads(yt, cfg)

    # 1. Topic: forced (--topic) → unused seed from topics.yml → generated from the day's category.
    _stage("topic")
    # Rows YouTube abandoned (e.g. the Oct 7 Ancient Egypt upload) do not count as published: their
    # topic can be made again and they do not advance the story-shape / title rotations.
    rows = [r for r in ledger.load_ledger(cfg.ledger_path) if not r.get("abandoned")]
    llm = LLM(cfg.openai_api_key, cfg.gemini_api_key, cfg.script_model, cfg.gemini_models)
    seeds = topics.load_topics(cfg.topics_path)
    category, wiki_title, queries = "", None, []
    if cfg.topic:
        name = cfg.topic.strip()
        seed = next((t for t in seeds if t.name.lower() == name.lower()), None)
        queries = seed.queries if seed else []
    elif (seed := topics.unused_seed(seeds, rows)) is not None:
        name, queries = seed.name, seed.queries
    else:
        pick = topicgen.choose(llm, categories=topics.load_categories(cfg.topics_path), ledger=rows, model=cfg.fast_model)
        name, wiki_title, category = pick.topic, pick.wiki_title, pick.category
    run_id = f"{today}-{_slug(name)}"
    work = Path(cfg.work_dir) / run_id
    work.mkdir(parents=True, exist_ok=True)
    print(f"topic: {name}{' (' + category + ')' if category else ''} · run {run_id} · {cfg.minutes} min target", flush=True)

    # 2. Research: the Wikipedia article + the articles it links to, chunked for retrieval.
    _stage("research")
    corpus_path = work / "corpus.json"
    corpus = None
    if corpus_path.exists():
        corpus = research.Corpus.from_dict(json.loads(corpus_path.read_text(encoding="utf-8")))
    else:
        try:
            corpus = research.build_corpus(wiki_title or name, max_linked=cfg.research_linked_articles)
            corpus_path.write_text(json.dumps(corpus.to_dict(), ensure_ascii=False), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 — research is strongly preferred, not mandatory
            print(f"! research unavailable ({exc}); writing from the lead section only", flush=True)
    if corpus:
        print(f"research: {corpus.title} + {len(corpus.sources) - 1} linked articles · {len(corpus.chunks)} chunks · "
              f"{corpus.chars // 1000}k chars", flush=True)

    # 3. Script.
    _stage("script")
    script_path = work / "script.json"
    if script_path.exists():
        sc = script.Script.from_dict(json.loads(script_path.read_text(encoding="utf-8")))
    else:
        sc = script.generate_script(
            llm, topic=name, corpus=corpus, hints=research.wikipedia_hints(name) if corpus is None else "",
            minutes=cfg.minutes, target_words=cfg.target_words, num_movements=cfg.num_movements,
            max_segments=cfg.max_segments, fill_ratio=cfg.fill_ratio, max_words_ratio=cfg.max_words_ratio,
            segment_max_tokens=cfg.segment_max_tokens, writer_model=cfg.script_model, fast_model=cfg.fast_model,
            story_format=STORY_ORDER[len(rows) % len(STORY_ORDER)],      # rotates one shape per published video
        )
        script_path.write_text(json.dumps(sc.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"script: {sc.word_count} words, {sc.movements} movements · shape: {sc.story_format or 'n/a'}", flush=True)

    # 3. TTS — synthesised movement by movement so chapter start times are exact.
    _stage("tts")
    narration = work / "narration.mp3"
    chapters_path = work / "chapters.json"
    chars = len(sc.body)
    secs = [x for x in sc.sections if str(x.get("body") or "").strip()] or [{"heading": "", "body": sc.body}]
    if not narration.exists() or not chapters_path.exists():
        chunks, owner = tts.chunk_sections(secs, cfg.tts_chunk_chars)
        parts = tts.synthesize(chunks, str(work / "tts"), api_key=cfg.openai_api_key, model=cfg.tts_model,
                               voice=cfg.tts_voice, speed=cfg.tts_speed, instructions=cfg.tts_instructions)
        if not narration.exists():
            tts.assemble(parts, str(work))
        starts = tts.section_starts(parts, owner, len(secs), duration=tts.duration_seconds)
        chapters_path.write_text(json.dumps([{"start": round(st, 2), "title": str(x.get("heading") or "")}
                                             for st, x in zip(starts, secs)], indent=1, ensure_ascii=False), encoding="utf-8")
    chapters = json.loads(chapters_path.read_text(encoding="utf-8"))
    duration = tts.duration_seconds(str(narration))
    hours = max(1, round(duration / 3600))
    print(f"narration: {duration / 60:.1f} min · {len(chapters)} chapters · est. TTS cost "
          f"${tts.estimate_cost_usd(chars, cfg.tts_model):.2f}", flush=True)

    # 3b. Faint ambient bed under the voice (synthesised, licence-free).
    soundtrack = narration
    if cfg.music_enabled:
        _stage("music")
        mixed = work / "soundtrack.mp3"
        if not mixed.exists():
            variant = int(hashlib.sha1(run_id.encode()).hexdigest(), 16) % 997    # a different bed per video
            music.mix(str(narration), str(mixed), work_dir=str(work), bed_lufs=cfg.music_lufs, variant=variant)
        soundtrack = mixed
        print(f"soundtrack: bed at {cfg.music_lufs:g} LUFS under narration", flush=True)

    # 4. Images: Commons (the article's own photos, then its category), NASA for space topics, then
    # name-gated stock; a vision pass labels every download subject / setting / reject.
    _stage("images")
    img_dir = work / "images"
    picked_path = work / "image_labels.json"
    # Never fetch more images than the narration can show (plus a margin); 3 h → the full 150,
    # a 3-minute smoke → ~15, so the reel build stays proportional to the video.
    showable = int(duration // max(1.0, cfg.dwell_seconds)) + 6
    target = max(cfg.images_fail_below, min(cfg.images_target, showable))
    article = corpus.title if corpus else wiki_title
    space = images.is_space_topic(category, corpus.lead if corpus else "")
    seo_category = category or (seo.SPACE_CATEGORY if space else "")
    if picked_path.exists():
        picked = json.loads(picked_path.read_text(encoding="utf-8"))
        labels, image_paths = picked["labels"], picked["reel"]
    else:
        terms = images.name_terms(name, article or "", sc.image_keywords)
        use_nasa = cfg.images_nasa == "on" or (cfg.images_nasa == "auto" and space)
        urls = images.gather_urls(topic=name, wiki_title=article, terms=terms, queries=list(queries) + list(sc.image_queries),
                                  setting_queries=sc.setting_queries, target=target,
                                  pexels_key=cfg.pexels_api_key, pixabay_key=cfg.pixabay_api_key,
                                  use_commons=cfg.images_commons, use_nasa=use_nasa, generic_floor=cfg.images_fail_below)

        def label(paths):
            if not cfg.images_vision_check:
                return {p: "unknown" for p in paths}
            return images.classify(paths, subject=name, api_key=cfg.openai_api_key, model=cfg.images_vision_model)

        if img_dir.exists():                       # a stale partial download from an interrupted run
            for f in img_dir.iterdir():
                f.unlink()
        image_paths, labels = images.gather_reel(urls, str(img_dir), target=target, floor=cfg.images_fail_below,
                                                 classify_fn=label, setting_share=cfg.images_setting_share)
        picked_path.write_text(json.dumps({"terms": terms, "nasa": use_nasa, "labels": labels, "reel": image_paths},
                                          indent=1, ensure_ascii=False), encoding="utf-8")
    if len(image_paths) < cfg.images_fail_below:
        print(f"✗ only {len(image_paths)} images; need at least {cfg.images_fail_below}", flush=True)
        return EXIT_IMAGES
    used = _used_credits(img_dir, image_paths)
    by_source = {s: sum(1 for u in used if u.get("source") == s) for s in ("commons", "nasa", "pexels", "pixabay")}
    print(f"images: {len(image_paths)} · " + ", ".join(f"{k} {v}" for k, v in by_source.items() if v)
          + f" · subject {sum(1 for p in image_paths if labels.get(p) == 'subject')}", flush=True)

    # 5. Render.
    _stage("render")
    final = work / "final.mp4"
    if not final.exists():
        reel = work / "reel.mp4"
        if not reel.exists():
            caps = captions.captions_for(image_paths, used) if cfg.photo_captions else {}
            print(f"on-screen credits: {len(caps)} of {len(image_paths)} photos", flush=True)
            built = render.build_reel(image_paths, str(work / "reel_tmp"), dwell=cfg.dwell_seconds,
                                      crossfade=cfg.crossfade_seconds, width=cfg.width, height=cfg.height,
                                      fps=cfg.fps, batch=cfg.xfade_batch, dim=cfg.video_dim, captions=caps)
            os.replace(built, reel)
        render.assemble(str(reel), str(soundtrack), str(final), audio_duration=duration, fps=cfg.fps)
    thumb = work / "thumb.jpg"
    if not thumb.exists():
        subject_shots = [p for p in image_paths if labels.get(p) == "subject"]
        chooser = None
        if cfg.images_vision_check and cfg.openai_api_key:
            def chooser(paths):
                return images.pick_iconic(paths, subject=name, api_key=cfg.openai_api_key, model=cfg.images_vision_model)
        thumbnail.make(image_paths, topic=name, hours=hours, out=str(thumb), candidates=subject_shots, choose=chooser)
    size_gb = final.stat().st_size / 1e9
    print(f"video: {final} ({size_gb:.2f} GB)", flush=True)
    try:                                   # a dozen stills for checking the run by eye (kept as artifacts)
        step = max(10.0, duration / 12)
        render.sample_frames(str(final), str(work / "frames"), [step * (i + 0.5) for i in range(int(duration // step))])
    except Exception as exc:  # noqa: BLE001
        log.warning("sample frames skipped: %s", exc)

    # 6. Upload (scheduled for the evening slot) + playlist + ledger.
    if cfg.skip_upload:
        print(f"skip-upload set; done in {(time.time() - t0) / 60:.1f} min", flush=True)
        return EXIT_OK
    _stage("upload")
    variant = len(rows) % seo.TITLE_VARIANTS             # a different title shape from one video to the next
    title = seo.build_title(name, seo_category, hours, hook=sc.title_hook, variant=variant)
    shape = STORY_FORMATS.get(sc.story_format or "")
    blurb = (f"Tonight: {shape['opening'].format(subject=name)}. " if shape else "") + (sc.blurb or "")
    description = seo.build_description(subject=name, category=seo_category, hours=hours, blurb=blurb,
                                        chapters=chapters, footer=sc.footer or sc.description,
                                        photos=captions.DESCRIPTION_LINE, extra=_thumbnail_credit(thumb, used))
    tags = seo.build_tags(name, seo_category, sc.tags)
    publish_at = None
    if cfg.schedule_publish and not cfg.is_smoke and cfg.privacy == "public":
        slot = seo.publish_slot(datetime.now(timezone.utc), cfg.publish_hour, cfg.publish_minute, tz=cfg.publish_tz)
        publish_at = seo.rfc3339(slot) if slot else None
    body = upload.video_body(title=title, description=description, tags=tags, privacy=cfg.privacy, publish_at=publish_at)
    video_id = upload.upload_video(yt, str(final), body, str(thumb))
    try:
        state, reason = upload.wait_until_accepted(yt, video_id, timeout=cfg.upload_check_seconds)
    except Exception as exc:  # noqa: BLE001 — the check is advisory; an API hiccup must not fail a good upload
        state, reason = "unknown", str(exc)
    if state in upload.BAD_STATES:      # no ledger row: the topic stays unused, so a seeded topic is simply made again
        print(f"✗ YouTube did not accept the upload: {state} ({reason or 'no reason given'}) — "
              f"https://studio.youtube.com/video/{video_id}/edit", flush=True)
        return EXIT_FAIL
    print(f"youtube status: {state}{' (' + reason + ')' if reason else ''}", flush=True)
    print(f"uploaded {video_id}: {title} · " + (f"scheduled for {publish_at}" if publish_at else cfg.privacy), flush=True)
    if cfg.playlists and not cfg.is_smoke:
        try:
            prof = seo.profile(seo_category)
            playlist_id = upload.ensure_playlist(yt, prof.playlist, seo.playlist_description(seo_category))
            upload.add_to_playlist(yt, playlist_id, video_id)
            print(f"playlist: {prof.playlist}", flush=True)
        except Exception as exc:  # noqa: BLE001 — never fail a published video over a playlist
            print(f"! playlist step failed: {exc}", flush=True)
    entry = {
        "date": today, "topic": name, "category": category, "wiki_title": (corpus.title if corpus else wiki_title),
        "video_id": video_id, "title": title, "privacy": cfg.privacy, "publish_at": publish_at,
        "duration_seconds": round(duration), "words": sc.word_count, "images": len(image_paths),
        "image_sources": by_source, "story_format": sc.story_format, "title_variant": variant,
        "cost_estimate_usd": round(tts.estimate_cost_usd(chars, cfg.tts_model) + 0.80, 2),
        "run_url": _run_url(),
    }
    if cfg.is_smoke:
        print(f"smoke run uploaded as https://youtu.be/{video_id} (unlisted); ledger not written", flush=True)
        return EXIT_OK
    ledger.append_entry(cfg.ledger_path, entry)
    if os.environ.get("GITHUB_ACTIONS"):
        ledger.git_commit_and_push(cfg.ledger_path, entry)
    print(f"✓ uploaded https://youtu.be/{video_id} · {(time.time() - t0) / 60:.1f} min total", flush=True)
    return EXIT_OK


def _check_recent_uploads(yt, cfg: Config, last: int = 3) -> None:
    """Warn (GitHub annotation) if any of the last few published videos was rejected, abandoned or deleted."""
    try:
        rows = [r for r in ledger.load_ledger(cfg.ledger_path) if r.get("video_id")][-last:]
        if not rows:
            return
        states = upload.upload_state(yt, [r["video_id"] for r in rows])
        for r in rows:
            state, reason = states.get(r["video_id"], ("unknown", ""))
            if state in upload.BAD_STATES:
                print(f"::warning::earlier video {r['video_id']} ({r.get('topic')}) is {state}"
                      f"{': ' + reason if reason else ''} — check YouTube Studio", flush=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("recent-upload check skipped: %s", exc)


def _thumbnail_credit(thumb: Path, used: list[dict]) -> str:
    """Description line crediting the thumbnail's photo when its licence needs it ('' otherwise)."""
    src = thumbnail.source_of(str(thumb))
    row = next((r for r in used if src and r.get("file") == Path(src).name), None)
    return captions.thumbnail_line(row)


def _used_credits(img_dir: Path, image_paths: list[str]) -> list[dict]:
    """Credit rows (source, author, licence, page) for exactly the photos in the reel, in reel order."""
    f = img_dir / "credits.json"
    rows = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
    by_file = {r.get("file"): r for r in rows}
    return [by_file[Path(p).name] for p in image_paths if Path(p).name in by_file]


def _run_url() -> str:
    srv, repo, rid = (os.environ.get(k, "") for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{srv}/{repo}/actions/runs/{rid}" if rid else ""
