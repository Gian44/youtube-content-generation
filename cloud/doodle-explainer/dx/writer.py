"""Everything the writer (Claude on Gian's subscription, via ``sof.llm``) is asked for: the question, the script,
a fact check, the drawings, the metadata.

Originality is the point of the channel (YouTube's 2026-10-01 Shorts change holds back channels that re-post other
creators' footage; sylusnix sank under it): every word is written fresh and every frame is drawn for the video. No
footage, photos or clips of anyone else, ever."""

from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass, field

from dx import scenes

log = logging.getLogger("dx.writer")


class WriterError(RuntimeError):
    pass


CHANNEL_VOICE = """You write for a faceless YouTube channel of stick-figure explainers that answer questions everyone
has wondered about (why we yawn, myths that turned out true, engineering projects that almost happened). Tone: curious,
quick, dry humour, like a smart friend explaining at a party. Plain words, short sentences, concrete numbers and
examples. Second person is fine. Never padding, never "in this video", never "let's dive in", never "buckle up",
never "fascinating", "delve", "tapestry", "testament", "intricate". Facts must be well established and checkable;
when science is unsure, say so in one line instead of picking a side. No medical advice. Nothing about real private
people; real public figures only for documented public facts."""

STYLE_GUIDE = """DRAWING RULES (follow exactly)
- You write SVG ELEMENTS ONLY (no <svg> root, no <defs>, no <image>, no <style>, no filters, no scripts) for a canvas
  {w} wide and {h} tall, origin top-left, y down. The paper background is already there (cream). Use single quotes
  for every attribute value.
- People are ALWAYS <fig/> tags, never drawn by hand: <fig x='..' y='..' scale='..' pose='..' face='..' color='..' flip='0'/>
  x,y = the point on the ground between the feet. Height is about 210*scale. Typical scale {fig_scale}.
  poses: {poses}. faces: {faces}. color = the body line colour (use #2b6cb0 for the main character "the curious one",
  #c0392b for a second character, #1f1f1f for extras). flip='1' mirrors.
- Everything else is simple hand-drawn shapes: path, circle, ellipse, rect (rx for rounded), line, polygon, g.
  Ink #1f1f1f, stroke-width 6 (4 for small details), stroke-linecap='round', stroke-linejoin='round'.
  Flat fills from this palette only: #f6c343 yellow, #e8743b orange, #c0392b red, #2b6cb0 blue, #7fb3d5 light blue,
  #58a65c green, #a3d39c light green, #8e6c4a brown, #d9d2c3 grey, #ffffff white, #1f1f1f ink.
- Draw recognisable objects from a few shapes (a phone = rounded rect + screen; a sun = circle + short rays).
  Bold, simple, readable on a phone. Fill the frame: the main subject large, no big empty areas, nothing important
  within 40 units of an edge.{vertical}
- On-screen text: at most 6 words per scene, only labels, numbers or a one-word reaction ("ouch", "1,000x", "2.5 m").
  NEVER write the narration on screen. font-size at least {min_font}, font-weight='bold', fill='#1f1f1f'.
- Reveals: give an element (usually a <g>) data-at='0.3' to make it appear 30% into the scene, timed to the moment the
  narration names it. 1 to 3 reveals per scene. The figure(s) are visible from the start.
- Vary the composition from scene to scene: close-ups (scale 3-4 figure cut off by the frame bottom), wide shots,
  diagrams with arrows, before/after splits, a giant object next to a tiny figure.
"""


@dataclass
class Scene:
    say: str
    svg: str = ""
    section: int = 0


@dataclass
class Script:
    kind: str                      # "long" | "short"
    question: str
    title: str
    hook: str
    scenes: list[Scene]
    sections: list[str] = field(default_factory=list)
    description: str = ""
    tags: list[str] = field(default_factory=list)
    thumb_svg: str = ""
    category: str = ""

    @property
    def words(self) -> int:
        return sum(len(s.say.split()) for s in self.scenes)

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["scenes"] = [s.__dict__ for s in self.scenes]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Script":
        d = dict(d)
        d["scenes"] = [Scene(**s) for s in d["scenes"]]
        return cls(**d)


def _canvas(kind: str) -> tuple[int, int]:
    return (1920, 1080) if kind == "long" else (1080, 1920)


def style_guide(kind: str) -> str:
    from dx import figures

    w, h = _canvas(kind)
    vertical = ("\n- VERTICAL phone frame: keep the action between y=300 and y=1500 (the top has the phone's status bar,"
                " the bottom 400 has YouTube's buttons and title). Stack things vertically, not side by side.") if kind == "short" else ""
    return STYLE_GUIDE.format(w=w, h=h, fig_scale="1.6 to 2.6" if kind == "long" else "2.2 to 3.4",
                              poses=", ".join(figures.POSES), faces=", ".join(figures.FACES), vertical=vertical,
                              min_font=48 if kind == "long" else 64)


# ------------------------------------------------------------------ question

