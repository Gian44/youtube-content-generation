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

from sof import images, ledger, render, research, script, topics, tts, upload
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

    # 1. Topic.
    topic_list = topics.load_topics(cfg.topics_path)
    rows = ledger.load_ledger(cfg.ledger_path)
    topic = topics.pick_next(topic_list, rows, forced=cfg.topic)
    run_id = f"{today}-{_slug(topic.name)}"
    work = Path(cfg.work_dir) / run_id
    work.mkdir(parents=True, exist_ok=True)
    print(f"topic: {topic.name} · run {run_id} · {cfg.minutes} min target", flush=True)

    # 2. Script.
    _stage("script")
    script_path = work / "script.json"
    if script_path.exists():
        sc = script.Script.from_dict(json.loads(script_path.read_text(encoding="utf-8")))
    else:
        llm = LLM(cfg.openai_api_key, cfg.gemini_api_key, cfg.script_model, cfg.gemini_models)
        hints = research.wikipedia_hints(topic.name)
        sc = script.generate_script(
            llm, topic=topic.name, hints=hints, minutes=cfg.minutes, target_words=cfg.target_words,
            num_movements=cfg.num_movements, max_segments=cfg.max_segments, fill_ratio=cfg.fill_ratio,
            max_words_ratio=cfg.max_words_ratio, segment_max_tokens=cfg.segment_max_tokens,
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
                               voice=cfg.tts_voice, speed=cfg.tts_speed)
        tts.assemble(parts, str(work))
    duration = tts.duration_seconds(str(narration))
    print(f"narration: {duration / 60:.1f} min · est. TTS cost ${tts.estimate_cost_usd(chars, cfg.tts_model):.2f}", flush=True)

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
        urls = images.collect_urls(list(topic.queries) + list(sc.image_queries), target=target,
                                   pexels_key=cfg.pexels_api_key, pixabay_key=cfg.pixabay_api_key)
        image_paths = images.download_all(urls, str(img_dir))
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
        render.assemble(str(reel), str(narration), str(final), audio_duration=duration, fps=cfg.fps)
    thumb = work / "thumb.jpg"
    if not thumb.exists():
        render.thumbnail(image_paths[min(9, len(image_paths) - 1)], sc.title, str(thumb))
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
        "date": today, "topic": topic.name, "video_id": video_id, "title": sc.title, "privacy": cfg.privacy,
        "duration_seconds": round(duration), "words": sc.word_count, "images": len(image_paths),
        "cost_estimate_usd": round(tts.estimate_cost_usd(chars, cfg.tts_model) + 0.15, 2),
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
