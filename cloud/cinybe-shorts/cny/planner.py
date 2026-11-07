"""Plot text → acts → long recap script + Shorts (two writer calls), validated, then the no-AI-slop pass.

Ported from StoryFactory's ``services/recap_planner.py`` (feat/narrated-recap) onto ``sof.llm.LLM`` (Claude on
the subscription, OpenAI/Gemini fallback). The writer only uses facts in the plot text; a validation failure is
sent back once with the errors; ``noslop`` (mode ``recap``) rewrites only the flagged paragraphs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from sof import noslop
from sof.llm import LLM, LLMError

log = logging.getLogger("cny.planner")
LONG_FORM_TOLERANCE = (0.65, 1.35)      # LLMs miss tight word bands; the voice decides the real length anyway


class PlanError(RuntimeError):
    """Planner output failed validation (after one retry) or the model errored."""


@dataclass
class FilmPlan:
    acts: list[dict]
    long_form: dict
    shorts: list[dict]
    slop: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"acts": self.acts, "long_form": self.long_form, "shorts": self.shorts, "slop": self.slop}

    @classmethod
    def from_dict(cls, d: dict) -> "FilmPlan":
        return cls(acts=list(d.get("acts") or []), long_form=dict(d.get("long_form") or {}),
                   shorts=list(d.get("shorts") or []), slop=dict(d.get("slop") or {}))

    @property
    def sections(self) -> list[dict]:
        """``[{"heading", "body"}]`` in act order — the shape ``sof.tts.chunk_sections`` takes."""
        by_idx = {int(a["index"]): a for a in self.acts}
        out = []
        for s in sorted(self.long_form.get("sections") or [], key=lambda x: int(x["act_index"])):
            act = by_idx.get(int(s["act_index"]), {})
            out.append({"heading": str(act.get("title") or ""), "body": str(s.get("text") or "").strip(),
                        "act_index": int(s["act_index"])})
        return out


ACTS_SYSTEM = ("You are a story editor for a movie-recap YouTube channel. You only use facts present in the plot "
               "text you are given; you never invent scenes, names or outcomes. Output strict JSON.")

ACTS_PROMPT = """Film: {title} ({year})
About the film: {lead}
Genres: {genres} · Director: {director} · Cast: {cast}

Split the plot below into {acts_min}-{acts_max} ordered acts that together tell the whole story, from setup
to ending. Each act is one clear story beat.

Return a JSON object {{"acts": [...]}}. Each act:
{{"index": <0-based int>, "title": "<2-6 word act title, no spoiler in the title>",
 "summary": "<30-60 words, present tense, concrete>",
 "broll_queries": ["<3-6 word stock-footage search: a place, weather, object or action, generic, no names>",
                   "<a second, different search for the same mood>"],
 "stakes": "<one short clause: what is at risk now>"}}

Rules: indices contiguous from 0; the final act is the ending and says what happens; nothing withheld; no
commentary; broll searches describe things that exist in stock footage (city at night, rain on a window,
hospital corridor, desert road), never characters or the film.

PLOT:
{plot_text}"""

SCRIPTS_SYSTEM = ("You write narration for a movie-recap channel. Voice: confident, vivid, present tense, plain words; "
                  "no filler, no 'in this video', no 'let's dive in'. You never add facts that are not in the acts. "
                  "Output strict JSON.")

SCRIPTS_PROMPT = """Film: {title} ({year})
Acts (JSON): {acts_json}

Write two things.

1. "long_form": the full narrated recap, about {target_words} words in total, roughly {per_section_words}
   words per act, one section per act in act order. Each section is narration only, no headings. Open the
   first section with a two-sentence hook that raises the film's central question (say the film's name and
   year in the first three sentences). The last section tells the ending and closes with one plain line.
   Also give "title" (a curiosity-driven line of at most 60 characters that names the film, no clickbait
   lies, no hashtags, no colon), "description" (2-3 sentences, spoiler warning included),
   "thumb_text" (2-4 words for the thumbnail, no punctuation) and "tags" (6-10 lowercase search tags:
   film, director, genre, key nouns).

2. "shorts": exactly {shorts_per_film} Shorts, each from a DIFFERENT act_index, preferring the most dramatic
   acts. Each Short: "hook" (at most 12 words, the first sentence spoken), "text" (the full narration
   INCLUDING the hook, at most {short_max_words} words, ending on a cliffhanger or a payoff), "title"
   (at most 60 characters, names the film, no hashtags), "tags" (4-8 lowercase tags).

