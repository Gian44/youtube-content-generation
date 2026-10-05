"""cast_bible — character bible + Flow prompt pack for the cast library.

One recurring AI character per voice persona. Its 8-second reaction clips are
generated ONCE in Google Flow (Veo 3.1, Frames to Video from one master frame)
and stored in a local bank. The story's own TTS narrates over the clips, so the
character NEVER speaks in any clip.

This module:

* writes the character "bible" (``character.json``) via one Gemini JSON call
  (``build_bible`` / ``write_bible`` / ``load_bible``),
* derives the per-clip motion prompts (``clip_prompt``), and
* renders the human-facing pack the user pastes into Flow
  (``write_pack`` / ``write_checklist``).

The bible mirrors a proven UGC avatar JSON (name / references / identity_lock /
voice_lock / setting / hero_action) adapted for a non-speaking character with no
reference photos: ``identity_lock`` doubles as the reference, ``voice_lock`` is
replaced by the hard no-speech rule inside ``base_motion``, and ``hero_action``
becomes ``emotion_actions`` (one list of reaction sentences per emotion tag).

Imports only ``content_defaults`` so ``db.seed`` can import the prompt template
from here without a cycle.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from storyfactory.content_defaults import CAST_EMOTION_TAGS

# ============================================================
# Constants
# ============================================================

NO_SPEECH_RULE = (
    "The character does not speak; lips stay closed. "
    "No captions, no on-screen text, no subtitles, no music."
)

BASE_MOTION_OPENER = "Continuous shot: phone propped still at chest height, vertical 9:16"

# Veo 3.1 credit costs in Google Flow (Google AI Pro plan).
CREDITS_FAST = 20
CREDITS_LITE = 10

TEXT_FIELDS: tuple[str, ...] = (
    "name",
    "age_range",
    "identity_lock",
    "setting",
    "base_motion",
    "master_frame_prompt",
)
KNOWN_KEYS: tuple[str, ...] = TEXT_FIELDS + ("emotion_actions",)
META_KEYS: tuple[str, ...] = ("persona", "created_at")

BIBLE_SYSTEM = f"""You write character bibles for AI video generation (Google Flow / Veo 3.1).
The character is a recurring, NON-SPEAKING on-camera presence for vertical short-form fiction videos. A separate narrator voice is laid over the clips later, so the character only listens and reacts.

Hard rules:
- Output STRICT JSON only. No markdown fences, no commentary, no trailing text.
- The character never speaks, mouths words or sings. Lips stay closed in every clip. {NO_SPEECH_RULE}
- Exactly ONE fixed identity (face, hair, skin, build, wardrobe) and ONE fixed room, described once and reused verbatim; never invent alternates.
- Realistic, candid, phone-shot look. No text, no captions, no watermark, no logos, no brand names, no real people, no celebrities.
- Every reaction must be visible within the first second of a clip and then hold; do not describe multi-stage scenes.
- Never re-describe the face inside a reaction sentence; the identity lock already covers it."""

BIBLE_PROMPT_TEMPLATE = """Create the character bible for a recurring, non-speaking on-camera character.

Channel niche: {{niche}}
Content style: {{content_style}}
Narrator persona: {{persona}}

The persona name is the TONE of the narrator voice that will be laid over the clips (e.g. "calm", "dramatic", "sarcastic", "confession", "horror", "warm"). The character's look, wardrobe, age and room must fit that tone and the channel niche. Pick a realistic, ordinary-looking person (not a model), an age range that reads credible for the niche, and a room that a real person of that age would live in.

Write these fields exactly (all strings unless noted):

