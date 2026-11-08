"""The recap, written from the film itself: the subtitle transcript (dialogue with timestamps) → acts that carry
their own time range in the film → long recap narration + Shorts, validated, then the no-AI-slop pass.

Gian, 2026-11-08, after eight sample channels (Movie Recaps, Twist Recaps, Film Decode, AniRoll, …): the script
comes from the subtitles (Wikipedia's plot is optional context), the title is a premise-and-twist hook that never
names the film, the footage follows the narration from start to finish. People are introduced once by what they
are and then called by name — "a professional hitman named Hyung-woo", "Michael, a writer" — exactly as the
samples' captions do (first render, 2026-11-08, used descriptors only and sounded robotic). The script call gets
each act's own dialogue so it narrates scene by scene and quotes a line now and then. Two writer calls via
``sof.llm.LLM`` (Claude on the subscription).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from sof import noslop
from sof.llm import LLM, LLMError

log = logging.getLogger("cny.planner")
LONG_FORM_TOLERANCE = (0.7, 1.3)
ACT_MIN_SECONDS = 20.0


class PlanError(RuntimeError):
    """Planner output failed validation (after one retry) or the model errored."""


@dataclass
class FilmPlan:
    acts: list[dict]            # index, title, summary, start_seconds, end_seconds (in the film)
    long_form: dict             # title (the hook), description, thumb_text, tags, sections[{act_index, text}]
    shorts: list[dict]          # act_index, hook, text, title, tags
    slop: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"acts": self.acts, "long_form": self.long_form, "shorts": self.shorts, "slop": self.slop}

    @classmethod
    def from_dict(cls, d: dict) -> "FilmPlan":
        return cls(acts=list(d.get("acts") or []), long_form=dict(d.get("long_form") or {}),
                   shorts=list(d.get("shorts") or []), slop=dict(d.get("slop") or {}))

    @property
    def sections(self) -> list[dict]:
        """``[{"heading", "body", "act_index"}]`` in act order — the shape ``sof.tts.chunk_sections`` takes."""
        by_idx = {int(a["index"]): a for a in self.acts}
        out = []
        for s in sorted(self.long_form.get("sections") or [], key=lambda x: int(x["act_index"])):
            act = by_idx.get(int(s["act_index"]), {})
            out.append({"heading": str(act.get("title") or ""), "body": str(s.get("text") or "").strip(),
                        "act_index": int(s["act_index"])})
        return out


ACTS_SYSTEM = ("You are a story editor for a movie-recap YouTube channel. You work from the film's own dialogue transcript "
               "(with timestamps) and only from it, plus any reference summary given; you never invent scenes or outcomes. "
               "Output strict JSON.")

ACTS_PROMPT = """Film: {title} ({year}) · runtime {duration} seconds.
{reference}
Below is the film's dialogue as "[mm:ss] text" lines (one line per 10 seconds that has dialogue). Read the whole film,
then split its story into {acts_min}-{acts_max} acts in screen order, from the opening to the ending. Each act is
one clear story beat and names the time range of the film where it plays out.

Return a JSON object {{"acts": [...], "people": [...]}}. Each act:
{{"index": <0-based int>, "title": "<2-6 word act title, no spoiler>",
 "summary": "<40-80 words, present tense, concrete: who does what, where, and what it changes>",
 "start_seconds": <number>, "end_seconds": <number>,
 "stakes": "<one short clause: what is at risk now>"}}
