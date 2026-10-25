"""Narration-synced slideshow: one photo per fixed-length slot, rendered as ONE full-length timeline.

Why not ``render.build_reel``: it makes one clip per photo, crossfades them with xfade chains (every
frame encoded two or three times, and big xfade chains buffer huge numbers of frames) and LOOPS a
~50-min reel under 3 hours of narration, so the picture has nothing to do with the words. Here slot k
shows its own photo during narration time [k*D, (k+1)*D) and the reel is exactly as long as the narration.

How (every output frame is encoded exactly once):

* Times become frame indices once (slot k starts at round(k*D*fps), the reel ends at round(total*fps)),
  so 540 slots never drift, and the timeline is cut there into segments: ``body`` (one photo on its own)
  and ``trans`` (photo a crossfading into photo b with ffmpeg xfade).
* Each photo's slow push-in follows its own clock tau (frames since it began fading in) at one fixed
  rate. Every segment's zoompan starts at that photo's tau, so the zoom runs on seamlessly from body to
  transition to body even though they are separate ffmpeg runs.
* Segments are encoded with identical libx264 settings and joined by the concat demuxer with stream
  copy. A body depends only on (photo, tau range, caption, fades), so a photo shown in several middle
  slots is encoded once and referenced several times; a transition only on (photo a, photo b, captions, taus).
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import json
import logging
import math
import os
import subprocess
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from sof import render

log = logging.getLogger("sof.timeline")

ZOOM_KNEE = 1.09     # a photo kept for several slots zooms linearly up to here...
ZOOM_MAX = 1.12      # ...then eases out toward this, same slope at the knee: no over-crop, no dead stop
# x264 only starts its B-frame DTS delay on the 3rd frame, so a 1- or 2-frame segment would put
# non-monotonic DTS into the stream-copy concat (seen in testing); every segment is kept >= 3 frames.
MIN_SEGMENT_FRAMES = 3
# Identical for every segment: stream-copy concat needs byte-identical SPS/PPS in all of them.
X264 = ("-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p")
SPLIT_PLANES = True  # zoom luma and chroma planes separately (same luma, ~0.63x the CPU; see _photo)


# ---------------------------------------------------------------- planning (pure)

@dataclass(frozen=True)
class Segment:
    """One piece of the reel, rendered by one ffmpeg run.

    ``slots`` names each photo by the first slot of its span: ``(k,)`` for a body, ``(a, b)`` for a
    transition from photo a into photo b. ``tau`` is, per photo, the number of frames since that photo
    began fading in, at this segment's first frame (the zoom clock). Fades are frame counts.
    """
    kind: str
    slots: tuple[int, ...]
    start: int
    frames: int
    tau: tuple[int, ...]
    fade_in: int = 0
    fade_out: int = 0

    @property
    def end(self) -> int:
        return self.start + self.frames


def plan_segments(n_slots: int, *, dwell: float, crossfade: float, fps: int, total: float | None = None,
                  fade_in: float = 2.0, fade_out: float = 3.0, same: Sequence[bool] | None = None) -> list[Segment]:
    """Cut a timeline of ``n_slots`` slots into body/transition segments (no ffmpeg, no I/O).

    * Slot k starts at frame round(k*dwell*fps); the crossfade into slot k occupies the ``crossfade``
      seconds just before that, so at the slot's start its photo is fully visible.
    * ``same[k]`` (k >= 1) means slot k shows the same photo as slot k-1: the run becomes one longer span,
      with no crossfade into itself and an unbroken zoom. ``same[0]`` is ignored.
    * The reel ends at round(total*fps) when ``total`` is given (else at n_slots*dwell): the last span is
      stretched or shortened to end there. Slots starting at/after the end are dropped, and a last span
      too short to hold its fade-out is folded into the previous photo, which simply stays on.
    * The first segment fades in from black, the last fades out to black (clamped to fit).
    """
    if n_slots < 1:
        raise ValueError("a timeline needs at least one slot")
    if dwell <= 0 or crossfade < 0 or fps <= 0:
        raise ValueError(f"bad timing: dwell={dwell} crossfade={crossfade} fps={fps}")
    if same is not None and len(same) != n_slots:
        raise ValueError(f"same has {len(same)} flags for {n_slots} slots")
    bounds = [round(k * dwell * fps) for k in range(n_slots + 1)]
    cf = round(crossfade * fps)
    shortest = min(b - a for a, b in zip(bounds, bounds[1:]))
    if cf and not MIN_SEGMENT_FRAMES <= cf <= shortest - MIN_SEGMENT_FRAMES:
        raise ValueError(f"crossfade of {cf} frames does not fit slots of {shortest} frames "
                         f"(each piece needs >= {MIN_SEGMENT_FRAMES} frames)")
    end = round(total * fps) if total is not None else bounds[-1]
    if end < 1:
        raise ValueError(f"total={total} is shorter than one frame")
    if end - bounds[-1] > bounds[1]:                  # the planner should give ceil(total / dwell) slots
        log.warning("timeline: %d slots of %g s end %.1f s before the narration; the last photo is held to the end",
                    n_slots, dwell, (end - bounds[-1]) / fps)
    fi, fo = max(0, round(fade_in * fps)), max(0, round(fade_out * fps))

    firsts = [k for k in range(n_slots) if k == 0 or not (same and same[k])]     # first slot of each photo span
    kept = [k for k in firsts if bounds[k] < end]
    while len(kept) > 1 and end - bounds[kept[-1]] < max(fo, MIN_SEGMENT_FRAMES):
        kept.pop()
    if len(kept) < len(firsts):
        log.info("timeline: %d photo span(s) at the end dropped/folded to end at frame %d", len(firsts) - len(kept), end)

    segs: list[Segment] = []
    for i, k in enumerate(kept):
        last = i == len(kept) - 1
        origin = 0 if i == 0 else bounds[k] - cf          # frame where this photo starts fading in (tau 0)
        b0 = 0 if i == 0 else bounds[k]
        b1 = end if last else bounds[kept[i + 1]] - cf
        segs.append(Segment("body", (k,), b0, b1 - b0, (b0 - origin,)))
        if not last and cf:
            segs.append(Segment("trans", (k, kept[i + 1]), b1, cf, (b1 - origin, 0)))

    first, final = segs[0], segs[-1]
    if first is final:                                    # a single photo: both fades share its frames
        if fi + fo > first.frames:
            fi = first.frames * fi // (fi + fo)
            fo = first.frames - fi
        segs[0] = dataclasses.replace(first, fade_in=fi, fade_out=fo)
    else:
        segs[0] = dataclasses.replace(first, fade_in=min(fi, first.frames))
        segs[-1] = dataclasses.replace(final, fade_out=min(fo, final.frames))
    return segs


# ---------------------------------------------------------------- zoom + command builders (pure)

def zoom_rate(dwell: float, crossfade: float, fps: int) -> float:
    """Zoom increase per frame: a photo on screen for one slot (dwell + crossfade) goes 1 → render.ZOOM_END,
    the same push-in as ``render.ken_burns_cmd``."""
    return (render.ZOOM_END - 1) / ((dwell + crossfade) * fps)


def zoom_at(tau: float, rate: float) -> float:
    """Python mirror of ``zoom_expr`` (tests, planning): linear, then an exponential ease to ZOOM_MAX."""
    knee = (ZOOM_KNEE - 1) / rate
    if tau <= knee:
        return 1 + rate * tau
    a = ZOOM_MAX - ZOOM_KNEE
    return ZOOM_MAX - a * math.exp(-(tau - knee) * rate / a)


def zoom_expr(tau: int, rate: float) -> str:
    """zoompan ``z`` for a segment whose first frame is ``tau`` frames into its photo's zoom clock.

    ``on`` restarts at 0 in every ffmpeg run, so the offset is what makes the zoom continuous across
    segments: zoompan's crop window is a pure function of z, so equal tau gives an identical frame.
    """
    knee, a, t = (ZOOM_KNEE - 1) / rate, ZOOM_MAX - ZOOM_KNEE, f"(on+{int(tau)})"
    return f"if(lte({t},{knee:.6f}),1+{rate:.12g}*{t},{ZOOM_MAX:g}-{a:g}*exp(-({t}-{knee:.6f})*{rate:.12g}/{a:g}))"


def _photo(inp: int, cap: int | None, label: str, *, frames: int, tau: int, rate: float, width: int, height: int,
           fps: int, split: bool | None = None, video: bool = False) -> str:
    """One photo's stream (the same chain in bodies and transitions, so they match pixel for pixel at their
    joins): the ``render.ken_burns_cmd`` zoompan on the pre-covered canvas, then the credit line on top.

    ``split`` (default ``SPLIT_PLANES``) zooms luma on the 4x canvas and the two chroma planes on half-size
    canvases (still 4x their 4:2:0 output) and merges them. Measured on 1080p: luma bit-identical to the
    one-zoompan chain, chroma within a few levels (PSNR > 48 dB), ~0.63x the CPU, because zoompan's per-frame
    bicubic downscale of the whole 4:4:4 canvas is the dominant cost of the render. zoompan itself in 4:2:0
    would be as cheap but snaps its crop to even pixels, which doubled the frame-to-frame step jitter.
    """
    if video:
        # Moving footage (pre-cut by render.cover_clip, looped by the input's -stream_loop): its clock is the
        # same tau, so a clip runs on seamlessly from body to transition to body, like a photo's zoom.
        chain = (f"[{inp}:v]trim=start_frame={int(tau)}:end_frame={int(tau) + frames},setpts=PTS-STARTPTS,"
                 f"fps={fps},setsar=1,format=yuv420p")
        if cap is None:
            return f"{chain}[{label}]"
        return f"{chain}[{label}z];[{label}z][{cap}:v]overlay=0:0:format=auto,format=yuv420p[{label}]"
    z = zoom_expr(tau, rate)

    def zp(w: int, h: int) -> str:
        return f"zoompan=z='{z}':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}"

    if SPLIT_PLANES if split is None else split:
        cw, ch, L = (width + 1) // 2, (height + 1) // 2, label
        chain = (f"[{inp}:v]format=yuvj444p,extractplanes=y+u+v[{L}y0][{L}u0][{L}v0];"
                 f"[{L}y0]{zp(width, height)}[{L}y];"
                 f"[{L}u0]scale=iw/2:ih/2:flags=bicubic,{zp(cw, ch)}[{L}u];"
                 f"[{L}v0]scale=iw/2:ih/2:flags=bicubic,{zp(cw, ch)}[{L}v];"
                 f"[{L}y][{L}u][{L}v]mergeplanes=0x001020:yuvj420p,setsar=1,format=yuv420p")
    else:
        chain = f"[{inp}:v]{zp(width, height)},setsar=1,format=yuv420p"
    if cap is None:
        return f"{chain}[{label}]"
    return f"{chain}[{label}z];[{label}z][{cap}:v]overlay=0:0:format=auto,format=yuv420p[{label}]"


def _fades(frames: int, fade_in: int, fade_out: int) -> str:
    """Frame-exact fades: black on the first frame / on the last frame."""
    parts = []
    if fade_in > 0:
        parts.append(f"fade=t=in:s=0:n={fade_in}")
    if fade_out > 0:
        parts.append(f"fade=t=out:s={frames - fade_out}:n={max(1, fade_out - 1)}")
    return ",".join(parts) or "null"


def _is_clip(path: str) -> bool:
    return str(path).lower().endswith((".mp4", ".mov", ".webm", ".m4v"))


def _inp(path: str) -> list[str]:
    """ffmpeg input args: a footage clip loops forever (the trim in ``_photo`` takes the frames it needs)."""
    return ["-stream_loop", "-1", "-i", path] if _is_clip(path) else ["-i", path]


def _encode(out: str, frames: int, fps: int) -> list[str]:
    return ["-map", "[v]", "-frames:v", str(frames), "-r", str(fps), *X264, "-an", "-map_metadata", "-1", out]


def body_cmd(image: str, out: str, *, frames: int, tau: int, rate: float, width: int, height: int, fps: int,
             caption: str | None = None, fade_in: int = 0, fade_out: int = 0, split: bool | None = None) -> list[str]:
    """One photo on its own for ``frames`` frames, its zoom clock starting at ``tau``."""
    vid = _is_clip(image)
    cmd = ["ffmpeg", "-y", *_inp(image)] + (["-i", caption] if caption else [])
    graph = _photo(0, 1 if caption else None, "p", frames=frames, tau=tau, rate=rate, width=width, height=height, fps=fps,
                   split=split, video=vid)
    return cmd + ["-filter_complex", f"{graph};[p]{_fades(frames, fade_in, fade_out)}[v]", *_encode(out, frames, fps)]


def transition_cmd(image_a: str, image_b: str, out: str, *, frames: int, tau_a: int, tau_b: int = 0, rate: float,
                   width: int, height: int, fps: int, caption_a: str | None = None,
                   caption_b: str | None = None, split: bool | None = None) -> list[str]:
    """Photo a (zoom clock at ``tau_a``) crossfading into photo b (at ``tau_b``) over ``frames`` frames.

    Each photo carries its own credit line before the xfade, so the two captions crossfade with their photos.
    The first frame is pure a and the last is 1/frames short of pure b, so a's body before and b's after join seamlessly.
    """
    cmd = ["ffmpeg", "-y", *_inp(image_a), *_inp(image_b)]
    ca = cb = None
    if caption_a:
        cmd += ["-i", caption_a]
        ca = 2
    if caption_b:
        cmd += ["-i", caption_b]
        cb = 3 if caption_a else 2
    kw = dict(frames=frames, rate=rate, width=width, height=height, fps=fps, split=split)
    graph = ";".join([_photo(0, ca, "a", tau=tau_a, video=_is_clip(image_a), **kw),
                      _photo(1, cb, "b", tau=tau_b, video=_is_clip(image_b), **kw),
                      f"[a][b]xfade=transition=fade:duration={frames / fps:.6f}:offset=0,format=yuv420p[v]"])
    return cmd + ["-filter_complex", graph, *_encode(out, frames, fps)]


def concat_cmd(list_file: str, out: str) -> list[str]:
    """Join the segments without re-encoding (they share one encoder setup, see X264)."""
    return ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", "-map_metadata", "-1", out]


# ---------------------------------------------------------------- orchestration

def _digest(key: object) -> str:
    return hashlib.sha1(repr(key).encode("utf-8")).hexdigest()[:16]


def _part(path: Path) -> Path:
    """Temporary name for an in-progress file: only finished outputs ever carry the final name."""
    return path.with_name(path.stem + ".part" + path.suffix)


def probe(path: str) -> tuple[int | None, float | None, str]:
    """(frames, duration s, H.264 header hash) of the first video stream, from the container (no decoding)."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_data_hash", "sha256",
                        "-show_entries", "stream=nb_frames,duration,extradata_hash:format=duration", "-of", "json", path],
                       capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    try:
        info = json.loads(r.stdout or "{}")
    except ValueError:
        info = {}
    st = (info.get("streams") or [{}])[0]
    frames = int(st["nb_frames"]) if str(st.get("nb_frames", "")).isdigit() else None
    dur = st.get("duration") or (info.get("format") or {}).get("duration")
    try:
        dur = float(dur) if dur is not None else None
    except ValueError:
        dur = None
    return frames, dur, str(st.get("extradata_hash") or "")


