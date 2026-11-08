"""The film files Gian supplies. Two stores, picked by which secrets exist:

* **Google Drive** (Gian, 2026-11-08: no card, 15 GB free on the channel's own Gmail): a folder shared with a
  service account; ``GDRIVE_SERVICE_ACCOUNT_JSON`` (the key file's content) + ``GDRIVE_FOLDER_ID`` (or the folder is
  found by name, ``GDRIVE_FOLDER`` = ``cinybe-films``).
* **Cloudflare R2** (S3 API): ``R2_ACCOUNT_ID`` / ``R2_ACCESS_KEY_ID`` / ``R2_SECRET_ACCESS_KEY`` / ``R2_BUCKET``.

Keys (file names) are ``<slug>.<ext>`` for the video and ``<slug>.srt`` (or ``<slug>.en.srt``) for the subtitles,
e.g. ``inception-2010.mkv`` + ``inception-2010.srt``. The runner downloads the film for the run and deletes it
after; a published film is removed from the store (the store is a queue, not a library).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("cny.store")
VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".m4v", ".webm", ".avi")
SUB_EXTS = (".srt", ".en.srt", ".eng.srt")
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]


@dataclass
class FilmFiles:
    slug: str
    video_key: str | None = None
    srt_key: str | None = None
    video_path: str | None = None
    srt_path: str | None = None
    size: int = 0


def match_keys(slug: str, keys: list[str]) -> FilmFiles:
    """Pure: which keys (file names, a folder prefix is fine) belong to ``slug``, case-insensitive."""
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


def backend(cfg) -> str | None:
    if cfg.gdrive_service_account_json and (cfg.gdrive_folder_id or cfg.gdrive_folder):
        return "drive"
    if cfg.r2_account_id and cfg.r2_access_key_id and cfg.r2_secret_access_key and cfg.r2_bucket:
        return "r2"
    return None


def configured(cfg) -> bool:
    return backend(cfg) is not None


def label(cfg) -> str:
    b = backend(cfg)
    return f"drive:{cfg.gdrive_folder_id or cfg.gdrive_folder}" if b == "drive" else f"r2://{cfg.r2_bucket}" if b == "r2" else "none"


# ---------------------------------------------------------------- Google Drive (service account)

class Drive:
    def __init__(self, cfg):
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        info = json.loads(cfg.gdrive_service_account_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=DRIVE_SCOPES)
        self.svc = build("drive", "v3", credentials=creds, cache_discovery=False)
        self.folder_id = cfg.gdrive_folder_id or self._find_folder(cfg.gdrive_folder)
        self.ids: dict[str, str] = {}           # name → file id

    def _find_folder(self, name: str) -> str:
        q = f"mimeType='application/vnd.google-apps.folder' and name='{name}' and trashed=false"
        items = self.svc.files().list(q=q, fields="files(id,name)", pageSize=5, supportsAllDrives=True,
                                      includeItemsFromAllDrives=True).execute().get("files") or []
        if not items:
            raise RuntimeError(f"no Drive folder named {name!r} is shared with the service account")
        return items[0]["id"]

    def list_keys(self) -> list[str]:
        keys, token = [], None
        while True:
            resp = self.svc.files().list(q=f"'{self.folder_id}' in parents and trashed=false", fields="nextPageToken,files(id,name,size)",
                                         pageSize=200, pageToken=token, supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            for f in resp.get("files") or []:
                self.ids[f["name"]] = f["id"]
                keys.append(f["name"])
            token = resp.get("nextPageToken")
            if not token:
                return keys

    def size(self, key: str) -> int:
        meta = self.svc.files().get(fileId=self.ids[key], fields="size", supportsAllDrives=True).execute()
        return int(meta.get("size") or 0)

    def download(self, key: str, dest: str) -> None:
        from googleapiclient.http import MediaIoBaseDownload

        req = self.svc.files().get_media(fileId=self.ids[key], supportsAllDrives=True)
        with open(dest, "wb") as f:
            dl = MediaIoBaseDownload(f, req, chunksize=64 * 1024 * 1024)
            done = False
            while not done:
                status, done = dl.next_chunk(num_retries=5)

    def trash(self, key: str) -> str:
        """Move the file to the Drive's trash; when Drive refuses (My Drive lets only the owner trash a file the
        service account merely edits), rename it ``published--<name>`` so it no longer matches any slug and the
        folder shows at a glance what is done. Returns what happened."""
        try:
            self.svc.files().update(fileId=self.ids[key], body={"trashed": True}, supportsAllDrives=True).execute()
            return "trashed"
        except Exception as exc:  # noqa: BLE001
            log.warning("could not trash %s (%s); renaming instead", key, str(exc)[:120])
            self.svc.files().update(fileId=self.ids[key], body={"name": "published--" + key.rsplit("/", 1)[-1]},
                                    supportsAllDrives=True).execute()
            return "renamed"


# ---------------------------------------------------------------- Cloudflare R2 (S3)

def r2_client(cfg):
    import boto3
    from botocore.config import Config as BotoConfig

    return boto3.client("s3", endpoint_url=f"https://{cfg.r2_account_id}.r2.cloudflarestorage.com",
                        aws_access_key_id=cfg.r2_access_key_id, aws_secret_access_key=cfg.r2_secret_access_key,
                        region_name="auto", config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 5}))


def r2_list_keys(cfg) -> list[str]:
    s3 = r2_client(cfg)
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


# ---------------------------------------------------------------- the store, whichever backend

_DRIVE: Drive | None = None


def _drive(cfg) -> Drive:
    global _DRIVE
    if _DRIVE is None:
        _DRIVE = Drive(cfg)
    return _DRIVE


def list_keys(cfg) -> list[str]:
    return _drive(cfg).list_keys() if backend(cfg) == "drive" else r2_list_keys(cfg)


def fetch_film(cfg, slug: str, work: Path) -> FilmFiles | None:
    """Download the film (and its subtitles) for ``slug`` into ``work/film/``; None when the store has no video for
    it or is not configured. Never raises — a store problem means nothing is posted, not a dead run."""
    if not configured(cfg):
        return None
    try:
        files = match_keys(slug, list_keys(cfg))
        if not files.video_key:
            log.info("no film file for %s in %s", slug, label(cfg))
            return None
        d = work / "film"
        d.mkdir(parents=True, exist_ok=True)
        dest = d / ("source" + os.path.splitext(files.video_key)[1].lower())
        srt_dest = d / "source.srt"
        if backend(cfg) == "drive":
            dr = _drive(cfg)
            if not dest.exists():
                files.size = dr.size(files.video_key)
                print(f"film file: {files.video_key} ({files.size / 1e9:.2f} GB) downloading from Drive…", flush=True)
                dr.download(files.video_key, str(dest))
            if files.srt_key and not srt_dest.exists():
                dr.download(files.srt_key, str(srt_dest))
        else:
            s3 = r2_client(cfg)
            if not dest.exists():
                head = s3.head_object(Bucket=cfg.r2_bucket, Key=files.video_key)
                files.size = int(head.get("ContentLength") or 0)
                print(f"film file: {files.video_key} ({files.size / 1e9:.2f} GB) downloading from R2…", flush=True)
                s3.download_file(cfg.r2_bucket, files.video_key, str(dest))
            if files.srt_key and not srt_dest.exists():
                s3.download_file(cfg.r2_bucket, files.srt_key, str(srt_dest))
        files.video_path = str(dest)
        files.size = files.size or dest.stat().st_size
        if files.srt_key and srt_dest.exists():
            files.srt_path = str(srt_dest)
        return files
    except Exception as exc:  # noqa: BLE001
        log.warning("film store unavailable for %s: %s", slug, exc)
        print(f"! film store unavailable ({str(exc)[:160]})", flush=True)
        return None


def delete_film(cfg, files: FilmFiles) -> bool:
    """Remove the film (and its subtitles) from the store once its recap is published. Never raises."""
    if not configured(cfg) or not files.video_key:
        return False
    try:
        if backend(cfg) == "drive":
            dr = _drive(cfg)
            for k in (files.video_key, files.srt_key):
                if k and k in dr.ids:
                    print(f"store: {k} {dr.trash(k)}", flush=True)
        else:
            r2_client(cfg).delete_objects(Bucket=cfg.r2_bucket, Delete={"Objects": [{"Key": k} for k in (files.video_key, files.srt_key) if k]})
        log.info("removed %s from %s", files.video_key, label(cfg))
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("could not remove %s from the store: %s", files.video_key, exc)
        return False
