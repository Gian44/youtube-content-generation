"""Cast library — filesystem and manifest side of the recurring-character bank.

One recurring AI character per voice persona. Its 8-second reaction clips are
generated once in Google Flow, dropped into ``<persona>/inbox`` and sorted here
into a tagged bank::

    <root>/<persona>/character.json          (written elsewhere)
    <root>/<persona>/reference.png           (user saves the master frame here)
    <root>/<persona>/outbox/...              (written elsewhere)
    <root>/<persona>/inbox/                  drop zone: <tag>_<n>.mp4, <tag>_<n>_alt<k>.mp4
    <root>/<persona>/<tag>/<n>.mp4           the bank (alt takes: <tag>/<n>_alt<k>.mp4)
    <root>/<persona>/manifest.json           {relpath: {duration, width, height, mtime, size}}
    <root>/<persona>/first_frames_grid.png   identity-drift check

No LLM calls live here: this module only moves files, probes them once with
ffprobe into a manifest, and renders a first-frames grid with Pillow.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from storyfactory.channel_context import effective_config
from storyfactory.config import get_settings
from storyfactory.content_defaults import CAST_DEFAULTS, CAST_EMOTION_TAGS
from storyfactory.logger import get_logger

log = get_logger("cast_library")

CLIP_EXTS = (".mp4", ".mov", ".webm", ".mkv")
GRID_COLUMNS = 8
GRID_FRAME_SECONDS = 1.0
GRID_THUMB = (270, 480)  # 9:16 thumbnails

MANIFEST_NAME = "manifest.json"
GRID_NAME = "first_frames_grid.png"
_IGNORED_INBOX_FILES = {"flow_clips.json"}

# Longest tag first so "neutral_listening_2" is not read as tag "neutral" + junk.
_TAGS_BY_LENGTH = sorted(CAST_EMOTION_TAGS, key=len, reverse=True)
_CLIP_STEM_RE = re.compile(
    r"^(?P<tag>" + "|".join(re.escape(t) for t in _TAGS_BY_LENGTH) + r")"
    r"_(?P<n>\d+)(?:_alt(?P<alt>\d+))?$"
)
_BANK_STEM_RE = re.compile(r"^(?P<n>\d+)(?:_alt(?P<alt>\d+))?$")


# ----------------------------------------------------------------------------
# Config / paths
# ----------------------------------------------------------------------------

def merged_cast_config() -> dict:
    """CAST_DEFAULTS overlaid with the active channel's ``cast`` config block."""
    override = effective_config("cast", {}) or {}
    return {**CAST_DEFAULTS, **dict(override)}


def library_root(channel_slug: str, cfg: dict) -> Path:
    """Bank root: ``cfg["library_path"]`` when set, else ``<storage>/cast/<slug>``."""
    override = (cfg or {}).get("library_path")
    if override:
        return Path(override)
    return Path(get_settings().local_storage_path) / "cast" / channel_slug


def personas_in(root: Path) -> list[str]:
    """Sorted persona folders under ``root`` (have character.json or a tag folder)."""
    root = Path(root)
    if not root.is_dir():
        return []
    out: list[str] = []
    for child in root.iterdir():
        if not child.is_dir() or child.name == "hero" or child.name.startswith("."):
            continue
        has_bible = (child / "character.json").is_file()
        has_tag = any((child / tag).is_dir() for tag in CAST_EMOTION_TAGS)
        if has_bible or has_tag:
            out.append(child.name)
    return sorted(out)


# ----------------------------------------------------------------------------
# Naming
# ----------------------------------------------------------------------------

def parse_clip_name(filename: str) -> tuple[str, int, int | None] | None:
    """``"shocked_1.mp4"`` -> ``("shocked", 1, None)``; ``"shocked_1_alt2.mov"`` -> ``("shocked", 1, 2)``.

    Returns None unless the tag is a known emotion tag and the extension is a
    clip extension (case-insensitive).
    """
    p = Path(filename)
    if p.suffix.lower() not in CLIP_EXTS:
        return None
    m = _CLIP_STEM_RE.match(p.stem.lower())
    if not m:
        return None
    alt = m.group("alt")
    return m.group("tag"), int(m.group("n")), (int(alt) if alt is not None else None)


def bank_name(n: int, alt: int | None, ext: str) -> str:
    """``"1.mp4"`` / ``"1_alt2.mov"``."""
    ext = ext if ext.startswith(".") else f".{ext}"
    suffix = f"_alt{alt}" if alt is not None else ""
    return f"{n}{suffix}{ext.lower()}"


def _parse_bank_name(filename: str) -> tuple[int, int | None] | None:
    p = Path(filename)
    if p.suffix.lower() not in CLIP_EXTS:
        return None
    m = _BANK_STEM_RE.match(p.stem)
    if not m:
        return None
    alt = m.group("alt")
    return int(m.group("n")), (int(alt) if alt is not None else None)


# ----------------------------------------------------------------------------
# Inbox -> bank
# ----------------------------------------------------------------------------

def _move(src: Path, dest: Path) -> None:
    try:
        src.rename(dest)
    except OSError:
        shutil.move(str(src), str(dest))


