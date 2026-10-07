"""Orchestration: topic → script → tts → images → render → upload → ledger.

Each stage writes into ``work/<run-id>/`` and is skipped when its output already
exists, so a re-dispatched run on the same checkout resumes where it stopped. The
ledger is written only after a successful upload.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from sof import images, ledger, music, render, research, script, thumbnail, topicgen, topics, tts, upload
from sof.config import Config
from sof.llm import LLM

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

    # 1. Topic: forced (--topic) → unused seed from topics.yml → generated from the day's category.
    _stage("topic")
    rows = ledger.load_ledger(cfg.ledger_path)
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
        )
        script_path.write_text(json.dumps(sc.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"script: {sc.word_count} words, {sc.movements} movements · {sc.title}", flush=True)

    # 3. TTS.
    _stage("tts")
    narration = work / "narration.mp3"
    chars = len(sc.body)
    if not narration.exists():
        chunks = tts.chunk_text(sc.body, cfg.tts_chunk_chars)
        parts = tts.synthesize(chunks, str(work / "tts"), api_key=cfg.openai_api_key, model=cfg.tts_model,
                               voice=cfg.tts_voice, speed=cfg.tts_speed, instructions=cfg.tts_instructions)
        tts.assemble(parts, str(work))
    duration = tts.duration_seconds(str(narration))
    print(f"narration: {duration / 60:.1f} min · est. TTS cost ${tts.estimate_cost_usd(chars, cfg.tts_model):.2f}", flush=True)

    # 3b. Faint ambient bed under the voice (synthesised, licence-free).
    soundtrack = narration
    if cfg.music_enabled:
        _stage("music")
        mixed = work / "soundtrack.mp3"
        if not mixed.exists():
            music.mix(str(narration), str(mixed), work_dir=str(work), bed_lufs=cfg.music_lufs)
        soundtrack = mixed
        print(f"soundtrack: bed at {cfg.music_lufs:g} LUFS under narration", flush=True)

    # 4. Images.
    _stage("images")
    img_dir = work / "images"
    # Never fetch more images than the narration can show (plus a margin); 3 h → the full 150,
    # a 3-minute smoke → ~15, so the reel build stays proportional to the video.
    showable = int(duration // max(1.0, cfg.dwell_seconds)) + 6
    target = max(cfg.images_fail_below, min(cfg.images_target, showable))
    minimum = min(cfg.images_min, target)
    existing = sorted(str(p) for p in img_dir.glob("*.jpg")) if img_dir.exists() else []
    if len(existing) >= minimum:
        image_paths = existing
    else:
        # Ask for ~40 % more URLs than needed: CDN 5xx, tiny files and duplicates all eat into the count.
        urls = images.collect_urls(list(queries) + list(sc.image_queries), target=int(target * 1.4) + 5,
                                   pexels_key=cfg.pexels_api_key, pixabay_key=cfg.pixabay_api_key)
        image_paths = images.download_all(urls, str(img_dir), want=target)
    if len(image_paths) < cfg.images_fail_below:
        print(f"✗ only {len(image_paths)} images; need at least {cfg.images_fail_below}", flush=True)
        return EXIT_IMAGES
    print(f"images: {len(image_paths)}", flush=True)

    # 5. Render.
    _stage("render")
    final = work / "final.mp4"
    if not final.exists():
        reel = work / "reel.mp4"
        if not reel.exists():
            built = render.build_reel(image_paths, str(work / "reel_tmp"), dwell=cfg.dwell_seconds,
                                      crossfade=cfg.crossfade_seconds, width=cfg.width, height=cfg.height,
                                      fps=cfg.fps, batch=cfg.xfade_batch)
            os.replace(built, reel)
        render.assemble(str(reel), str(soundtrack), str(final), audio_duration=duration, fps=cfg.fps)
    thumb = work / "thumb.jpg"
    if not thumb.exists():
        thumbnail.make(image_paths, topic=name, hours=max(1, round(cfg.minutes / 60)), out=str(thumb))
    size_gb = final.stat().st_size / 1e9
    print(f"video: {final} ({size_gb:.2f} GB)", flush=True)

    # 6. Upload + ledger.
    if cfg.skip_upload:
        print(f"skip-upload set; done in {(time.time() - t0) / 60:.1f} min", flush=True)
        return EXIT_OK
    _stage("upload")
    body = upload.video_body(title=sc.title, description=sc.description, tags=sc.tags, privacy=cfg.privacy)
    video_id = upload.upload_video(yt, str(final), body, str(thumb))
    entry = {
        "date": today, "topic": name, "category": category, "wiki_title": (corpus.title if corpus else wiki_title),
        "video_id": video_id, "title": sc.title, "privacy": cfg.privacy,
        "duration_seconds": round(duration), "words": sc.word_count, "images": len(image_paths),
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


def _run_url() -> str:
    srv, repo, rid = (os.environ.get(k, "") for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{srv}/{repo}/actions/runs/{rid}" if rid else ""