def pick_question(llm, *, kind: str, categories: list[str], used: list[str], seed: str, fast_model: str | None = None) -> dict:
    rng = random.Random(f"{seed}|{kind}")
    cats = rng.sample(categories, k=min(3, len(categories)))
    want = "a 9-minute explainer" if kind == "long" else "a 45-second Short"
    prompt = f"""Pick ONE question for {want} on the channel.

Categories to draw from today: {", ".join(cats)}.
Good questions are ones millions of people have idly wondered about, with a surprising, solid answer
(e.g. "Why do we get goosebumps?", "What if you fell into the Mariana Trench?", "Why can't we tickle ourselves?",
"Ancient myths that turned out to be true"). {"A long video can be a list (5 to 8 items) or one deep question." if kind == "long" else "A Short is ONE narrow question with one clear payoff."}
Never one of these (already made):
{chr(10).join("- " + u for u in used[-120:]) or "- (none yet)"}

Return JSON: {{"question": "...", "category": "one of the categories", "why_it_works": "one line"}}"""
    out = llm.generate_json(prompt, system=CHANNEL_VOICE, max_tokens=400, model=fast_model)
    q = str(out.get("question") or "").strip()
    if not q:
        raise WriterError("no question returned")
    return {"question": q, "category": str(out.get("category") or cats[0])}


# ------------------------------------------------------------------ script

def write_script(llm, *, kind: str, question: str, words_min: int, words_max: int) -> dict:
    if kind == "long":
        shape = f"""A 8-10 minute narrated video, {words_min}-{words_max} words in total, in 5 to 8 sections.
Each section has a short heading (2-5 words, used as a YouTube chapter) and 5 to 9 scenes.
Each scene = 1 to 3 sentences of narration (15-40 words) that one drawing can show. The first scene is the hook
(the most surprising thing, in under 12 seconds); the last section ends on a satisfying payoff, then one line
inviting a comment (a question to the viewer, not "like and subscribe")."""
        schema = '{"title": "...", "hook": "...", "sections": [{"heading": "...", "scenes": [{"say": "..."}]}]}'
    else:
        shape = f"""A YouTube Short of {words_min}-{words_max} words, 6 to 9 scenes, each 1 to 2 sentences.
Scene 1 is the hook: the question or the surprising answer in under 3 seconds of speech. Build to one clear payoff;
the last line lands the answer or a twist (no "follow for more")."""
        schema = '{"title": "...", "hook": "...", "sections": [{"heading": "", "scenes": [{"say": "..."}]}]}'
    prompt = f"""Write the narration for: {question}

{shape}
Title: under 60 characters, curiosity without lying, no clickbait caps, no emoji, no hashtags.
Return JSON exactly in this shape: {schema}"""
    out = llm.generate_json(prompt, system=CHANNEL_VOICE, max_tokens=8000)
    if not out.get("sections"):
        raise WriterError("script has no sections")
    return out


def fact_check(llm, script: dict) -> dict:
    """A second read by the writer with one job: find claims that are wrong or shaky and fix those lines only."""
    lines = [s["say"] for sec in script["sections"] for s in sec.get("scenes", [])]
    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(lines))
    prompt = f"""You are a strict fact checker. Below are the narration lines of an explainer video.
Find every claim that is false, outdated, a popular myth presented as fact, or stated more confidently than the
evidence allows. For each, give a corrected line of similar length in the same tone. Leave good lines alone.

{numbered}

Return JSON: {{"fixes": [{{"line": <number>, "problem": "...", "new": "..."}}]}} (empty list if all is fine)."""
    out = llm.generate_json(prompt, max_tokens=4000)
    fixes = {int(f["line"]): str(f["new"]).strip() for f in out.get("fixes") or [] if str(f.get("line", "")).isdigit() and f.get("new")}
    k = 0
    for sec in script["sections"]:
        for s in sec.get("scenes", []):
            if k in fixes:
                log.info("fact fix line %d: %s → %s", k, s["say"][:60], fixes[k][:60])
                s["say"] = fixes[k]
            k += 1
    script["_fact_fixes"] = len(fixes)
    return script


# ------------------------------------------------------------------ drawings

def draw_batch(llm, *, kind: str, title: str, batch: list[Scene], context: str) -> list[str]:
    numbered = "\n".join(f"{i}. {s.say}" for i, s in enumerate(batch))
    prompt = f"""{style_guide(kind)}
VIDEO: {title}
{context}
Draw one scene for each narration line below. Each drawing must SHOW what the line says (a metaphor is fine), so a
viewer with the sound off still follows the idea.

{numbered}

Return JSON: {{"scenes": [{{"i": 0, "svg": "<fig x='...' .../> <path d='...' .../> ..."}}]}} with one entry per line, in order."""
    out = llm.generate_json(prompt, max_tokens=16000)
    got = {int(d.get("i", -1)): str(d.get("svg") or "") for d in out.get("scenes") or [] if isinstance(d, dict)}
    return [got.get(i, "") for i in range(len(batch))]


