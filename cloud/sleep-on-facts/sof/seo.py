"""Discovery metadata: titles, descriptions, chapters, tags, hashtags, playlists, publish slot.

2026-10-25 (channel analytics): 76 % of long-form watch time comes from *Suggested* — the video
shown beside someone else's long documentary at bedtime — and only 1.4 % of those impressions were
clicked; search brought 0.1 h. The neighbours that win there lead with curiosity ("The Amazon Gets
More Dangerous the Deeper You Go"); ours led with mood words ("Calm", "Gentle", "Quiet"). So every
title now LEADS with a true, specific hook from the video (``title_hook``) and ends with the sleep
intent ("| 3 Hours of Space Facts to Fall Asleep To"). The tail rotates (TITLE_VARIANTS) so the
channel page does not read as one template with the topic swapped — the pattern YouTube's
inauthentic-content policy describes. Fear and hype words stay banned: the narration is restful,
and the title must not promise what the video does not deliver.
Each topic category maps to one search phrase, one documentary noun, one playlist and one hashtag.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class Profile:
    phrase: str          # inside the title: "… of Calm {phrase} to Fall Asleep To"
    playlist: str        # playlist title the video is added to
    hashtag: str         # second hashtag (first is always #sleep)
    doc: str = "Documentary"   # "3-Hour {doc} for Sleep"


PROFILES: list[tuple[str, Profile]] = [
    ("space", Profile("Space Facts", "Space Facts to Fall Asleep To", "spacefacts", "Space Documentary")),
    ("astronom", Profile("Space Facts", "Space Facts to Fall Asleep To", "spacefacts", "Space Documentary")),
    ("history", Profile("History", "History for Sleep", "historyforsleep", "History Documentary")),
    ("nature", Profile("Nature Facts", "Nature Facts to Fall Asleep To", "naturefacts", "Nature Documentary")),
    ("ocean", Profile("Ocean Facts", "Ocean Facts to Fall Asleep To", "oceanfacts", "Ocean Documentary")),
    ("how things work", Profile("Science Facts", "Science Facts to Fall Asleep To", "sciencefacts", "Science Documentary")),
    ("earth", Profile("Earth Facts", "Earth & Geology Facts to Fall Asleep To", "earthfacts", "Earth Documentary")),
    ("cities", Profile("Geography Facts", "Geography Facts to Fall Asleep To", "geography", "Travel Documentary")),
    ("food", Profile("Facts", "Food, Farming & Plants for Sleep", "sleepfacts", "Documentary")),
    ("invention", Profile("History", "Inventions & Ideas for Sleep", "historyforsleep", "History Documentary")),
]
DEFAULT = Profile("Facts", "Calm Facts to Fall Asleep To", "sleepfacts")
SPACE_CATEGORY = "space & astronomy"


def profile(category: str) -> Profile:
    c = (category or "").lower()
    for key, prof in PROFILES:
        if c.startswith(key) or key in c:
            return prof
    return DEFAULT


def _clean(s: str) -> str:
    """YouTube rejects '<' and '>' in titles and descriptions."""
    return re.sub(r"[<>]", "", str(s or "")).strip()


def hours_label(hours: int) -> str:
    h = max(1, int(hours))
    return f"{h} Hour{'s' if h != 1 else ''}"


_HOOK_BANNED = re.compile(r"[!?#@\"“”]|\b(shocking|secrets?|insane|unbelievable|incredible|you|your|must|best|worst|"
                          r"top|ultimate|mind|blow\w*|amazing|scar(?:y|ier|iest)|terrif\w*|horrif\w*|disturbing|creepy|"
                          r"deadl\w*|dangerous|nightmare\w*|evil|killer\w*|exposed|truth)\b", re.I)
_ARTICLE_NOUNS = re.compile(r"\b(reef|rainforest|forest|civili[sz]ation|engine|river|desert|ocean|sea|mountains|valley|canyon|"
                            r"islands|wall|pyramids|alps|andes|himalayas|sahara|nile|amazon|arctic|antarctic|moon|sun|"
                            r"universe|galaxy|milky way|telescope|railway|printing press|lighthouse)$", re.I)


def clean_hook(hook: str) -> str:
    """A model-written title hook, or '' when it is not a 2-10 word statement (hype, fear, question, too long).

    Curiosity is the point ("The Moon Is Slowly Drifting Away From Earth", "Why Saturn's Rings Won't
    Last"); digits are fine; questions, exclamations, hype and fear words are not.
    """
    h = re.sub(r"\s+", " ", _clean(hook)).strip(" .,:;-–—'‘’")
    if not 2 <= len(h.split()) <= 10 or len(h) > 64 or _HOOK_BANNED.search(h):
        return ""
    return h


def subject_phrase(subject: str) -> str:
    """How the subject reads mid-sentence: "the Great Barrier Reef", but "Saturn", "Ancient Egypt"."""
    s = _clean(subject)
    if len(s.split()) >= 2 and _ARTICLE_NOUNS.search(s) and not re.match(r"(?i)(the|mount|lake|ancient|saint|st\.?)\b", s):
        return "the " + s
    return s


def _mentions_subject(hook: str, subject: str) -> bool:
    """More than half of the subject's words are in the hook ("Cities of the Indus Valley" names the
    Indus Valley Civilization well enough; "Life Along the Ancient Nile" does not name Ancient Egypt)."""
    words = [w for w in re.findall(r"[a-z0-9]+", subject.lower()) if len(w) > 2 and w != "the"]
    hits = sum(1 for w in words if re.search(rf"\b{re.escape(w)}", hook.lower()))
    return bool(words) and hits * 2 > len(words)


TITLE_VARIANTS = 4
NO_HOOK_LEADS = ("The Whole Story of {s}", "Everything We Know About {s}", "A Long, Slow Look at {s}", "All About {s}")


def build_title(subject: str, category: str, hours: int, *, hook: str = "", variant: int = 0) -> str:
    """Hook first, sleep intent last; one of TITLE_VARIANTS tails (rotate ``variant`` per video).

    ``hook`` comes from the metadata model; one that does not name the subject is prefixed with it
    ("Rice: Half the World Eats It"). With no usable hook a plain lead names the subject. Anything over
    YouTube's 100 characters falls back to the shortest tail, then to the subject alone.
    """
    p, h, S = profile(category), hours_label(hours), _clean(subject)
    n = max(1, int(hours))
    k = clean_hook(hook)
    if k:
        lead = k if _mentions_subject(k, S) else f"{S}: {k}"
    else:
        lead = NO_HOOK_LEADS[int(variant) % len(NO_HOOK_LEADS)].format(s=subject_phrase(S))
    tails = [
        f"{h} of {p.phrase} to Fall Asleep To",
        f"{n}-Hour {p.doc} for Sleep",
        f"Fall Asleep to {h} of {p.phrase}",
        f"{p.phrase} for Sleep ({h})",
    ]
    tail = tails[int(variant) % len(tails)]
    for cand in (f"{lead} | {tail}", f"{lead} | {min(tails, key=len)}", f"{S} | {tails[0]}"):
        if len(cand) <= 100:
            return cand
    return f"{S} | {tails[0]}"[:100]


def hashtags(subject: str, category: str) -> list[str]:
    camel = "".join(w[:1].upper() + w[1:] for w in re.findall(r"[A-Za-z0-9]+", subject or ""))
    tags = ["#sleep", f"#{profile(category).hashtag}"]
    if camel and f"#{camel}".lower() not in {t.lower() for t in tags}:
        tags.append(f"#{camel}")
    return tags


def fmt_ts(seconds: float) -> str:
    s = max(0, int(seconds))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def chapter_lines(chapters: list[dict], *, min_gap: float = 10.0) -> list[str]:
    """YouTube chapter rules: first at 0:00, ascending, ≥ 3 chapters, each ≥ 10 s. Returns [] if unmet."""
    rows = sorted(((float(c.get("start") or 0), _clean(c.get("title") or "")) for c in chapters or []), key=lambda r: r[0])
    out: list[tuple[float, str]] = []
    for start, title in rows:
        title = re.sub(r"\s+", " ", title).rstrip(".:;,")[:70] or f"Part {len(out) + 1}"
        if not out:
            out.append((0.0, title))
        elif start - out[-1][0] >= min_gap:
            out.append((start, title))
    return [f"{fmt_ts(s)} {t}" for s, t in out] if len(out) >= 3 else []


def build_description(*, subject: str, category: str, hours: int, blurb: str = "", chapters: list[dict] | None = None,
                      footer: str = "", photos: str = "", extra: str = "", limit_bytes: int = 4900) -> str:
    """Keyword-first opening line → blurb → chapters → sources footer → photo/licence line → extra → hashtags.

    No link to the channel's own site or code (Gian, 2026-10-11). Over the byte limit, source lines
    are dropped from the end first (chapters carry more value).
    """
    p = profile(category)
    h = max(1, int(hours))
    opening = f"Fall asleep to {h} hour{'s' if h != 1 else ''} of calm {p.phrase.lower()}: {_clean(subject)}."
    head = "\n\n".join(x for x in [opening + (" " + _clean(blurb) if blurb else ""),
                                   ("Chapters\n" + "\n".join(chapter_lines(chapters or []))) if chapter_lines(chapters or []) else ""] if x)
    tail_parts = []
    if photos:
        tail_parts.append(_clean(photos))
    if extra:
        tail_parts.append(_clean(extra))
    tail_parts.append(" ".join(hashtags(subject, category)))
    foot_lines = _clean(footer).splitlines()

    def assemble(lines: list[str]) -> str:
        return "\n\n".join(x for x in [head, "\n".join(lines).strip(), *tail_parts] if x)

    text = assemble(foot_lines)
    while len(text.encode("utf-8")) > limit_bytes and any(l.startswith("- ") for l in foot_lines):
        idx = max(i for i, l in enumerate(foot_lines) if l.startswith("- "))
        foot_lines.pop(idx)
        text = assemble(foot_lines)
    return text.encode("utf-8")[:limit_bytes].decode("utf-8", "ignore")


def build_tags(subject: str, category: str, extra: list[str] | None = None, *, limit_chars: int = 450) -> list[str]:
    p = profile(category)
    s = _clean(subject).lower()
    base = ["sleep", "facts to fall asleep to", f"{p.phrase.lower()} to fall asleep to", s, f"{s} facts",
            "sleep documentary", "bedtime", "calm", "relaxing", "fall asleep fast", "sleep on facts"]
    out: list[str] = []
    for t in base + [str(x) for x in (extra or [])]:
        t = _clean(t).replace(",", " ").strip()
        if t and t.lower() not in {o.lower() for o in out} and sum(len(o) + 1 for o in out) + len(t) <= limit_chars:
            out.append(t)
    return out


def playlist_description(category: str) -> str:
    p = profile(category)
    return (f"Calm, slow {p.phrase.lower()} narrated to help you fall asleep — one long, gentle video at a time. "
            "Facts are drawn from Wikipedia and the sources listed on each video.")


def publish_slot(now: datetime, hour: int, minute: int, *, tz: str = "UTC", max_wait_hours: float = 12.0) -> datetime | None:
    """Next HH:MM in ``tz`` at least 5 minutes away, as UTC — or None (publish immediately) if that is
    more than ``max_wait_hours`` off, so a very late or manual run does not sit private for a day."""
    from zoneinfo import ZoneInfo

    local_now = now.astimezone(ZoneInfo(tz))
    slot = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if slot <= local_now + timedelta(minutes=5):
        slot = (slot + timedelta(days=1)).replace(hour=hour, minute=minute)
    slot = slot.astimezone(timezone.utc)
    return slot if slot - now.astimezone(timezone.utc) <= timedelta(hours=max_wait_hours) else None


def rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