1. "name": a first name (one or two words).
2. "age_range": e.g. "late 20s", "mid 40s".
3. "identity_lock": ONE paragraph that names the EXACT same face, hair, skin, build and wardrobe every time: face shape and distinctive features, eye color, eyebrows, hair color/length/style, skin tone and texture (moles, freckles, stubble if any), build and height impression, and the fixed outfit down to color and fabric. This sentence opens every prompt and is the identity anchor, so it must be specific enough that a generator draws the same person twice.
4. "setting": ONE fixed, lived-in room described once: room type, wall color, two or three specific background objects, the light source (a window), the time-of-day feel. The character is always in this room.
5. "base_motion": the shared motion description appended to every clip prompt. It MUST open with exactly "Continuous shot: phone propped still at chest height, vertical 9:16" and then, in order: the framing (head and shoulders, centered, camera does not move), a one-line pointer to the setting, natural micro-movements (glances, blinks, small nods, slight weight shifts, breathing), warm natural light from the window and quiet room tone, and it MUST end with exactly: "__NO_SPEECH_RULE__"
6. "master_frame_prompt": the still-image prompt for the master frame, in this order: the identity_lock paragraph; then a neutral listening pose (relaxed face, eyes on camera, closed mouth, shoulders level) in the setting; then the framing: propped-iPhone selfie framing about 1.2 m away, head and shoulders, vertical 9:16, natural window light, candid, faint grain, no retouching, no text, no watermark.
7. "emotion_actions" (object): for EACH of these emotion tags, exactly {{variants_per_tag}} action sentences:
   {{emotion_tags}}
   Each sentence describes ONE reaction that develops within the first second and then holds for the rest of the clip (a body-language and expression beat: eyes, brows, mouth shape while closed, head, shoulders, hands). Each of the {{variants_per_tag}} variants for a tag must be a clearly different physical reaction, and every sentence across ALL tags must open with different words (the first eight words are used to tell clips apart). Never re-describe the face or the room, never mention speaking, talking, mouthing, whispering words, or sound. For "whisper_secret" show leaning in and a conspiratorial, hushed body language with lips closed, not actual whispering.

Return strict JSON with exactly these keys:
{
  "name": "...",
  "age_range": "...",
  "identity_lock": "...",
  "setting": "...",
  "base_motion": "Continuous shot: phone propped still at chest height, vertical 9:16 ... __NO_SPEECH_RULE__",
  "master_frame_prompt": "...",
  "emotion_actions": {
    "neutral_listening": ["...", "..."],
    "shocked": ["...", "..."]
  }
}
Include every tag listed above as a key of "emotion_actions" with exactly {{variants_per_tag}} strings each.""".replace("__NO_SPEECH_RULE__", NO_SPEECH_RULE)

FLOW_HOWTO = """## How to generate these in Google Flow

Use **one Flow project per persona** so the master frame and all its clips stay together.

**Step 1 — master frame (once).** Open Flow's *image* tool, paste the master frame prompt below and generate. Re-roll until the face reads right: this frame is the identity anchor for every clip, so do not settle. Download the image and save it as `<persona>/reference.png` next to this file's `outbox/` folder.

**Step 2 — clips.** For each clip section below: choose *Frames to Video*, set the **first frame** to `reference.png`, paste the motion prompt, pick **Veo 3.1 Fast** (or *Lite* if credits are tight), **9:16**, **one output**, **{clip_seconds} seconds**. Audio does not matter — it is stripped at render time and the story's narration is laid over the clip. Download the **1080p MP4** and save it into `inbox/` under the exact filename given in its section. A second take of the same clip goes in as an alternate: `inbox/<tag>_<n>_alt1.mp4` (then `_alt2`, ...).