@dataclass
class _Job:
    path: Path
    cmd: Callable[[str], list[str]]
    frames: int
    photos: tuple[str, ...]


def _workers(workers: int | None, jobs: int) -> int:
    return max(1, min(workers or max(2, (os.cpu_count() or 2) - 1), max(1, jobs)))


def _cover_all(images: list[str], tmp: Path, *, width: int, height: int, canvas: float, dim: float,
               workers: int | None, fps: int = 24, clip_slow: float = 1.5) -> dict[str, str | None]:
    """Pre-cover each unique photo once (pre-cut each footage clip once); ``None`` for one that cannot be read."""
    def one(src: str) -> tuple[str, str | None]:
        try:
            st = os.stat(src)
            key = (os.path.abspath(src), st.st_size, st.st_mtime_ns, width, height, canvas, dim)
            if _is_clip(src):
                dst = tmp / f"clip_{_digest(key + (fps, clip_slow))}.mp4"
                if not dst.exists():
                    part = _part(dst)
                    render.cover_clip(src, str(part), width=width, height=height, fps=fps, dim=dim, slow=clip_slow)
                    frames, _, _ = probe(str(part))
                    if not frames or frames < fps:
                        raise RuntimeError(f"clip too short after cutting ({frames} frames)")
                    os.replace(part, dst)
                return src, str(dst)
            dst = tmp / f"cover_{_digest(key)}.jpg"
            if not dst.exists():
                part = _part(dst)
                render.cover_image(src, str(part), width=width, height=height, scale=canvas, dim=dim)
                os.replace(part, dst)
            return src, str(dst)
        except Exception as exc:  # noqa: BLE001 — a corrupt download must not kill the video
            log.warning("cover failed for %s: %s", src, exc)
            return src, None

    out: dict[str, str | None] = {}
    with ThreadPoolExecutor(max_workers=_workers(workers, len(images))) as pool:
        for src, cov in pool.map(one, images):
            out[src] = cov
    return out


