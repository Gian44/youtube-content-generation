"""The channel's stick figures. The writer never draws a person itself: it places ``<fig .../>`` tags and this
module expands each into the same hand-drawn figure every time (one look across every video), so only props and
backgrounds vary from scene to scene.

    <fig x="640" y="900" scale="1.2" pose="point_right" face="shocked" color="#2b6cb0" flip="0"/>

``x``/``y`` is the point on the ground between the feet. Height at scale 1 is about 210 units.
``jitter`` (0 = none) moves every joint a little, seeded, for the "boiling line" hand-drawn look."""

from __future__ import annotations

import math
import random

# angles in degrees from straight down; +90 = toward the viewer's right, 180 = straight up
# (l_upper, l_lower, r_upper, r_lower, l_thigh, l_shin, r_thigh, r_shin)
POSES: dict[str, tuple[float, ...]] = {
    "stand":       (-24, -12, 24, 12, -10, -6, 10, 6),
    "wave":        (-14, -8, 135, 170, -10, -6, 10, 6),
    "arms_up":     (-145, -170, 145, 170, -14, -8, 14, 8),
    "cheer":       (-130, -160, 130, 160, -22, -10, 22, 10),
    "point_right": (-18, -8, 88, 92, -10, -6, 10, 6),
    "point_left":  (-88, -92, 18, 8, -10, -6, 10, 6),
    "point_up":    (-14, -8, 160, 178, -10, -6, 10, 6),
    "shrug":       (-62, -150, 62, 150, -10, -6, 10, 6),
    "think":       (-14, -8, 35, 172, -10, -6, 10, 6),
    "facepalm":    (-14, -8, 25, 178, -10, -6, 10, 6),
    "hold":        (60, 100, 75, 100, -10, -6, 10, 6),
    "walk":        (22, 12, -22, -30, -24, -8, 26, 40),
    "run":         (70, 160, -60, -20, -55, -15, 50, 110),
    "scared":      (-45, -165, 45, 165, -24, -12, 24, 12),
    "sit":         (20, 70, 30, 80, 92, 2, 85, 0),
    "lie":         (-14, -8, 14, 8, -10, -6, 10, 6),
    "dizzy":       (-60, -30, 60, 30, -18, -4, 18, 4),
}
FACES = ("happy", "sad", "shocked", "neutral", "smirk", "scared", "angry", "sleepy", "grin", "worried")

HEAD_R, NECK, SHOULDER, HIP = 22.0, 156.0, 140.0, 82.0
UPPER_ARM, FOREARM, THIGH, SHIN = 38.0, 36.0, 42.0, 42.0
STROKE = 7.0
INK = "#1f1f1f"


def _seg(x: float, y: float, ang: float, length: float) -> tuple[float, float]:
    a = math.radians(ang)
    return x + math.sin(a) * length, y + math.cos(a) * length