Each person in "people": {{"name": "<as in the dialogue>", "descriptor": "<2-5 words: who they are when we first meet
them, e.g. 'a professional hitman', 'a poor actor', 'her older brother'>"}} — the main characters only (3-10).

Rules: indices contiguous from 0; acts in order; time ranges in order, not overlapping, inside the runtime, each at
least {act_min} seconds and together covering most of the film; skip the opening titles and the end credits; the final
act is the ending and says what happens; nothing withheld; no commentary.

TRANSCRIPT:
{transcript}"""

SCRIPTS_SYSTEM = ("You write narration for a movie-recap YouTube channel in the style of the big recap channels (Movie Recaps, "
                  "Mystery Recapped): present tense, fast, concrete, telling what happens on screen as it happens, one event "
                  "after another, in plain spoken words; no filler, no 'in this video', no 'let's dive in', no moralising, no "
                  "commentary on the filmmaking. Each person is introduced once by who they are and their name ('a drifter "
                  "named Ben', 'Barbra, a young woman visiting her father's grave') and from then on called by name or he/she; "
                  "unnamed people stay plain descriptions ('the sheriff', 'a news reporter'). You never add facts that are not in "
                  "the acts or the dialogue. Output strict JSON.")

SCRIPTS_PROMPT = """Film: {title} ({year})
People (name, and who they are when first met): {people_json}
Acts (JSON): {acts_json}

DIALOGUE PER ACT — the film's own lines inside each act's time range ("[mm:ss] text"); use it for what people say and do,
in the order it happens:
{dialogue}

Write two things.

1. "long_form": the full narrated recap, about {target_words} words in total, roughly {per_section_words} words per act,
   one section per act in act order. Each section is narration only, no headings; it tells what happens in that act,
   scene by scene and moment by moment, in present tense, with the concrete details the dialogue gives (who says what,
   what they decide, what goes wrong). Now and then quote a short line word for word from the dialogue, the way recap
   channels do: He tells her, "They're coming to get you." Introduce each person the first time they appear (who they
   are + name) and use the name after that. The first section opens with one or two sentences on the strangest premise
   or the opening event, then "The story begins…" or straight into the first scene — do NOT say the film's name or
   year anywhere in the narration. The last section tells the ending and closes with one plain line.
   Also give:
   - "title": a YouTube hook of 60-100 characters in Title Case: the premise, then the twist, the way the big recap
     channels write them — e.g. "CIA Built the Most Sadistic Kill Team to Hunt Terrorists But It Quickly Spirals Out of
     Control", "Poor Man Wants to Die, But Accidentally Becomes a Millionaire", "Wounded Soldier Wakes Up in a Secret Lab
     and Discovers He Cannot Die". Never the film's name, never a person's name; no question, no colon, no clickbait
     lies, no "| Movie Recap".
   - "thumb_text": 3-5 words for the thumbnail, no punctuation (e.g. "HE PLANNED EVERY DEATH").
   - "description": 2-3 sentences that DO name the film and year, with a spoiler warning.
   - "tags": 8-12 lowercase search tags: the film's name, director, genre, "movie recap", key nouns.

2. "shorts": exactly {shorts_per_film} Shorts, each from a DIFFERENT act_index, the most dramatic moments. Each Short:
   "hook" (at most 14 words, the first sentence spoken, a concrete event), "text" (the full narration INCLUDING the
   hook, {short_min_words}-{short_max_words} words, present tense, people introduced as in the long form, ending on a
   cliffhanger or a payoff; no film name), "title" (a premise-and-twist hook of at most 70 characters, no film name, no
   person's name, no hashtags), "tags" (4-8 lowercase tags).

Return JSON: {{"long_form": {{"title", "thumb_text", "description", "tags", "sections": [{{"act_index", "text"}}]}},
              "shorts": [{{"act_index", "hook", "text", "title", "tags"}}]}}"""

EDIT_SYSTEM = ("You are a line editor for spoken narration. You change wording only; every event, name and outcome "
               "stays exactly as written. Output strict JSON.")
STYLE_BLOCK = noslop.style_block("recap")


# ----------------------------------------------------------------- the dialogue each act covers (pure)

_LINE = re.compile(r"^\[(\d+):(\d\d)\]\s*(.*)$")


def act_dialogue(transcript: str, acts: list[dict], *, max_chars: int = 60000) -> str:
    """Each act's transcript lines (those whose time falls inside the act's range), as one block per act, the whole
    trimmed to ``max_chars`` (every act keeps a fair share; long acts lose their tail lines)."""
    lines: list[tuple[float, str]] = []
    for raw in transcript.splitlines():
        m = _LINE.match(raw.strip())
        if m:
            lines.append((int(m.group(1)) * 60 + int(m.group(2)), raw.strip()))
    per = max(400, max_chars // max(1, len(acts)))
    blocks = []
    for a in acts:
        s, e = float(a["start_seconds"]), float(a["end_seconds"])
        mine = [t for sec, t in lines if s - 5 <= sec <= e]
        body = "\n".join(mine)
        if len(body) > per:
            body = body[:per].rsplit("\n", 1)[0] + "\n[…]"
        mm = lambda x: f"{int(x) // 60:02d}:{int(x) % 60:02d}"
        blocks.append(f"Act {a['index']} ({mm(s)}-{mm(e)}):\n" + (body or "(no dialogue — describe the action from the summary)"))
    return "\n\n".join(blocks)


# ----------------------------------------------------------------- validation (pure)

def _num(v, name: str) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        raise PlanError(f"{name} is not a number: {v!r}")


def validate_acts(data, *, acts_min: int, acts_max: int, duration: float, act_min_seconds: float = ACT_MIN_SECONDS) -> tuple[list[dict], list[dict]]:
    """(acts, people). Acts must be contiguous, in screen order, with ordered non-overlapping time ranges inside the
    runtime; the people list is optional."""
    acts = data.get("acts") if isinstance(data, dict) else data
    people = (data.get("people") if isinstance(data, dict) else None) or []
    errors: list[str] = []
    if not isinstance(acts, list):
        raise PlanError("acts must be a JSON array under \"acts\"")
    if not (acts_min <= len(acts) <= acts_max):
        errors.append(f"expected {acts_min}-{acts_max} acts, got {len(acts)}")
    prev_end = -1.0
    out: list[dict] = []
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
        try:
            s, e = _num(a.get("start_seconds"), f"act {i} start_seconds"), _num(a.get("end_seconds"), f"act {i} end_seconds")
        except PlanError as exc:
            errors.append(str(exc))
            continue
        if s < 0 or e > duration + 1.0:
            errors.append(f"act {i} time range {s:.0f}-{e:.0f} is outside the runtime (0-{duration:.0f})")
        if e - s < act_min_seconds:
            errors.append(f"act {i} time range is {e - s:.0f}s; must be at least {act_min_seconds:g}s")
        if s < prev_end:
            errors.append(f"act {i} starts before act {i - 1} ends; ranges must be in order and not overlap")
        prev_end = max(prev_end, e)
        out.append(dict(a, index=i, start_seconds=round(s, 2), end_seconds=round(e, 2)))
    if errors:
        raise PlanError("; ".join(errors))
    people = [{"name": str(p.get("name") or "").strip(), "descriptor": str(p.get("descriptor") or "").strip()}
              for p in people if isinstance(p, dict) and str(p.get("name") or "").strip()][:12]
    return out, people


def _names_film(text: str, film: str) -> bool:
    words = [w for w in re.findall(r"[a-z0-9]+", film.lower()) if len(w) > 3 and w not in ("the", "and", "part", "film", "movie")]
    t = text.lower()
    return bool(words) and all(w in t for w in words)


def _names_person(text: str, people: list[dict]) -> str | None:
    """The first person's name (any word of it, 3+ letters) found as a whole word in ``text``."""
    words = set(re.findall(r"[a-z][a-z'-]+", text.lower()))
    for p in people or []:
        for part in re.findall(r"[a-z][a-z'-]+", str(p.get("name") or "").lower()):
            if len(part) >= 3 and part in words and part not in ("the", "man", "mrs", "miss", "mister", "doctor"):
                return str(p.get("name"))
    return None


def validate_scripts(data, acts: list[dict], *, film: str, target_words: int, shorts_per_film: int, short_max_words: int,
                     people: list[dict] | None = None) -> dict:
    errors: list[str] = []
    if not isinstance(data, dict):
        raise PlanError("scripts must be a JSON object")
    valid_idx = {int(a["index"]) for a in acts}
    lf = data.get("long_form") or {}
    sections = lf.get("sections") or []
    title = re.sub(r"\s*\|\s*movie recap\s*$", "", str(lf.get("title", "")).strip(), flags=re.I)
    named = _names_person(title, people or [])
    if not title:
        errors.append("long_form.title is empty")
    elif _names_film(title, film):
        errors.append("long_form.title must not name the film")
    elif named:
        errors.append(f"long_form.title must not use a person's name ({named}); say who they are instead")
    elif not (40 <= len(title) <= 110):
        errors.append(f"long_form.title is {len(title)} characters; aim for 60-100")
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
        text = str(s.get("text", ""))
        if not text.strip():
            errors.append("long_form section has empty text")
        elif _names_film(text, film):
            errors.append(f"section {sidx} names the film; the narration never says the film's name")
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
        st = str(s.get("title", "")).strip()
        if not st:
            errors.append(f"short {i} has an empty title")
        elif _names_film(st, film):
            errors.append(f"short {i} title must not name the film")
        elif _names_person(st, people or []):
            errors.append(f"short {i} title must not use a person's name")
    if errors:
        raise PlanError("; ".join(errors))
    lf["title"] = title
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


def plan_film(llm: LLM, *, title: str, year: int | None, transcript: str, duration: float, plot_text: str = "",
              target_words: int, acts_range: tuple[int, int], shorts_per_film: int, short_max_words: int,
              noslop_rounds: int = 1, fast_model: str | None = None, dialogue_max_chars: int = 60000) -> FilmPlan:
    acts_min, acts_max = acts_range
    reference = (f"A published plot summary, for reference only (the transcript decides what is on screen):\n{plot_text[:6000]}\n"
                 if plot_text.strip() else "")
    acts_prompt = ACTS_PROMPT.format(title=title, year=year or "n/a", duration=int(duration), reference=reference,
                                     acts_min=acts_min, acts_max=acts_max, act_min=int(ACT_MIN_SECONDS), transcript=transcript)
    try:
        acts, people = _call(llm, acts_prompt, ACTS_SYSTEM,
                             lambda d: validate_acts(d, acts_min=acts_min, acts_max=acts_max, duration=duration),
                             temperature=0.2, max_tokens=6000)
    except LLMError as exc:
        raise PlanError(f"acts call failed: {exc}") from exc
    scripts_prompt = SCRIPTS_PROMPT.format(
        title=title, year=year or "n/a", people_json=json.dumps(people, ensure_ascii=False),
        acts_json=json.dumps([{k: a[k] for k in ("index", "title", "summary", "stakes") if k in a} for a in acts], ensure_ascii=False),
        dialogue=act_dialogue(transcript, acts, max_chars=dialogue_max_chars),
        target_words=target_words, per_section_words=max(40, target_words // max(1, len(acts))),
        shorts_per_film=shorts_per_film, short_max_words=short_max_words, short_min_words=max(20, short_max_words * 2 // 3))
    scripts_prompt = noslop.inject(scripts_prompt, STYLE_BLOCK, before="Return JSON")
    try:
        scripts = _call(llm, scripts_prompt, SCRIPTS_SYSTEM,
                        lambda d: validate_scripts(d, acts, film=title, target_words=target_words, shorts_per_film=shorts_per_film,
                                                   short_max_words=short_max_words, people=people),
                        temperature=0.5, max_tokens=max(8000, int(target_words * 2.5)))
    except LLMError as exc:
        raise PlanError(f"scripts call failed: {exc}") from exc
    slop = despop(llm, scripts, title=title, rounds=noslop_rounds, short_max_words=short_max_words, fast_model=fast_model)
    scripts["long_form"]["people"] = people
    return FilmPlan(acts=acts, long_form=scripts["long_form"], shorts=scripts["shorts"], slop=slop)


def _one_para(text: str) -> str:
    return " ".join(str(text or "").split())


def despop(llm: LLM, scripts: dict, *, title: str, rounds: int, short_max_words: int, fast_model: str | None) -> dict:
    """No-AI-slop pass over the validated scripts (long-form sections as one multi-paragraph text, Shorts likewise,
    one model call per round). A Short that would exceed ``short_max_words`` keeps its text. Never raises."""
    rounds = max(0, int(rounds or 0))

    def rewrite(prompt: str) -> dict:
        return llm.generate_json(prompt, system=EDIT_SYSTEM, temperature=0.0, model=fast_model, max_tokens=8000)

    extra = ("This is a spoken recap of a film; keep the present tense, every plot event, every name, and every quoted "
             "line word for word; never add the film's name.")
    stats: dict[str, dict] = {}
    try:
        sections = [s for s in (scripts.get("long_form") or {}).get("sections") or [] if isinstance(s, dict)]
        if sections:
            res = noslop.polish("\n\n".join(_one_para(s.get("text")) for s in sections), rewrite=rewrite, mode="recap",
                                rounds=rounds, length_band=(0.75, 1.25), extra=extra)
            paras = noslop.paragraphs(res.text)
            if len(paras) == len(sections):
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