def _substitute(slots: list[str], bad: set[str]) -> list[str]:
    """Show the nearest usable neighbour (previous first) in a failed photo's slots, so no timing shifts."""
    out: list[str | None] = [None if s in bad else s for s in slots]
    last = None
    for i, s in enumerate(out):
        out[i] = last = s if s is not None else last
    nxt = None
    for i in range(len(out) - 1, -1, -1):
        out[i] = nxt = out[i] if out[i] is not None else nxt
    if any(s is None for s in out):
        raise RuntimeError("no slideshow photo could be prepared")
    return out  # type: ignore[return-value]


def _run_jobs(jobs: list[_Job], workers: int | None) -> tuple[dict[Path, str], set[str]]:
    """Render (or, if a finished file exists from an earlier run, just re-check) each unique segment.

    Returns ({path: error} for segments that failed twice, the set of H.264 header hashes seen)."""
    failed: dict[Path, str] = {}
    headers: set[str] = set()
    if not jobs:
        return failed, headers

    def one(job: _Job) -> tuple[_Job, str | None, str]:
        if job.path.exists():
            frames, _, hdr = probe(str(job.path))
            if frames == job.frames:
                return job, None, hdr
            job.path.unlink(missing_ok=True)
        part, err = _part(job.path), ""
        for _ in range(2):                                 # one retry: a transient OOM/kill is not a bad photo
            try:
                r = subprocess.run(job.cmd(str(part)), capture_output=True, text=True, stdin=subprocess.DEVNULL,
                                   timeout=max(600, 3 * job.frames))
                if r.returncode != 0:
                    err = r.stderr[-300:]
                    continue
                frames, _, hdr = probe(str(part))
            except (subprocess.TimeoutExpired, OSError) as exc:
                err = str(exc)[-300:]
                continue
            if frames == job.frames:
                os.replace(part, job.path)
                return job, None, hdr
            err = f"{frames} frames instead of {job.frames}"
        part.unlink(missing_ok=True)
        return job, err, ""

    n = _workers(workers, len(jobs))
    total_frames = sum(j.frames for j in jobs)
    done = done_frames = 0
    t0 = last_log = time.time()
    log.info("timeline: rendering %d segments (%d frames) with %d workers", len(jobs), total_frames, n)
    with ThreadPoolExecutor(max_workers=n) as pool:
        # longest first: the pool drains evenly instead of ending on one long body
        futs = [pool.submit(one, j) for j in sorted(jobs, key=lambda j: -j.frames * len(j.photos))]
        for fut in as_completed(futs):
            job, err, hdr = fut.result()
            done += 1
            done_frames += job.frames
            if err:
                failed[job.path] = err
                log.warning("segment %s failed: %s", job.path.name, err)
            else:
                headers.add(hdr)
            if time.time() - last_log > 120 or done == len(jobs):
                last_log = time.time()
                log.info("timeline: %d/%d segments, %d/%d frames, %.0f s", done, len(jobs), done_frames, total_frames,
                         last_log - t0)
    return failed, headers


