"""Scene drawings: the writer's SVG body → checked, expanded (``<fig>`` tags become the channel's stick figures),
and rendered to PNG frames.

Motion without animation software:
- "boil": every frame set is drawn 3 times with each top-level element nudged ~2 px (seeded), cycled at 8 fps —
  the wobbling-line look of hand-drawn explainers;
- reveals: an element with ``data-at="0.4"`` appears 40 % of the way into its scene (props popping in as the
  narration reaches them)."""

from __future__ import annotations

import random
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from dx import figures

SVG_NS = "http://www.w3.org/2000/svg"
PAPER = "#fbf7ee"
import os
FONT = os.environ.get("DX_FONT", "Comic Neue")          # fonts-comic-neue on the runner (OFL); cairosvg takes one family
ALLOWED = {"g", "path", "circle", "ellipse", "rect", "line", "polyline", "polygon", "text", "tspan", "fig"}
BANNED_ATTR = re.compile(r"^(on|href|xlink|style$|filter$|mask$|clip-path$)", re.I)
BOIL_VARIANTS = 3


class SceneError(ValueError):
    pass


@dataclass
class Layer:
    at: float           # fraction of the scene's time when it appears (0 = from the start)
    xml: str


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def parse_body(body: str) -> list[ET.Element]:
    """The writer's elements (no <svg> root) → top-level elements. Raises SceneError on anything unusable."""
    body = re.sub(r"^\s*```(?:svg|xml)?|```\s*$", "", body.strip())
    body = re.sub(r"<\?xml[^>]*>|<!DOCTYPE[^>]*>|</?svg[^>]*>", "", body)
    try:
        root = ET.fromstring(f"<root>{body}</root>")
    except ET.ParseError as exc:
        raise SceneError(f"svg does not parse: {exc}") from exc
    kids = list(root)
    if not kids:
        raise SceneError("svg is empty")
    for el in root.iter():
        if el is root:
            continue
        name = _local(el.tag)
        if name not in ALLOWED:
            raise SceneError(f"element <{name}> not allowed")
        for k in list(el.attrib):
            if BANNED_ATTR.match(_local(k)):
                del el.attrib[k]
    return kids


def _fig(el: ET.Element, jitter: float, seed) -> str:
    a = el.attrib
    def num(k, d):
        try:
            return float(a.get(k, d))
        except ValueError:
            return d
    return figures.figure_svg(x=num("x", 0), y=num("y", 0), scale=max(.2, min(4.0, num("scale", 1))), pose=a.get("pose", "stand"),
                              face=a.get("face", "neutral"), color=a.get("color", figures.INK),
                              flip=a.get("flip", "0") in ("1", "true", "yes"), jitter=jitter, seed=seed)


def _serialize(el: ET.Element, jitter: float, seed) -> str:
    """One top-level element as SVG text, ``<fig>`` expanded (also inside groups)."""
    if _local(el.tag) == "fig":
        return _fig(el, jitter, seed)
    attrs = "".join(f' {_local(k)}="{_esc(v)}"' for k, v in el.attrib.items() if _local(k) != "data-at")
    name = _local(el.tag)
    if name == "text" and "font-family" not in el.attrib:
        attrs += f' font-family="{FONT}"'
    inner = _esc(el.text or "") + "".join(_serialize(c, jitter, seed) + _esc(c.tail or "") for c in el)
    return f"<{name}{attrs}>{inner}</{name}>"


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def reveal_times(kids: list[ET.Element]) -> list[float]:
    """Distinct appearance fractions, 0 first."""
    ts = {0.0}
    for el in kids:
        try:
            ts.add(max(0.0, min(0.9, float(el.attrib.get("data-at", 0)))))
        except ValueError:
            pass
    return sorted(ts)


def compose(kids: list[ET.Element], *, width: int, height: int, upto: float, variant: int, seed) -> str:
    """Full SVG document of the elements visible at fraction ``upto``, boil variant ``variant``."""
    rng = random.Random(f"{seed}|{variant}")
    jit = 2.2 if variant else 0.0
    out = []
    for i, el in enumerate(kids):
        try:
            at = float(el.attrib.get("data-at", 0))
        except ValueError:
            at = 0.0
        if at > upto + 1e-6:
            continue
        dx, dy = (rng.uniform(-jit, jit), rng.uniform(-jit, jit)) if jit else (0.0, 0.0)
        out.append(f'<g transform="translate({dx:.2f},{dy:.2f})">{_serialize(el, 2.0 if variant else 0.8, f"{seed}|{variant}|{i}")}</g>')
    return (f'<svg xmlns="{SVG_NS}" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<rect width="{width}" height="{height}" fill="{PAPER}"/>' + "".join(out) + "</svg>")


def render_png(svg: str, out: str, *, width: int, height: int) -> str:
    import cairosvg

    cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=out, output_width=width, output_height=height)
    return out


def render_scene(body: str, out_dir: str | Path, *, width: int, height: int, seed) -> list[tuple[float, list[str]]]:
    """Render every reveal state × boil variant. Returns [(at, [png per variant]), ...] in time order."""
    kids = parse_body(body)
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    states = []
    for si, at in enumerate(reveal_times(kids)):
        pngs = []
        for v in range(BOIL_VARIANTS):
            p = d / f"st{si}_v{v}.png"
            if not p.exists():
                render_png(compose(kids, width=width, height=height, upto=at, variant=v, seed=seed), str(p), width=width, height=height)
            pngs.append(str(p))
        states.append((at, pngs))
    return states


def check(body: str, *, width: int, height: int) -> str | None:
    """None when the drawing is usable, else why not (parse errors, nothing drawn, no figure and no shape)."""
    try:
        kids = parse_body(body)
    except SceneError as exc:
        return str(exc)
    shapes = sum(1 for el in kids for x in el.iter() if _local(x.tag) != "text")
    if shapes == 0:
        return "only text, no drawing"
    words = sum(len((x.text or "").split()) for el in kids for x in el.iter() if _local(x.tag) in ("text", "tspan"))
    if words > 8:
        return f"{words} words of on-screen text (at most 8)"
    try:
        render_png(compose(kids, width=width, height=height, upto=1.0, variant=0, seed=0),
                   str(Path("/tmp") / "dx_check.png"), width=width // 8, height=height // 8)
    except Exception as exc:  # noqa: BLE001
        return f"does not render: {exc}"
    return None