def ingest_inbox(root: Path, persona: str) -> dict:
    """Sort ``<persona>/inbox`` into ``<persona>/<tag>/<bank_name>``.

    Never overwrites: a clip whose destination already exists stays in the inbox
    and is reported as skipped with reason ``"exists"``.
    """
    result: dict = {"moved": [], "skipped": []}
    persona_dir = Path(root) / persona
    if not persona_dir.is_dir():
        return result
    inbox = persona_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    for src in sorted(inbox.iterdir()):
        if not src.is_file():
            continue
        if src.name.startswith(".") or src.name in _IGNORED_INBOX_FILES:
            continue
        parsed = parse_clip_name(src.name)
        if parsed is None:
            result["skipped"].append((src.name, "unrecognized name"))
            continue
        tag, n, alt = parsed
        dest = persona_dir / tag / bank_name(n, alt, src.suffix)
        if dest.exists():
            result["skipped"].append((src.name, "exists"))
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        _move(src, dest)
        result["moved"].append((src.name, f"{tag}/{dest.name}"))

    log.info(
        "cast_ingest",
        persona=persona,
        moved=len(result["moved"]),
        skipped=len(result["skipped"]),
    )
    return result


# ----------------------------------------------------------------------------
# Flow export -> inbox
# ----------------------------------------------------------------------------

def normalize_key(text: str, n: int = 40) -> str:
    """Lowercase, alnum + spaces only, whitespace collapsed, first ``n`` chars."""
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())
    return " ".join(cleaned.split())[:n]


def _match_prompt(line: str, keys: dict[str, tuple[str, int]]) -> tuple[str, int] | None:
    key = normalize_key(line)
    if not key:
        return None
    if key in keys:
        return keys[key]
    for pkey, target in keys.items():
        if pkey and (pkey.startswith(key) or key.startswith(pkey)):
            return target
    return None


def _next_inbox_name(inbox: Path, tag: str, n: int, ext: str = ".mp4") -> str:
    name = f"{tag}_{n}{ext}"
    if not (inbox / name).exists():
        return name
    k = 1
    while (inbox / f"{tag}_{n}_alt{k}{ext}").exists():
        k += 1
    return f"{tag}_{n}_alt{k}{ext}"


def _http_download(src: str, dest: Path) -> None:
    import httpx

    with httpx.stream("GET", src, follow_redirects=True, timeout=120) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in resp.iter_bytes():
                fh.write(chunk)


def fetch_flow_clips(
    root: Path,
    persona: str,
    entries: list[dict],
    prompts: dict[tuple[str, int], str],
    *,
    download=None,
) -> dict:
    """Download Flow-exported clips (``[{id, src, line}]``) into the inbox.

    Each entry's ``line`` is matched against the prompt texts by the 40-char
    normalized key; matched clips become ``inbox/<tag>_<n>.mp4`` (then
    ``_alt1``, ``_alt2``, ...), unmatched ones ``inbox/unmatched_<id>.mp4`` so
    nothing is lost.
    """
    result: dict = {"fetched": [], "unmatched": []}
    inbox = Path(root) / persona / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    dl = download or _http_download
    keys = {normalize_key(text): target for target, text in prompts.items()}

    for entry in entries or []:
        entry_id = str(entry.get("id", "") or "")
        src = entry.get("src")
        if not src:
            result["unmatched"].append(entry_id)
            continue
        target = _match_prompt(entry.get("line", ""), keys)
        if target is None:
            safe_id = re.sub(r"[^A-Za-z0-9_-]+", "_", entry_id) or "unknown"
            name = f"unmatched_{safe_id}.mp4"
            result["unmatched"].append(entry_id)
        else:
            tag, n = target
            name = _next_inbox_name(inbox, tag, n)
        dest = inbox / name
        try:
            dl(src, dest)
        except Exception as exc:  # noqa: BLE001 — one bad download must not kill the batch
            log.warning("cast_fetch_failed", persona=persona, id=entry_id, error=str(exc))
            if dest.exists():
                dest.unlink()
            continue
        if target is not None:
            result["fetched"].append((entry_id, name))

    log.info(
        "cast_fetch_flow_clips",
        persona=persona,
        fetched=len(result["fetched"]),
        unmatched=len(result["unmatched"]),
    )
    return result


# ----------------------------------------------------------------------------
# Probe / manifest
# ----------------------------------------------------------------------------

