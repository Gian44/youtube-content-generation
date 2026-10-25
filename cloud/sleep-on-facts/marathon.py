"""Weekly marathon: three recent long videos joined into one upload (sof/marathon.py).

python marathon.py [--group auto|mix|<category>] [--size 3] [--privacy public] [--skip-upload] [--work DIR]

Needs GITHUB_REPOSITORY and a GH_TOKEN that can read Actions artifacts (the workflow provides both).
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from sof import ledger, marathon, seo, thumbnail, upload
from sof.config import Config


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--group", default="auto", help="auto | mix | a category name from topics.yml")
    p.add_argument("--size", type=int, default=3)
    p.add_argument("--privacy", choices=["public", "unlisted", "private"], default="public")
    p.add_argument("--skip-upload", action="store_true")
    p.add_argument("--work", default="work-marathon")
    ns = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", stream=sys.stdout)
    t0 = time.time()

    cfg = Config.from_env()
    repo = os.environ.get("GITHUB_REPOSITORY", "Gian44/youtube-content-generation")
    rows = ledger.load_ledger(cfg.ledger_path)
    recent = [r for r in rows if r.get("kind", "video") == "video"][-40:]
    avail = marathon.available_runs(repo, recent)
    cands = marathon.candidates(rows, avail)
    category, segments = marathon.choose_group(cands, size=ns.size, group=ns.group)
    print(f"media artifacts available: {len(avail)} · unused videos with media: {len(cands)}", flush=True)
    if not segments:
        print(f"not enough unused videos with kept media for a {ns.size}-part marathon ({ns.group}); nothing to do", flush=True)
        return 0
    print(f"marathon ({category}): " + " → ".join(f"{s['topic']} ({s['video_id']})" for s in segments), flush=True)

    yt = None
    if not ns.skip_upload:
        yt = upload.build_client(cfg)
        print(f"connected as {upload.health(yt)}", flush=True)

    work = Path(ns.work)
    work.mkdir(parents=True, exist_ok=True)
    finals, parts, photos = [], [], []
    for i, seg in enumerate(segments):
        folder = marathon.download_media(marathon.run_id(seg), work / f"seg{i}")
        final = folder / "final.mp4"
        secs = marathon.probe_seconds(str(final))
        chf = folder / "chapters.json"
        chapters = json.loads(chf.read_text(encoding="utf-8")) if chf.exists() else []
        src = folder / "thumb_source.jpg"
        if src.exists():
            photos.append(str(src))
        finals.append(str(final))
        parts.append((seo._clean(str(seg.get("topic"))), secs, chapters))
        print(f"part {i + 1}: {seg['topic']} · {secs / 3600:.2f} h · {len(chapters)} chapters", flush=True)

    out = work / "marathon.mp4"
    subprocess.run(marathon.concat_cmd(marathon.write_concat_list(finals, str(work / "list.txt")), str(out)), check=True, timeout=7200)
    for f in finals:                      # free the runner's disk before the upload
        Path(f).unlink(missing_ok=True)
    total = marathon.probe_seconds(str(out))
    hours = max(1, round(total / 3600))
    chapters = marathon.offset_chapters(parts)
    print(f"joined: {total / 3600:.2f} h · {out.stat().st_size / 1e9:.2f} GB", flush=True)

    credits = []
    if yt is not None:
        try:
            items = yt.videos().list(part="snippet", id=",".join(s["video_id"] for s in segments)).execute().get("items") or []
            by_id = {it["id"]: it["snippet"].get("description") or "" for it in items}
            credits = [marathon.thumb_credit_from(by_id.get(s["video_id"], "")) for s in segments]
        except Exception as exc:  # noqa: BLE001
            print(f"! could not read the originals' thumbnail credits ({exc})", flush=True)
    label = marathon.profile_for(category).phrase + " to fall asleep to"
    thumb = str(work / "thumb.jpg")
    if photos:
        thumbnail.compose_marathon(photos, label=label, names=[pt[0] for pt in parts], hours=hours, out=thumb)
    else:
        thumb = None
    title = marathon.build_title(category, segments, hours)
    description = marathon.build_description(category, segments, hours, chapters,
                                             thumb_credits=[c for c, s in zip(credits, segments) if c] if photos else [])
    tags = marathon.build_tags(category, segments)
    (work / "meta.json").write_text(json.dumps({"title": title, "description": description, "tags": tags}, indent=1,
                                               ensure_ascii=False), encoding="utf-8")
    print(f"title: {title}", flush=True)
    if ns.skip_upload:
        print(f"skip-upload set; built {out} in {(time.time() - t0) / 60:.1f} min", flush=True)
        return 0

    slot = seo.publish_slot(datetime.now(timezone.utc), 19, 30, tz=cfg.publish_tz, max_wait_hours=14)
    publish_at = seo.rfc3339(slot) if slot and ns.privacy == "public" else None
    body = upload.video_body(title=title, description=description, tags=tags, privacy=ns.privacy, publish_at=publish_at)
    video_id = upload.upload_video(yt, str(out), body, thumb)
    try:
        state, reason = upload.wait_until_accepted(yt, video_id, timeout=cfg.upload_check_seconds)
    except Exception as exc:  # noqa: BLE001
        state, reason = "unknown", str(exc)
    if state in upload.BAD_STATES:
        print(f"✗ YouTube did not accept the marathon: {state} ({reason or 'no reason given'})", flush=True)
        return 1
    print(f"uploaded {video_id} · " + (f"scheduled for {publish_at}" if publish_at else ns.privacy), flush=True)
    for name in ([marathon.MARATHON_PLAYLIST] + ([seo.profile(category).playlist] if category != marathon.MIX else [])):
        try:
            upload.add_to_playlist(yt, upload.ensure_playlist(yt, name, seo.playlist_description(
                category if category != marathon.MIX else "")), video_id)
        except Exception as exc:  # noqa: BLE001
            print(f"! playlist {name!r} failed: {exc}", flush=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entry = {"date": today, "kind": "marathon", "topic": "Marathon: " + ", ".join(pt[0] for pt in parts),
             "category": category, "video_id": video_id, "title": title, "privacy": ns.privacy, "publish_at": publish_at,
             "duration_seconds": round(total), "segments": [s["video_id"] for s in segments],
             "run_url": (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
                         if os.environ.get("GITHUB_RUN_ID") else None)}
    ledger.append_entry(cfg.ledger_path, entry)
    if os.environ.get("GITHUB_ACTIONS"):
        ledger.git_commit_and_push(cfg.ledger_path, entry)
    shutil.rmtree(work, ignore_errors=True)
    print(f"✓ marathon https://youtu.be/{video_id} · {(time.time() - t0) / 60:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
