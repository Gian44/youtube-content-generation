"""The film files Gian supplies, in a private Cloudflare R2 bucket (S3 API). Keys are ``<slug>.<ext>`` for the
video and ``<slug>.srt`` (or ``<slug>.en.srt``) for the subtitles, e.g. ``inception-2010.mp4`` +
``inception-2010.srt``. The runner downloads the film for the run and deletes it after. No file → the stock path."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("cny.store")
VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".m4v", ".webm", ".avi")
SUB_EXTS = (".srt", ".en.srt", ".eng.srt")


@dataclass
class FilmFiles:
    slug: str
    video_key: str | None = None
    srt_key: str | None = None
    video_path: str | None = None
    srt_path: str | None = None
    size: int = 0


def match_keys(slug: str, keys: list[str]) -> FilmFiles:
    """Pure: which bucket keys belong to ``slug`` (case-insensitive; a folder prefix is fine)."""
    out = FilmFiles(slug=slug)
    want = slug.lower()
    for k in sorted(keys):
        name = k.rsplit("/", 1)[-1].lower()
        for ext in SUB_EXTS:
            if name == want + ext and out.srt_key is None:
                out.srt_key = k
        for ext in VIDEO_EXTS:
            if name == want + ext and out.video_key is None:
                out.video_key = k
    return out


def configured(cfg) -> bool:
    return bool(cfg.r2_account_id and cfg.r2_access_key_id and cfg.r2_secret_access_key and cfg.r2_bucket)


def client(cfg):
    import boto3
    from botocore.config import Config as BotoConfig

    return boto3.client("s3", endpoint_url=f"https://{cfg.r2_account_id}.r2.cloudflarestorage.com",
                        aws_access_key_id=cfg.r2_access_key_id, aws_secret_access_key=cfg.r2_secret_access_key,
                        region_name="auto", config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 5}))


def list_keys(cfg) -> list[str]:
    s3 = client(cfg)
    keys: list[str] = []
    token = None
    while True:
        kw = {"Bucket": cfg.r2_bucket, "MaxKeys": 1000}
        if token:
            kw["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kw)
        keys += [o["Key"] for o in resp.get("Contents") or []]
        if not resp.get("IsTruncated"):
            return keys
        token = resp.get("NextContinuationToken")


def fetch_film(cfg, slug: str, work: Path) -> FilmFiles | None:
    """Download the film (and its subtitles) for ``slug`` into ``work/film/``; None when the bucket has no video
    for it or R2 is not configured. Never raises — a store problem means the stock path, not a dead run."""
    if not configured(cfg):
        return None
    try:
        files = match_keys(slug, list_keys(cfg))
        if not files.video_key:
            log.info("no film file for %s in r2://%s", slug, cfg.r2_bucket)
            return None
        d = work / "film"
        d.mkdir(parents=True, exist_ok=True)
        s3 = client(cfg)
        dest = d / ("source" + os.path.splitext(files.video_key)[1].lower())
        if not dest.exists():
            head = s3.head_object(Bucket=cfg.r2_bucket, Key=files.video_key)
            files.size = int(head.get("ContentLength") or 0)
            print(f"film file: {files.video_key} ({files.size / 1e9:.2f} GB) downloading…", flush=True)
            s3.download_file(cfg.r2_bucket, files.video_key, str(dest))
        files.video_path = str(dest)
        files.size = files.size or dest.stat().st_size
        if files.srt_key:
            sd = d / "source.srt"
            if not sd.exists():
                s3.download_file(cfg.r2_bucket, files.srt_key, str(sd))
            files.srt_path = str(sd)
        return files
    except Exception as exc:  # noqa: BLE001
        log.warning("film store unavailable for %s: %s", slug, exc)
        print(f"! film store unavailable ({str(exc)[:160]}); using stock footage", flush=True)
        return None


def delete_film(cfg, files: FilmFiles) -> bool:
    """Remove the film (and its .srt) from the bucket once its recap is published — 10 GB holds 3-4 films, so
    the bucket is a queue, not a library. Never raises."""
    if not configured(cfg) or not files.video_key:
        return False
    try:
        s3 = client(cfg)
        s3.delete_objects(Bucket=cfg.r2_bucket, Delete={"Objects": [{"Key": k} for k in (files.video_key, files.srt_key) if k]})
        log.info("deleted %s from r2://%s", files.video_key, cfg.r2_bucket)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("could not delete %s from the bucket: %s", files.video_key, exc)
        return False