def probe_clip(path: Path) -> dict:
    """ffprobe duration/width/height; zeros on any failure."""
    empty = {"duration": 0.0, "width": 0, "height": 0}
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-show_entries", "format=duration",
            "-of", "json",
            str(path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return empty
        data = json.loads(result.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
        fmt = data.get("format") or {}
        return {
            "duration": float(fmt.get("duration") or 0.0),
            "width": int(stream.get("width") or 0),
            "height": int(stream.get("height") or 0),
        }
    except Exception:  # noqa: BLE001
        return empty


def _bank_files(persona_dir: Path) -> list[tuple[str, Path]]:
    """``[(relpath, path)]`` for every bank clip, tags in CAST_EMOTION_TAGS order."""
    out: list[tuple[str, Path]] = []
    for tag in CAST_EMOTION_TAGS:
        tag_dir = persona_dir / tag
        if not tag_dir.is_dir():
            continue
        for f in sorted(tag_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in CLIP_EXTS:
                out.append((f"{tag}/{f.name}", f))
    return out


def _read_manifest(persona_dir: Path) -> dict:
    path = persona_dir / MANIFEST_NAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def build_manifest(root: Path, persona: str) -> dict:
    """Probe every bank clip once; reuse entries whose mtime and size are unchanged."""
    persona_dir = Path(root) / persona
    old = _read_manifest(persona_dir)
    manifest: dict = {}
    probed = 0

    for relpath, path in _bank_files(persona_dir):
        st = path.stat()
        mtime, size = st.st_mtime, st.st_size
        prev = old.get(relpath)
        if (
            isinstance(prev, dict)
            and prev.get("mtime") == mtime
            and prev.get("size") == size
        ):
            manifest[relpath] = prev
            continue
        info = probe_clip(path)
        probed += 1
        manifest[relpath] = {**info, "mtime": mtime, "size": size}

    if persona_dir.is_dir():
        (persona_dir / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
    log.info(
        "cast_manifest",
        persona=persona,
        clips=len(manifest),
        probed=probed,
        dropped=len(set(old) - set(manifest)),
    )
    return manifest


def coverage(root: Path, persona: str, manifest: dict | None = None) -> dict[str, int]:
    """``{tag: clip_count}`` for all emotion tags (0 when none)."""
    cov = {tag: 0 for tag in CAST_EMOTION_TAGS}
    if manifest is not None:
        relpaths = list(manifest.keys())
    else:
        relpaths = [rel for rel, _ in _bank_files(Path(root) / persona)]
    for rel in relpaths:
        tag = rel.split("/", 1)[0]
        if tag in cov:
            cov[tag] += 1
    return cov


def have_clips(root: Path, persona: str) -> set[tuple[str, int]]:
    """``{(tag, n)}`` for every bank file; alt takes fold into the same ``n``."""
    have: set[tuple[str, int]] = set()
    for rel, path in _bank_files(Path(root) / persona):
        tag = rel.split("/", 1)[0]
        parsed = _parse_bank_name(path.name)
        if parsed is not None:
            have.add((tag, parsed[0]))
    return have


def missing_tags(cov: dict[str, int]) -> list[str]:
    """Tags with zero clips, in CAST_EMOTION_TAGS order."""
    return [tag for tag in CAST_EMOTION_TAGS if not cov.get(tag)]


# ----------------------------------------------------------------------------
# First-frames grid
# ----------------------------------------------------------------------------

def _extract_frame(clip: Path, dest: Path, seconds: float) -> bool:
    w, h = GRID_THUMB
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{seconds:.3f}",
        "-i", str(clip),
        "-frames:v", "1",
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
        "-update", "1",
        str(dest),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception:  # noqa: BLE001
        return False
    return result.returncode == 0 and dest.is_file() and dest.stat().st_size > 0


def write_first_frames_grid(root: Path, persona: str, manifest: dict) -> Path | None:
    """One thumbnail per bank clip at GRID_FRAME_SECONDS, labelled, in an 8-column grid."""
    if not manifest:
        return None
    from PIL import Image, ImageDraw

    persona_dir = Path(root) / persona
    w, h = GRID_THUMB
    frames: list[tuple[str, Image.Image]] = []

    with tempfile.TemporaryDirectory(prefix="cast_grid_") as tmp:
        tmp_dir = Path(tmp)
        for i, relpath in enumerate(sorted(manifest)):
            clip = persona_dir / relpath
            if not clip.is_file():
                continue
            duration = float((manifest.get(relpath) or {}).get("duration") or 0.0)
            seconds = GRID_FRAME_SECONDS if duration > GRID_FRAME_SECONDS else 0.0
            frame_path = tmp_dir / f"{i}.png"
            ok = _extract_frame(clip, frame_path, seconds)
            if not ok and seconds > 0:
                ok = _extract_frame(clip, frame_path, 0.0)
            if not ok:
                continue
            with Image.open(frame_path) as im:
                frames.append((relpath, im.convert("RGB").copy()))

    if not frames:
        log.info("cast_grid", persona=persona, frames=0, written=False)
        return None

    n = len(frames)
    cols = min(n, GRID_COLUMNS)
    rows = math.ceil(n / GRID_COLUMNS)
    grid = Image.new("RGB", (w * cols, h * rows), "black")
    draw = ImageDraw.Draw(grid)
    for idx, (relpath, im) in enumerate(frames):
        x = (idx % GRID_COLUMNS) * w
        y = (idx // GRID_COLUMNS) * h
        grid.paste(im, (x, y))
        draw.rectangle([x, y + h - 16, x + w, y + h], fill=(0, 0, 0))
        draw.text((x + 4, y + h - 14), relpath, fill=(255, 255, 255))

    out = persona_dir / GRID_NAME
    grid.save(out, format="PNG")
    log.info("cast_grid", persona=persona, frames=n, written=True, path=str(out))
    return out