def _f(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


def face_svg(face: str, cx: float, cy: float, s: float, ink: str) -> str:
    """Eyes and mouth inside a head of radius ``HEAD_R * s`` centred at (cx, cy)."""
    r = HEAD_R * s
    ex, ey, er = r * 0.38, cy - r * 0.15, max(1.8, 2.8 * s)
    w = STROKE * s * 0.45
    parts = []
    st = f'stroke="{ink}" stroke-width="{_f(w)}" stroke-linecap="round" fill="none"'
    if face == "sleepy":
        for sx in (-1, 1):
            parts.append(f'<path d="M{_f(cx + sx * ex - er * 1.4)},{_f(ey)} h{_f(er * 2.8)}" {st}/>')
    else:
        big = 1.5 if face in ("shocked", "scared") else 1.0
        for sx in (-1, 1):
            parts.append(f'<circle cx="{_f(cx + sx * ex)}" cy="{_f(ey)}" r="{_f(er * big)}" fill="{ink}"/>')
    if face == "angry":
        for sx in (-1, 1):
            parts.append(f'<path d="M{_f(cx + sx * ex * 1.6)},{_f(ey - r * 0.32)} L{_f(cx + sx * ex * 0.4)},{_f(ey - r * 0.18)}" {st}/>')
    if face == "worried":
        for sx in (-1, 1):
            parts.append(f'<path d="M{_f(cx + sx * ex * 1.5)},{_f(ey - r * 0.2)} L{_f(cx + sx * ex * 0.5)},{_f(ey - r * 0.34)}" {st}/>')
    my, mw = cy + r * 0.42, r * 0.42
    mouth = {
        "happy": f"M{_f(cx - mw)},{_f(my - r * .08)} Q{_f(cx)},{_f(my + r * .32)} {_f(cx + mw)},{_f(my - r * .08)}",
        "grin": f"M{_f(cx - mw)},{_f(my - r * .12)} Q{_f(cx)},{_f(my + r * .42)} {_f(cx + mw)},{_f(my - r * .12)} Z",
        "sad": f"M{_f(cx - mw)},{_f(my + r * .14)} Q{_f(cx)},{_f(my - r * .2)} {_f(cx + mw)},{_f(my + r * .14)}",
        "worried": f"M{_f(cx - mw)},{_f(my + r * .1)} Q{_f(cx)},{_f(my - r * .12)} {_f(cx + mw)},{_f(my + r * .1)}",
        "neutral": f"M{_f(cx - mw * .8)},{_f(my)} H{_f(cx + mw * .8)}",
        "angry": f"M{_f(cx - mw * .8)},{_f(my + r * .05)} H{_f(cx + mw * .8)}",
        "sleepy": f"M{_f(cx - mw * .5)},{_f(my)} H{_f(cx + mw * .5)}",
        "smirk": f"M{_f(cx - mw * .7)},{_f(my + r * .05)} Q{_f(cx + mw * .3)},{_f(my + r * .12)} {_f(cx + mw)},{_f(my - r * .15)}",
        "scared": (f"M{_f(cx - mw)},{_f(my)} l{_f(mw / 2)},{_f(-r * .1)} l{_f(mw / 2)},{_f(r * .1)} "
                   f"l{_f(mw / 2)},{_f(-r * .1)} l{_f(mw / 2)},{_f(r * .1)}"),
    }.get(face)
    if face == "shocked":
        parts.append(f'<ellipse cx="{_f(cx)}" cy="{_f(my + r * .05)}" rx="{_f(r * .16)}" ry="{_f(r * .22)}" fill="{ink}"/>')
    elif face == "grin" and mouth:
        parts.append(f'<path d="{mouth}" fill="#ffffff" stroke="{ink}" stroke-width="{_f(w)}" stroke-linejoin="round"/>')
    elif mouth:
        parts.append(f'<path d="{mouth}" {st}/>')
    return "".join(parts)


def figure_svg(*, x: float, y: float, scale: float = 1.0, pose: str = "stand", face: str = "neutral",
               color: str = INK, flip: bool = False, jitter: float = 0.0, seed: int | str = 0) -> str:
    """One stick figure as an SVG ``<g>``, feet on (x, y)."""
    pose = pose if pose in POSES else "stand"
    face = face if face in FACES else "neutral"
    la1, la2, ra1, ra2, lt, ls, rt, rs = POSES[pose]
    rng = random.Random(f"{seed}|{x}|{y}|{pose}")
    j = (lambda: rng.uniform(-jitter, jitter)) if jitter else (lambda: 0.0)
    # local coordinates: hip at (0, -HIP) above the ground, y down
    hip = (0.0, -HIP)
    sh = (j() * .5, -SHOULDER)
    neck = (sh[0], -NECK)
    head = (neck[0] + j() * .6, -NECK - HEAD_R)
    pts = {}
    pts["le"] = _seg(*sh, la1 + j(), UPPER_ARM)
    pts["lh"] = _seg(*pts["le"], la2 + j(), FOREARM)
    pts["re"] = _seg(*sh, ra1 + j(), UPPER_ARM)
    pts["rh"] = _seg(*pts["re"], ra2 + j(), FOREARM)
    pts["lk"] = _seg(*hip, lt + j(), THIGH)
    pts["lf"] = _seg(*pts["lk"], ls + j(), SHIN)
    pts["rk"] = _seg(*hip, rt + j(), THIGH)
    pts["rf"] = _seg(*pts["rk"], rs + j(), SHIN)
    ground = max(pts["lf"][1], pts["rf"][1], 0.0 if pose != "sit" else -999)
    if pose == "sit":
        ground = max(pts["lf"][1], pts["rf"][1])
    dy = -ground                       # feet (lowest point) on y=0
    w = STROKE
    c = color or INK
    def P(p):
        return f"{_f(p[0])},{_f(p[1] + dy)}"
    body = (f'<path d="M{P(pts["lh"])} L{P(pts["le"])} L{P(sh)} L{P(pts["re"])} L{P(pts["rh"])}" '
            f'stroke="{c}" stroke-width="{w}" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
            f'<path d="M{P(pts["lf"])} L{P(pts["lk"])} L{P(hip)} L{P(pts["rk"])} L{P(pts["rf"])}" '
            f'stroke="{c}" stroke-width="{w}" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
            f'<path d="M{P(neck)} L{P(hip)}" stroke="{c}" stroke-width="{w}" stroke-linecap="round"/>')
    hx, hy = head[0], head[1] + dy
    head_svg = (f'<circle cx="{_f(hx)}" cy="{_f(hy)}" r="{HEAD_R}" fill="#ffffff" stroke="{INK}" stroke-width="{w}"/>'
                + face_svg(face, hx, hy, 1.0, INK))
    extra = ""
    if pose == "dizzy":
        extra = "".join(f'<circle cx="{_f(hx + dx)}" cy="{_f(hy - 36 + abs(dx) * .2)}" r="4" fill="{INK}"/>' for dx in (-22, 0, 22))
    sx = -scale if flip else scale
    rot = ' rotate(-90)' if pose == "lie" else ""
    return (f'<g transform="translate({_f(x)},{_f(y)}) scale({_f(sx)},{_f(scale)}){rot}">'
            f"{body}{head_svg}{extra}</g>")