def build_timeline_reel(slots: list[str], tmp_dir: str, *, dwell: float, crossfade: float, width: int, height: int,
                        fps: int, dim: float = 1.0, captions: dict[str, str] | None = None, total: float | None = None,
                        workers: int | None = None, fade_in: float = 2.0, fade_out: float = 3.0,
                        canvas: float | None = None, cleanup: bool = True, clip_slow: float = 1.5) -> str:
    """Render the slot list (one local image path — or a ``.mp4`` footage clip — per ``dwell``-second slot) into one silent reel and
    return its path (``tmp_dir/timeline.mp4``), verified with ffprobe to hold exactly the planned frames.

    ``captions`` maps an image path to its on-screen credit line. ``total`` (the narration length) makes the
    reel end exactly there. ``canvas`` overrides ``render.CANVAS`` (cover scale; 3 measured at ~0.73x the CPU
    of 4 with no measurable loss of smoothness). ``cleanup`` deletes covers before the concat and segments
    after it (a 4x cover is 5-13 MB; the segments are about half the reel).
    """
    if not slots:
        raise ValueError("no slots to render")
    tmp = Path(tmp_dir)
    tmp.mkdir(parents=True, exist_ok=True)
    canvas = render.CANVAS if canvas is None else canvas
    captions = captions or {}
    rate = zoom_rate(dwell, crossfade, fps)
    t0 = time.time()

    unique = list(dict.fromkeys(slots))
    covers = _cover_all(unique, tmp, width=width, height=height, canvas=canvas, dim=dim, workers=workers, fps=fps,
                        clip_slow=clip_slow)
    bad = {s for s, c in covers.items() if c is None}
    cap_png: dict[str, str] = {}                       # image → credit-line PNG (one PNG per distinct text)
    for img in unique:
        text = captions.get(img)
        if not text or img in bad:
            continue
        png = tmp / f"caption_{_digest((text, width, height))}.png"
        try:
            if not png.exists():
                part = _part(png)
                render.caption_png(text, str(part), width=width, height=height)
                os.replace(part, png)
            cap_png[img] = str(png)
        except Exception as exc:  # noqa: BLE001 — never show a photo that needs a credit without it
            log.warning("caption failed for %s: %s", img, exc)
            bad.add(img)
    log.info("timeline: %d slots, %d unique photos (%d unusable), covers in %.0f s", len(slots), len(unique),
             len(bad), time.time() - t0)

    for _attempt in range(3):
        eff = _substitute(slots, bad)
        same = [False] + [eff[k] == eff[k - 1] for k in range(1, len(eff))]
        segs = plan_segments(len(eff), dwell=dwell, crossfade=crossfade, fps=fps, total=total, fade_in=fade_in,
                             fade_out=fade_out, same=same)
        files: list[Path] = []
        jobs: dict[Path, _Job] = {}
        for seg in segs:
            imgs = tuple(eff[k] for k in seg.slots)
            covs = [covers[i] for i in imgs]
            caps = [cap_png.get(i) for i in imgs]
            kw = dict(frames=seg.frames, rate=rate, width=width, height=height, fps=fps)
            if seg.kind == "body":
                key = ("body", covs[0], caps[0], seg.tau[0], seg.fade_in, seg.fade_out)
                cmd = functools.partial(body_cmd, covs[0], tau=seg.tau[0], caption=caps[0], fade_in=seg.fade_in,
                                        fade_out=seg.fade_out, **kw)
            else:
                key = ("trans", covs[0], caps[0], seg.tau[0], covs[1], caps[1], seg.tau[1])
                cmd = functools.partial(transition_cmd, covs[0], covs[1], tau_a=seg.tau[0], tau_b=seg.tau[1],
                                        caption_a=caps[0], caption_b=caps[1], **kw)
            path = tmp / f"{seg.kind}_{_digest(key + (seg.frames, rate, width, height, fps, X264, SPLIT_PLANES))}.mp4"
            files.append(path)
            jobs.setdefault(path, _Job(path, cmd, seg.frames, imgs))
        log.info("timeline: %d segments (%d unique) for %d frames", len(segs), len(jobs), segs[-1].end)
        failed, headers = _run_jobs(list(jobs.values()), workers)
        if not failed:
            break
        for path in failed:
            bad.update(jobs[path].photos)               # replaced by a neighbour on the next pass
    else:
        raise RuntimeError(f"timeline segments kept failing: {next(iter(failed.values()))}")
    if len(headers) > 1:
        raise RuntimeError(f"segments were encoded with different H.264 headers ({len(headers)}); "
                           "a stream-copy concat would corrupt the video")
    if cleanup:                                        # covers are ~5-13 MB each at 4x: free them before the concat
        for p in {*(Path(c) for c in covers.values() if c), *(Path(c) for c in cap_png.values())}:
            p.unlink(missing_ok=True)

    lst = tmp / "timeline.txt"
    with open(lst, "w", encoding="utf-8") as f:
        for p in files:
            f.write("file '" + str(p.resolve()).replace("'", "'\\''") + "'\n")
    reel = tmp / "timeline.mp4"
    part = _part(reel)
    render.run(concat_cmd(str(lst), str(part)), 3600)
    expected = segs[-1].end
    frames, dur, _ = probe(str(part))
    if frames is None or abs(frames - expected) > 1 or dur is None or abs(dur - expected / fps) > 1.0 / fps + 1e-3:
        raise RuntimeError(f"timeline reel check failed: {frames} frames / {dur} s, expected {expected} frames / "
                           f"{expected / fps:.3f} s")
    os.replace(part, reel)
    log.info("timeline: %d frames (%.1f min) from %d segments in %.0f s", expected, expected / fps / 60, len(files),
             time.time() - t0)
    if cleanup:
        for p in {*files, lst}:
            p.unlink(missing_ok=True)
    return str(reel)