def fallback_svg(kind: str, n: int) -> str:
    """A plain scene when a drawing cannot be fixed: the curious one thinking (never a text card)."""
    w, h = _canvas(kind)
    poses = ["think", "shrug", "point_up"]
    y = h * 0.82 if kind == "long" else h * 0.72
    s = 2.4 if kind == "long" else 3.2
    return (f"<path d='M40,{y:.0f} Q{w / 2:.0f},{y - 14:.0f} {w - 40},{y:.0f}' stroke='#1f1f1f' stroke-width='6' fill='none' stroke-linecap='round'/>"
            f"<fig x='{w / 2:.0f}' y='{y:.0f}' scale='{s}' pose='{poses[n % 3]}' face='worried' color='#2b6cb0'/>"
            f"<g data-at='0.3'><circle cx='{w / 2 + 170 * s / 2.4:.0f}' cy='{y - 300 * s / 2.4:.0f}' r='{38 * s / 2.4:.0f}' fill='#f6c343' stroke='#1f1f1f' stroke-width='6'/></g>")


def draw_all(llm, script: Script, *, batch_size: int) -> int:
    """Fill ``scene.svg`` for every scene; a broken drawing is asked for once more, then falls back. Returns the
    number of fallbacks."""
    w, h = _canvas(script.kind)
    todo = [i for i, s in enumerate(script.scenes) if not s.svg]
    fallbacks = 0
    for start in range(0, len(todo), batch_size):
        idx = todo[start:start + batch_size]
        ctx = "Scenes before these: " + " / ".join(script.scenes[j].say[:80] for j in range(max(0, idx[0] - 2), idx[0]))
        for attempt in range(2):
            want = [i for i in idx if not script.scenes[i].svg]
            if not want:
                break
            try:
                svgs = draw_batch(llm, kind=script.kind, title=script.title, batch=[script.scenes[i] for i in want], context=ctx)
            except Exception as exc:  # noqa: BLE001
                log.warning("drawing batch failed: %s", exc)
                continue
            for i, svg in zip(want, svgs):
                why = scenes.check(svg, width=w, height=h) if svg else "missing"
                if why is None:
                    script.scenes[i].svg = svg
                else:
                    log.warning("scene %d drawing rejected (try %d): %s", i, attempt + 1, why)
        for i in idx:
            if not script.scenes[i].svg:
                script.scenes[i].svg = fallback_svg(script.kind, i)
                fallbacks += 1
        print(f"drawings {min(start + batch_size, len(todo))}/{len(todo)}", flush=True)
    return fallbacks


def draw_thumbnail(llm, script: Script) -> str:
    prompt = f"""{style_guide("long")}
Draw the THUMBNAIL for the video "{script.title}" (hook: {script.hook}).
One big, instantly readable idea: a large figure (scale 3 to 4.5) with an extreme expression reacting to one big
object, plus at most 4 words of text in font-size 130 or more, with a thick #1f1f1f outline look (draw the text
twice: first stroke='#1f1f1f' stroke-width='16', then fill='#ffffff' or '#f6c343' on top). High contrast, no clutter.
No data-at attributes.
Return JSON: {{"svg": "..."}}"""
    out = llm.generate_json(prompt, max_tokens=6000)
    svg = str(out.get("svg") or "")
    why = scenes.check(svg, width=1920, height=1080) if svg else "missing"
    if why:
        log.warning("thumbnail rejected: %s", why)
    return "" if why else svg


# ------------------------------------------------------------------ metadata

def metadata(llm, script: Script, *, fast_model: str | None = None) -> dict:
    text = " ".join(s.say for s in script.scenes)[:3500]
    prompt = f"""Write YouTube metadata for this {"video" if script.kind == "long" else "Short"}.
Title idea: {script.title}
Narration: {text}

Return JSON: {{"title": "under 60 chars, curiosity, no clickbait caps, no emoji",
"description": "2-3 plain sentences on what the viewer learns (no hashtags, no links)",
"tags": ["8 to 12 search phrases"]}}"""
    out = llm.generate_json(prompt, system=CHANNEL_VOICE, max_tokens=800, model=fast_model)
    return {"title": str(out.get("title") or script.title)[:95], "description": str(out.get("description") or ""),
            "tags": [str(t)[:60] for t in out.get("tags") or []][:15]}


def build(kind: str, question: str, raw: dict, category: str) -> Script:
    sc: list[Scene] = []
    heads: list[str] = []
    for k, sec in enumerate(raw.get("sections") or []):
        heads.append(str(sec.get("heading") or "").strip())
        for s in sec.get("scenes") or []:
            say = re.sub(r"\s+", " ", str(s.get("say") or "")).strip()
            if say:
                sc.append(Scene(say=say, section=k))
    if len(sc) < 4:
        raise WriterError(f"only {len(sc)} scenes")
    return Script(kind=kind, question=question, title=str(raw.get("title") or question)[:95],
                  hook=str(raw.get("hook") or sc[0].say), scenes=sc, sections=heads, category=category)


def dump(script: Script) -> str:
    return json.dumps(script.to_dict(), ensure_ascii=False, indent=1)