**Step 3 — scan.** Run `npm run worker -- cast scan --channel <slug>`. The scan sorts the inbox into the bank and writes `first_frames_grid.png`; check the grid and re-roll any clip where the face drifted from the master frame.
"""


# ============================================================
# Prompt rendering
# ============================================================

def render_prompt(template: str, **values) -> str:
    """Replace each ``{{key}}`` in *template* with ``str(value)``.

    Lists (e.g. ``emotion_tags``) are joined with ``", "``.
    """
    out = template
    for key, value in values.items():
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value)
        out = out.replace("{{" + key + "}}", str(value))
    return out


# ============================================================
# Validation
# ============================================================

def _is_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_bible(data: dict, variants_per_tag: int) -> dict:
    """Validate and clean a bible dict returned by the model.

    Raises ``ValueError`` listing every missing/invalid field. Returns a cleaned
    copy containing only the known keys (plus ``persona``/``created_at`` when
    present). Extra emotion sentences are trimmed to *variants_per_tag*, unknown
    tags are dropped, and NO_SPEECH_RULE is appended to ``base_motion`` when it
    lacks the "lips stay closed" clause.
    """
    if not isinstance(data, dict):
        raise ValueError(f"bible must be a JSON object, got {type(data).__name__}")
    variants_per_tag = int(variants_per_tag)
    if variants_per_tag < 1:
        raise ValueError("variants_per_tag must be >= 1")

    errors: list[str] = []
    cleaned: dict = {}

    for field in TEXT_FIELDS:
        value = data.get(field)
        if _is_text(value):
            cleaned[field] = value.strip()
        else:
            errors.append(f"{field}: missing or empty string")

    actions = data.get("emotion_actions")
    cleaned_actions: dict[str, list[str]] = {}
    if not isinstance(actions, dict):
        errors.append("emotion_actions: missing or not an object")
    else:
        for tag in CAST_EMOTION_TAGS:
            raw = actions.get(tag)
            if not isinstance(raw, list):
                errors.append(f"emotion_actions.{tag}: missing or not a list")
                continue
            sentences = [s.strip() for s in raw if _is_text(s)]
            if len(sentences) < variants_per_tag:
                errors.append(
                    f"emotion_actions.{tag}: {len(sentences)} sentence(s), "
                    f"need {variants_per_tag}"
                )
                continue
            cleaned_actions[tag] = sentences[:variants_per_tag]
        # unknown tags are dropped silently: they have no folder in the bank

    if errors:
        raise ValueError("invalid character bible: " + "; ".join(errors))

    cleaned["emotion_actions"] = cleaned_actions

    if "lips stay closed" not in cleaned["base_motion"]:
        cleaned["base_motion"] = cleaned["base_motion"] + " " + NO_SPEECH_RULE

    # Inbox matching keys on the first 40 normalized characters of each clip
    # prompt (action sentence first), so two sentences that open alike would
    # misfile clips. Refuse the bible rather than discover it at scan time.
    from storyfactory.services.cast_library import normalize_key

    seen: dict[str, str] = {}
    for tag, sentences in cleaned_actions.items():
        for n, sentence in enumerate(sentences, 1):
            key = normalize_key(sentence + " " + cleaned["base_motion"])
            if key in seen:
                raise ValueError(
                    f"invalid character bible: emotion_actions {seen[key]} and {tag}[{n}] "
                    f"open with the same words ({key!r}); re-run cast init"
                )
            seen[key] = f"{tag}[{n}]"

    for key in META_KEYS:
        if key in data:
            cleaned[key] = data[key]
    return cleaned


# ============================================================
# Build (one Gemini JSON call)
# ============================================================

def build_bible(
    client,
    *,
    model: str,
    persona: str,
    niche: str | None,
    content_style: str | None,
    variants_per_tag: int,
    template: str = BIBLE_PROMPT_TEMPLATE,
    temperature: float = 0.7,
) -> dict:
    """Ask the model for the bible, validate it and stamp persona/created_at.

    *client* is duck-typed: anything with
    ``generate_json(model, prompt, system=..., temperature=...)`` (in production
    ``services.gemini_rest.Gemini``).
    """
    prompt = render_prompt(
        template,
        persona=persona,
        niche=niche or "Reddit-style fiction stories",
        content_style=content_style or "candid, phone-shot, realistic",
        emotion_tags=CAST_EMOTION_TAGS,
        variants_per_tag=variants_per_tag,
    )
    data = client.generate_json(model, prompt, system=BIBLE_SYSTEM, temperature=temperature)
    bible = validate_bible(data, variants_per_tag)
    bible["persona"] = persona
    bible["created_at"] = datetime.now(timezone.utc).isoformat()
    return bible


# ============================================================
# Storage
# ============================================================

def bible_path(root: Path, persona: str) -> Path:
    return Path(root) / persona / "character.json"


def write_bible(root: Path, persona: str, bible: dict) -> Path:
    path = bible_path(root, persona)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bible, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_bible(root: Path, persona: str) -> dict | None:
    path = bible_path(root, persona)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ============================================================
# Clip keys and prompts
# ============================================================

def all_clip_keys(variants_per_tag: int) -> list[tuple[str, int]]:
    """Every (tag, n) in bank order: tags as in CAST_EMOTION_TAGS, n from 1."""
    return [(tag, n) for tag in CAST_EMOTION_TAGS for n in range(1, int(variants_per_tag) + 1)]


def clip_filename(tag: str, n: int) -> str:
    return f"{tag}_{n}.mp4"


def clip_prompt(bible: dict, tag: str, n: int) -> str:
    """Motion prompt for clip (tag, n).

    The ACTION SENTENCE COMES FIRST on purpose: every prompt shares
    ``base_motion``, so leading with the action makes the first 40 normalized
    characters unique per (tag, n), which is what inbox matching keys on.
    """
    actions = bible["emotion_actions"][tag]
    return actions[n - 1] + " " + bible["base_motion"]


# ============================================================
# Outbox: prompt pack + checklist
# ============================================================

def _outbox(root: Path, persona: str) -> Path:
    out = Path(root) / persona / "outbox"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _fence(text: str) -> str:
    return "```\n" + text.strip() + "\n```"


def write_pack(
    root: Path,
    persona: str,
    bible: dict,
    pending: list[tuple[str, int]],
    cfg: dict,
) -> Path:
    """Write ``root/persona/outbox/prompt_pack.md`` (UGC pack layout)."""
    variants = int(cfg.get("variants_per_tag", len(next(iter(bible["emotion_actions"].values())))))
    clip_seconds = cfg.get("clip_seconds", 8)
    total = len(all_clip_keys(variants))
    pending_set = {(t, int(n)) for t, n in pending}

    lines: list[str] = []
    lines.append(f"# Flow prompt pack — {persona}")
    lines.append("")
    lines.append(f"**{bible.get('name', '')}** — {bible.get('age_range', '')}")
    lines.append("")
    lines.append(FLOW_HOWTO.format(clip_seconds=clip_seconds).rstrip())
    lines.append("")
    lines.append("## Master frame prompt")
    lines.append("")
    lines.append(_fence(bible["master_frame_prompt"]))
    lines.append("")
    lines.append("Save as: reference.png")
    lines.append("")
    lines.append(
        "Re-roll in Flow's image tool until the face reads right; "
        "this frame is the identity anchor for every clip."
    )
    lines.append("")
    lines.append(f"## Clips to generate ({len(pending_set)} pending of {total})")
    lines.append("")

    if not pending_set:
        lines.append(f"All {total} clips are in the bank.")
    else:
        for tag, n in all_clip_keys(variants):
            if (tag, n) not in pending_set:
                continue
            lines.append(f"### {tag} {n}")
            lines.append("")
            lines.append(_fence(clip_prompt(bible, tag, n)))
            lines.append("")
            lines.append(f"Save as: inbox/{clip_filename(tag, n)}")
            lines.append("")

    path = _outbox(root, persona) / "prompt_pack.md"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def write_checklist(
    root: Path,
    persona: str,
    pending: list[tuple[str, int]],
    cfg: dict,
) -> Path:
    """Write ``root/persona/outbox/flow_checklist.md``."""
    count = len(pending)
    lines: list[str] = []
    lines.append(f"# Flow checklist — {persona}")
    lines.append("")
    lines.append("- [ ] reference.png (master frame, Flow image tool)")
    for tag, n in pending:
        lines.append(f"- [ ] inbox/{clip_filename(tag, int(n))}")
    lines.append("")
    lines.append("## Credit estimate")
    lines.append("")
    lines.append(f"{count} clips × {CREDITS_FAST} credits = {count * CREDITS_FAST} on Veo 3.1 Fast")
    lines.append(f"{count} clips × {CREDITS_LITE} credits = {count * CREDITS_LITE} on Veo 3.1 Lite")
    lines.append("Google AI Pro: 50 credits/day + 1,000/month, no rollover.")

    path = _outbox(root, persona) / "flow_checklist.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
