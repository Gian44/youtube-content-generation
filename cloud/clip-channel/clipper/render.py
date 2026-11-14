"""Editor: cut the moment, crop it to 9:16, burn captions, and add the headline in one of two looks.

Style A (meme panel): white panel on top with a bold black headline + emoji line, clip below, glow captions.
Style B (cinematic): clip centred between black bars, a two-line banner for the first seconds, big captions.
Reference: project doc claude/clipping-style-reference.md."""
from __future__ import annotations

import json
import os
import re
import statistics
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .find import YTDLP, label_said, sh

W, H = 1080, 1920
PANEL_H = 700                    # style A: white headline panel height; the clip fills the rest
B_TOP, B_SIZE = 420, 1080        # style B: square clip box
BANNER_S = 5                     # style B: seconds the banner stays up
FONTS_BOLD = [r"C:\Windows\Fonts\arialbd.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
FONTS_EMOJI = [r"C:\Windows\Fonts\seguiemj.ttf", "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"]


def _font(paths: list[str], size: int):
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def download_segment(video_id: str, start: float, end: float, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    # HLS first: the plain https formats hung for minutes on sections deep inside long videos (2026-11-14 test:
    # 150 s timeout twice, HLS done in 8 s). The second format list is only a fallback.
    formats = ["bv*[height<=1080][protocol^=m3u8]+ba[protocol^=m3u8]/b[height<=1080][protocol^=m3u8]",
               "bv*[height<=1080]+ba/b[height<=1080]/b"]
    last = ""
    for fmt in formats:
        for stale in dest.parent.glob(dest.name + "*"):
            stale.unlink(missing_ok=True)
        cmd = YTDLP + ["--download-sections", f"*{start:.2f}-{end:.2f}", "--force-keyframes-at-cuts", "-f", fmt,
                       "--merge-output-format", "mp4", "-o", str(dest), f"https://www.youtube.com/watch?v={video_id}"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        try:
            out, _ = p.communicate(timeout=240)
            last = (out or "")[-400:]
        except subprocess.TimeoutExpired:
            # kill yt-dlp and the ffmpeg it started, or the stalled ffmpeg keeps the file locked
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
            else:
                p.kill()
            p.communicate()
            last = "timed out after 240 s"
        if dest.exists() and dest.stat().st_size > 50_000:
            return dest
    raise RuntimeError(f"download failed for {video_id}: {last}")


def probe(path: Path) -> tuple[int, int, float]:
    r = sh(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
            "-of", "json", str(path)], timeout=60)
    d = json.loads(r.stdout)
    return int(d["streams"][0]["width"]), int(d["streams"][0]["height"]), float(d["format"]["duration"])


def face_center_x(path: Path) -> float:
    """Median horizontal position (0..1) of the largest face, sampled once a second. 0.5 when none is found."""
    try:
        import cv2
        det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        cap = cv2.VideoCapture(str(path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        xs, i = [], 0
        while len(xs) < 90:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(i * fps))
            ok, frame = cap.read()
            if not ok:
                break
            i += 1
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = det.detectMultiScale(gray, 1.15, 6, minSize=(frame.shape[0] // 9, frame.shape[0] // 9))
            if len(faces):
                x, _, w, _ = max(faces, key=lambda f: f[2] * f[3])
                xs.append((x + w / 2) / frame.shape[1])
        cap.release()
        return statistics.median(xs) if len(xs) >= 3 else 0.5
    except Exception:
        return 0.5


def caption_chunks(words: list[dict], start: float, end: float, max_words: int = 3, max_chars: int = 18) -> list[tuple[float, float, str]]:
    ws = [w for w in words if start <= w["t"] < end]
    groups: list[list[dict]] = []
    for w in ws:
        g = groups[-1] if groups else None
        if g and len(g) < max_words and sum(len(x["w"]) + 1 for x in g) + len(w["w"]) <= max_chars \
                and w["t"] - g[-1]["e"] < 0.6 and not re.search(r"[.?!]$", g[-1]["w"]):
            g.append(w)
        else:
            groups.append([w])
    out = []
    for i, g in enumerate(groups):
        t0 = g[0]["t"] - start
        nxt = groups[i + 1][0]["t"] - start if i + 1 < len(groups) else end - start
        t1 = nxt if nxt - (g[-1]["e"] - start) < 0.8 else g[-1]["e"] - start + 0.25
        out.append((max(t0, 0), min(max(t1, t0 + 0.2), end - start), " ".join(x["w"] for x in g)))
    return out


def _ts(t: float) -> str:
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _ass_colour(hex_rgb: str) -> str:
    h = hex_rgb.lstrip("#")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def write_ass(path: Path, chunks, style: str, duration: float, watermark: str, accent: str) -> None:
    acc = _ass_colour(accent)
    if style == "A":
        cap = f"Style: Cap,Arial,66,&H00FFFFFF,&H00FFFFFF,&H00E22B8A,&H00000000,-1,0,0,0,100,100,0,0,1,5,0,8,60,60,{PANEL_H + 46},1"
        mark = "Style: Mark,Arial,40,&H80FFFFFF,&H80FFFFFF,&HC0000000,&H00000000,-1,0,0,0,100,100,0,0,1,1,0,1,44,44,330,1"
    else:
        cap = f"Style: Cap,Arial,76,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,6,0,2,60,60,{H - (B_TOP + B_SIZE) + 150},1"
        mark = f"Style: Mark,Arial,36,&H80FFFFFF,&H80FFFFFF,&HC0000000,&H00000000,-1,0,0,0,100,100,2,0,1,1,0,2,44,44,{H - (B_TOP + B_SIZE) + 28},1"
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
             "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
             cap, mark, "", "[Events]",
             "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    if watermark:
        lines.append(f"Dialogue: 0,{_ts(0)},{_ts(duration)},Mark,,0,0,0,,{watermark}")
    for t0, t1, text in chunks:
        text = text.replace("{", "(").replace("}", ")").replace("\\", "/")
        if style == "A":
            text = "{\\blur3}" + text[:1].upper() + text[1:]
        else:
            toks = text.upper().split()
            key = max(range(len(toks)), key=lambda i: len(toks[i]))
            if len(toks) > 1 and len(toks[key]) >= 5:
                toks[key] = "{\\c" + acc[:2] + acc[4:] + "&}" + toks[key] + "{\\c&HFFFFFF&}"
            text = " ".join(toks)
        lines.append(f"Dialogue: 1,{_ts(t0)},{_ts(t1)},Cap,,0,0,0,,{text}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if cur and draw.textlength(trial, font=font) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + ([cur] if cur else [])


def _emoji_image(text: str, target_h: int = 96):
    """Colour emoji as an image. Segoe UI Emoji (Windows) scales freely; Noto Color Emoji (Linux runners) is a
    bitmap font that only loads at size 109, so it is drawn there and resized."""
    for p in FONTS_EMOJI:
        for size in (target_h, 109):
            try:
                f = ImageFont.truetype(p, size)
                im = Image.new("RGBA", (int(f.getlength(text)) + 16, int(size * 1.5)), (0, 0, 0, 0))
                ImageDraw.Draw(im).text((8, 4), text, font=f, embedded_color=True)
            except Exception:
                continue
            box = im.getbbox()
            if not box:
                continue
            im = im.crop(box)
            s = target_h / im.height
            return im.resize((max(1, int(im.width * s)), target_h), Image.LANCZOS)
    return None


def panel_png(path: Path, style: str, headline: str, emojis: str, accent: str) -> None:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if style == "A":
        size = 88
        while True:
            font = _font(FONTS_BOLD, size)
            lines = _wrap(d, headline, font, 960)
            if len(lines) <= 3 or size <= 56:
                break
            size -= 6
        lh = int(size * 1.16)
        em = _emoji_image(emojis.strip().replace(" ", "")) if emojis.strip() else None
        block = lh * len(lines) + (118 if em else 0)
        y = max(150, (PANEL_H + 110 - block) // 2)
        for ln in lines:
            d.text(((W - d.textlength(ln, font=font)) / 2, y), ln, font=font, fill=(0, 0, 0, 255))
            y += lh
        if em:
            img.alpha_composite(em, ((W - em.width) // 2, y + 14))
    else:
        top, bottom = (headline.split("|", 1) + [""])[:2]
        font = _font(FONTS_BOLD, 46)
        d.rectangle([0, B_TOP - 160, W, B_TOP - 14], fill=(255, 255, 255, 255))
        for i, (ln, col) in enumerate(((top, (0, 0, 0, 255)), (bottom, tuple(int(accent.lstrip("#")[j:j + 2], 16) for j in (0, 2, 4)) + (255,)))):
            ln = ln.strip().upper()
            f, s = font, 46
            while d.textlength(ln, font=f) > 1020 and s > 28:
                s -= 2
                f = _font(FONTS_BOLD, s)
            d.text(((W - d.textlength(ln, font=f)) / 2, B_TOP - 146 + i * 64), ln, font=f, fill=col)
    img.save(path)


def render(seg: Path, ass: Path, panel: Path, out: Path, style: str, face_x: float) -> None:
    sw, sh_, _ = probe(seg)
    box_h, top = (H - PANEL_H, PANEL_H) if style == "A" else (B_SIZE, B_TOP)
    scale = max(W / sw, box_h / sh_)
    tw, th = int(sw * scale / 2 + 0.5) * 2, int(sh_ * scale / 2 + 0.5) * 2
    x = int(min(max(face_x * tw - W / 2, 0), tw - W))
    y = (th - box_h) // 2
    grade = "" if style == "A" else "eq=saturation=1.18:contrast=1.06,"
    pad = "white" if style == "A" else "black"
    ov = "overlay=0:0" if style == "A" else f"overlay=0:0:enable='lt(t,{BANNER_S})'"
    graph = (f"[0:v]fps=30,scale={tw}:{th},crop={W}:{box_h}:{x}:{y},{grade}setsar=1,"
             f"pad={W}:{H}:0:{top}:{pad},subtitles={ass.name}[b];[b][1:v]{ov}[v]")
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", seg.name, "-i", panel.name, "-filter_complex", graph,
                        "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out.name],
                       cwd=seg.parent, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg failed: {(r.stderr or '')[-500:]}")


def make_clip(outdir: Path, creator: dict, video_id: str, info: dict, words: list[dict], clip: dict, cfg: dict) -> list[Path]:
    """Renders one finished video per headline option into outdir and writes info.json beside them."""
    outdir.mkdir(parents=True, exist_ok=True)
    seg = download_segment(video_id, clip["start"], clip["end"], outdir / "source.mp4")
    _, _, dur = probe(seg)
    style, accent = clip["style"], cfg.get("accent") or "#FF8A00"
    ass = outdir / "caps.ass"
    write_ass(ass, caption_chunks(words, clip["start"], clip["start"] + dur), style, dur, cfg.get("channel_name") or "", accent)
    fx = face_center_x(seg)
    outs = []
    for i, head in enumerate(clip["headlines"], 1):
        panel = outdir / f"panel{i}.png"
        panel_png(panel, style, head, clip.get("emojis") or "", accent)
        out = outdir / f"option{i}.mp4"
        render(seg, ass, panel, out, style, fx)
        outs.append(out)
    (outdir / "info.json").write_text(json.dumps({
        "creator": creator.get("name"), "verified": bool(creator.get("verified")), "rate": creator.get("rate"),
        "source": f"https://www.youtube.com/watch?v={video_id}&t={int(clip['start'])}s", "source_title": info.get("title"),
        "start": clip["start"], "end": clip["end"], "style": style, "headlines": clip["headlines"],
        "emojis": clip.get("emojis"), "upload_title": clip.get("title"), "why": clip.get("why"), "face_x": round(fx, 3),
    }, indent=1, ensure_ascii=False), encoding="utf-8")
    return outs


# ---- Style C: ranking (reference: project doc claude/clipping-style-ranking.md) ------------------------------------
R_TOP, R_H = 360, 1280           # clip box under the black title band
RANK_ORDER = [3, 5, 4, 2, 1]     # play order: the list fills in out of order and number 1 lands last
GOLD, RED, WHITE, BLACK = (255, 200, 40, 255), (255, 59, 48, 255), (255, 255, 255, 255), (0, 0, 0, 255)
# short falling "bloop" played as each entry lands; synthesized, so there is nothing to license
POP = "aevalsrc=0.5*sin(2*PI*(900-2400*t)*t)*exp(-16*t):d=0.25:s=48000"


def _split_emoji(label: str) -> tuple[str, str]:
    text = "".join(ch for ch in label if ord(ch) < 0x2190 and ord(ch) != 0x200D).strip()
    emoji = "".join(ch for ch in label if ord(ch) >= 0x2190 and ord(ch) != 0xFE0F)
    return text, emoji


def rank_frame_png(path: Path, title_plain: str, title_accent: str, labels: dict, shown: set, watermark: str) -> None:
    """Overlay for one clip: two-colour title on the top band, the list 1..5 down the left of the clip. Only the
    ranks in `shown` carry their label yet."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    size = 72
    while True:
        f = _font(FONTS_BOLD, size)
        wp, wa = d.textlength(title_plain + " ", font=f), d.textlength(title_accent, font=f)
        if wp + wa <= W - 80 or size <= 56:
            break
        size -= 4
    if wp + wa <= W - 80:
        x = (W - wp - wa) / 2
        d.text((x, 236), title_plain + " ", font=f, fill=WHITE)
        d.text((x + wp, 236), title_accent, font=f, fill=RED)
    else:
        for i, (text, col) in enumerate(((title_plain, WHITE), (title_accent, RED))):
            s = 70
            ff = _font(FONTS_BOLD, s)
            while d.textlength(text, font=ff) > W - 80 and s > 40:
                s -= 2
                ff = _font(FONTS_BOLD, s)
            d.text(((W - d.textlength(text, font=ff)) / 2, 166 + i * 86), text, font=ff, fill=col)
    num_f, lab_f = _font(FONTS_BOLD, 62), _font(FONTS_BOLD, 52)
    y = R_TOP + 80
    for rank in range(1, 6):
        d.text((44, y), f"{rank}.", font=num_f, fill=GOLD, stroke_width=5, stroke_fill=BLACK)
        if rank in shown:
            text, emoji = _split_emoji(labels[rank])
            d.text((136, y + 6), text, font=lab_f, fill=WHITE, stroke_width=5, stroke_fill=BLACK)
            em = _emoji_image(emoji, 56) if emoji else None
            if em:
                img.alpha_composite(em, (int(136 + d.textlength(text, font=lab_f) + 14), y + 8))
        y += 104
    if watermark:
        d.text((44, R_TOP + R_H - 76), watermark, font=_font(FONTS_BOLD, 36), fill=(255, 255, 255, 120),
               stroke_width=1, stroke_fill=(0, 0, 0, 110))
    img.save(path)


def write_ass_rank(path: Path, chunks) -> None:
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
             "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
             f"Style: Cap,Arial,62,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,6,0,2,60,60,{H - (R_TOP + R_H) + 70},1",
             "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for t0, t1, text in chunks:
        text = text.replace("{", "(").replace("}", ")").replace("\\", "/")
        lines.append(f"Dialogue: 1,{_ts(t0)},{_ts(t1)},Cap,,0,0,0,,{text[:1].upper() + text[1:]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _has_audio(path: Path) -> bool:
    r = sh(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)], timeout=60)
    return bool((r.stdout or "").strip())


def render_rank_part(seg: Path, panel: Path, ass: Path, out: Path, face_x: float) -> None:
    sw, sh_, dur = probe(seg)
    scale = max(W / sw, R_H / sh_)
    tw, th = int(sw * scale / 2 + 0.5) * 2, int(sh_ * scale / 2 + 0.5) * 2
    x = int(min(max(face_x * tw - W / 2, 0), tw - W))
    y = (th - R_H) // 2
    vf = (f"[0:v]fps=30,scale={tw}:{th},crop={W}:{R_H}:{x}:{y},setsar=1,pad={W}:{H}:0:{R_TOP}:black,"
          f"subtitles={ass.name}[b];[b][1:v]overlay=0:0[v]")
    stereo = "aformat=sample_rates=48000:channel_layouts=stereo"
    if _has_audio(seg):
        # short fades so one clip's sound does not snap into the next
        af = (f"[0:a]{stereo},afade=t=in:d=0.06,afade=t=out:st={max(dur - 0.2, 0):.3f}:d=0.2[a0];[2:a]{stereo}[p];"
              "[a0][p]amix=inputs=2:duration=first:normalize=0[a]")
    else:
        af = f"[2:a]{stereo},apad=whole_dur={dur:.3f}[a]"
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", seg.name, "-i", panel.name, "-f", "lavfi", "-i", POP,
                        "-filter_complex", vf + ";" + af, "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", out.name],
                       cwd=seg.parent, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg failed: {(r.stderr or '')[-400:]}")


def cut_local(src: Path, start: float, end: float, dest: Path) -> Path:
    """Cut [start, end] out of a file already on disk (Twitch clips are downloaded whole). Re-encoded, so the cut
    lands on the exact frame."""
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(start, 0):.3f}", "-i", str(src),
                        "-t", f"{max(end - start, 0.5):.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", str(dest)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    if r.returncode != 0 or not dest.exists() or dest.stat().st_size < 20_000:
        raise RuntimeError(f"cut failed for {src.name}: {(r.stderr or '')[-300:]}")
    return dest


def make_ranking(outdir: Path, plan: dict, words_by_id: dict, cfg: dict) -> Path:
    """Five clips from five different videos; the list fills in as each plays, number 1 last. Writes option1.mp4
    and info.json into outdir."""
    outdir.mkdir(parents=True, exist_ok=True)
    labels = {it["rank"]: it["label"] for it in plan["items"]}
    by_rank = {it["rank"]: it for it in plan["items"]}
    parts, shown, said = [], set(), {}
    for rank in RANK_ORDER:
        it = by_rank[rank]
        shown.add(rank)
        seg = (cut_local(Path(it["src_file"]), it["start"], it["end"], outdir / f"src{rank}.mp4") if it.get("src_file")
               else download_segment(it["video_id"], it["start"], it["end"], outdir / f"src{rank}.mp4"))
        _, _, dur = probe(seg)
        heard = " ".join(w["w"] for w in words_by_id.get(it["video_id"], []) if it["start"] <= w["t"] < it["start"] + dur)
        said[rank] = label_said(it["label"], heard)      # a number on the list that nobody says in the clip is suspect
        ass = outdir / f"caps{rank}.ass"
        # captions stop at the planned end, not the file's: the download can run a frame or two long, and the next
        # sentence's first word would flash on screen
        chunks = (caption_chunks(words_by_id.get(it["video_id"], []), it["start"], min(it["start"] + dur, it["end"]))
                  if plan.get("captions", True) else [])       # off for streams that already show live captions
        write_ass_rank(ass, chunks)
        panel = outdir / f"list{rank}.png"
        rank_frame_png(panel, plan["title_plain"], plan["title_accent"], labels, set(shown), cfg.get("channel_name") or "")
        part = outdir / f"part{rank}.mp4"
        render_rank_part(seg, panel, ass, part, face_center_x(seg))
        parts.append(part)
    (outdir / "parts.txt").write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    out = outdir / "option1.mp4"
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "parts.txt", "-c", "copy",
                        "-movflags", "+faststart", out.name], cwd=outdir, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"concat failed: {(r.stderr or '')[-400:]}")
    (outdir / "info.json").write_text(json.dumps({
        "style": "C", "theme": plan["theme"], "title": [plan["title_plain"], plan["title_accent"]],
        "upload_title": plan["upload_title"], "play_order": RANK_ORDER,
        "items": [{**{k: v for k, v in it.items() if k != "src_file"}, "label_said": said.get(it["rank"]),
                   "source": it.get("source_url") or f"https://www.youtube.com/watch?v={it['video_id']}&t={int(it['start'])}s"}
                  for it in plan["items"]],
    }, indent=1, ensure_ascii=False), encoding="utf-8")
    return out