Return JSON: {{"long_form": {{"title", "description", "thumb_text", "tags", "sections": [{{"act_index", "text"}}]}},
              "shorts": [{{"act_index", "hook", "text", "title", "tags"}}]}}"""

EDIT_SYSTEM = ("You are a line editor for spoken narration. You change wording only; every event, name and outcome "
               "stays exactly as written. Output strict JSON.")
STYLE_BLOCK = noslop.style_block("recap")


# ----------------------------------------------------------------- validation (pure)

def validate_acts(data, *, acts_min: int, acts_max: int) -> list[dict]:
    acts = data.get("acts") if isinstance(data, dict) else data
    errors: list[str] = []
    if not isinstance(acts, list):
        raise PlanError("acts must be a JSON array under \"acts\"")
    if not (acts_min <= len(acts) <= acts_max):
        errors.append(f"expected {acts_min}-{acts_max} acts, got {len(acts)}")
    for i, a in enumerate(acts):
        if not isinstance(a, dict):
            errors.append(f"act {i} is not an object")
            continue
        try:
            idx = int(a.get("index", -1))
        except (TypeError, ValueError):
            idx = -1
        if idx != i:
            errors.append(f"act {i} has index {a.get('index')}; indices must be contiguous from 0")
        if not str(a.get("summary", "")).strip():
            errors.append(f"act {i} has an empty summary")
        if not str(a.get("title", "")).strip():
            errors.append(f"act {i} has an empty title")
        q = a.get("broll_queries")
        if isinstance(q, str):
            q = [q]
        q = [str(x).strip() for x in (q or []) if str(x).strip()]
        if not q and str(a.get("broll_query", "")).strip():
            q = [str(a["broll_query"]).strip()]
        if not q:
            errors.append(f"act {i} has no broll_queries")
        a["broll_queries"] = q[:3]
    if errors:
        raise PlanError("; ".join(errors))
    return [dict(a, index=i) for i, a in enumerate(acts)]


def validate_scripts(data, acts: list[dict], *, target_words: int, shorts_per_film: int, short_max_words: int) -> dict:
    errors: list[str] = []
    if not isinstance(data, dict):
        raise PlanError("scripts must be a JSON object")
    valid_idx = {int(a["index"]) for a in acts}
    lf = data.get("long_form") or {}
    sections = lf.get("sections") or []
    if not str(lf.get("title", "")).strip():
        errors.append("long_form.title is empty")
    if len(sections) != len(acts):
        errors.append(f"long_form.sections must have {len(acts)} entries (one per act), got {len(sections)}")
    total = sum(len(str(s.get("text", "")).split()) for s in sections if isinstance(s, dict))
    lo_w, hi_w = int(LONG_FORM_TOLERANCE[0] * target_words), int(LONG_FORM_TOLERANCE[1] * target_words)
    if not (lo_w <= total <= hi_w):
        errors.append(f"long_form total words {total} outside {lo_w}-{hi_w} (target {target_words}); aim for about "
                      f"{target_words // max(1, len(acts))} words per section")
    seen_sections: set[int] = set()
    for s in sections:
        if not isinstance(s, dict):
            errors.append("long_form section is not an object")
            continue
        try:
            sidx = int(s.get("act_index", -1))
        except (TypeError, ValueError):
            sidx = -1
        if sidx not in valid_idx:
            errors.append(f"long_form section has unknown act_index {s.get('act_index')}")
        elif sidx in seen_sections:
            errors.append(f"long_form has two sections for act_index {sidx}")
        seen_sections.add(sidx)
        if not str(s.get("text", "")).strip():
            errors.append("long_form section has empty text")
    shorts = data.get("shorts") or []
    if len(shorts) != shorts_per_film:
        errors.append(f"expected exactly {shorts_per_film} shorts, got {len(shorts)}")
    seen: set[int] = set()
    for i, s in enumerate(shorts):
        if not isinstance(s, dict):
            errors.append(f"short {i} is not an object")
            continue
        try:
            idx = int(s.get("act_index", -1))
        except (TypeError, ValueError):
            idx = -1
        if idx not in valid_idx:
            errors.append(f"short {i} has unknown act_index {idx}")
        if idx in seen:
            errors.append(f"short {i} reuses act_index {idx}; each Short needs a different act")
        seen.add(idx)
        words = len(str(s.get("text", "")).split())
        if words == 0 or words > short_max_words:
            errors.append(f"short {i} text is {words} words; must be 1-{short_max_words}")
        if not str(s.get("hook", "")).strip():
            errors.append(f"short {i} has an empty hook")
        if not str(s.get("title", "")).strip():
            errors.append(f"short {i} has an empty title")
    if errors:
        raise PlanError("; ".join(errors))
    lf["tags"] = [str(t).strip().lower() for t in (lf.get("tags") or []) if str(t).strip()]
    for s in shorts:
        s["tags"] = [str(t).strip().lower() for t in (s.get("tags") or []) if str(t).strip()]
    return data


# ----------------------------------------------------------------- planning

def _call(llm: LLM, prompt: str, system: str, validate, *, temperature: float, max_tokens: int) -> object:
    """One writer call; on PlanError the errors go back once with the same prompt."""
    try:
        return validate(llm.generate_json(prompt, system=system, temperature=temperature, max_tokens=max_tokens))
    except PlanError as first:
        log.warning("plan rejected, retrying once: %s", str(first)[:300])
        retry = (f"{prompt}\n\nYour previous answer was rejected. Validation errors:\n{first}\n"
                 "Return corrected JSON that satisfies every rule.")
        return validate(llm.generate_json(retry, system=system, temperature=temperature, max_tokens=max_tokens))


def plan_film(llm: LLM, *, title: str, year: int | None, plot_text: str, lead: str, meta: dict, target_words: int,
              acts_range: tuple[int, int], shorts_per_film: int, short_max_words: int, noslop_rounds: int = 1,
              fast_model: str | None = None) -> FilmPlan:
    acts_min, acts_max = acts_range
    acts_prompt = ACTS_PROMPT.format(
        title=title, year=year or "n/a", lead=(lead or "n/a")[:1200],
        genres=", ".join(meta.get("genres") or []) or "n/a", director=meta.get("director") or "n/a",
        cast=", ".join(meta.get("cast") or []) or "n/a", acts_min=acts_min, acts_max=acts_max, plot_text=plot_text)
    try:
        acts = _call(llm, acts_prompt, ACTS_SYSTEM, lambda d: validate_acts(d, acts_min=acts_min, acts_max=acts_max),
                     temperature=0.2, max_tokens=4000)
    except LLMError as exc:
        raise PlanError(f"acts call failed: {exc}") from exc
    scripts_prompt = SCRIPTS_PROMPT.format(
        title=title, year=year or "n/a", acts_json=json.dumps(acts, ensure_ascii=False), target_words=target_words,
        per_section_words=max(40, target_words // max(1, len(acts))), shorts_per_film=shorts_per_film,
        short_max_words=short_max_words)
    scripts_prompt = noslop.inject(scripts_prompt, STYLE_BLOCK, before="Return JSON")
    try:
        scripts = _call(llm, scripts_prompt, SCRIPTS_SYSTEM,
                        lambda d: validate_scripts(d, acts, target_words=target_words, shorts_per_film=shorts_per_film,
                                                   short_max_words=short_max_words),
                        temperature=0.5, max_tokens=max(6000, int(target_words * 2.5)))
    except LLMError as exc:
        raise PlanError(f"scripts call failed: {exc}") from exc
    slop = despop(llm, scripts, title=title, rounds=noslop_rounds, short_max_words=short_max_words, fast_model=fast_model)
    return FilmPlan(acts=acts, long_form=scripts["long_form"], shorts=scripts["shorts"], slop=slop)


def _one_para(text: str) -> str:
    return " ".join(str(text or "").split())


def despop(llm: LLM, scripts: dict, *, title: str, rounds: int, short_max_words: int, fast_model: str | None) -> dict:
    """No-AI-slop pass over the validated scripts (long-form sections as one multi-paragraph text, Shorts likewise,
    one model call per round). A Short that would exceed ``short_max_words`` keeps its text. Never raises."""
    rounds = max(0, int(rounds or 0))

    def rewrite(prompt: str) -> dict:
        return llm.generate_json(prompt, system=EDIT_SYSTEM, temperature=0.0, model=fast_model, max_tokens=6000)

    extra = f"This is a spoken recap of the film {title}; keep the present tense and every plot event."
    stats: dict[str, dict] = {}
    try:
        sections = [s for s in (scripts.get("long_form") or {}).get("sections") or [] if isinstance(s, dict)]
        if sections:
            res = noslop.polish("\n\n".join(_one_para(s.get("text")) for s in sections), rewrite=rewrite, mode="recap",
                                rounds=rounds, length_band=(0.75, 1.25), extra=extra)
            paras = noslop.paragraphs(res.text)
            if len(paras) == len(sections):            # a deleted paragraph would shift the act mapping
                for s, p in zip(sections, paras):
                    s["text"] = p
            stats["long_form"] = res.to_dict()
        shorts = [s for s in scripts.get("shorts") or [] if isinstance(s, dict)]
        if shorts:
            res = noslop.polish("\n\n".join(_one_para(s.get("text")) for s in shorts), rewrite=rewrite, mode="recap",
                                rounds=rounds, length_band=(0.75, 1.2), extra=extra)
            paras = noslop.paragraphs(res.text)
            if len(paras) == len(shorts):
                for s, p in zip(shorts, paras):
                    if len(p.split()) <= short_max_words and p != _one_para(s.get("text")):
                        s["text"] = p
                        if str(s.get("hook") or "").strip() not in p:
                            s["hook"] = noslop.sentences(p)[0]
            stats["shorts"] = res.to_dict()
    except Exception as exc:  # noqa: BLE001 — the editor must never cost the plan
        log.warning("noslop pass failed: %s", exc)
    return {k: {"before": v["before"]["findings"], "after": v["after"]["findings"], "rewritten": v["rewritten"]}
            for k, v in stats.items()}
