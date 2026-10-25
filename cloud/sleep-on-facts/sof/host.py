"""The host's spoken welcome at the start of every video (Gian, 2026-10-25).

The leading channels in this niche open with the same kind of short host segment every night: a
greeting, an invitation to say in the comments where you are listening from, a like/subscribe ask,
a thank-you to regulars, then "Tonight, we…". It turns a narration into a show with a host, and the
comment prompt gives YouTube engagement on videos people mostly sleep through.

The wording here is our own. Each part has several phrasings and every video combines them differently
(seeded by the subject), so no two intros are word-for-word the same — a fixed template repeated on
every upload is what YouTube's inauthentic-content policy describes. About 60–90 seconds when spoken.
"""

from __future__ import annotations

import hashlib
import re

GREETINGS = (
    "Hello, and welcome to Sleep On Facts.",
    "Good evening, and welcome to Sleep On Facts.",
    "Hello again, and welcome back to Sleep On Facts.",
    "Welcome to Sleep On Facts. It's good to have you here tonight.",
    "Good evening. This is Sleep On Facts, and you're very welcome here.",
)

SETTLE = (
    "Wherever you are tonight, find a comfortable spot and let the rest of the day go.",
    "Get comfortable, turn the lights down if you can, and let everything else wait until morning.",
    "Settle in wherever you are. For the next while, there's nothing to do except listen.",
    "Maybe you're already in bed, maybe you're on the sofa with the house going quiet around you. Either way, get comfortable.",
)

COMMENT_ASKS = (
    "Before we start, I'd love to know where in the world you're listening from. If you feel like it, leave it in the comments.",
    "If you'd like, tell me in the comments where you're listening from tonight, and what time it is there.",
    "One small thing before we begin: drop a comment with your town or country, so we can see how far these nights travel.",
    "I'm always curious where these evenings end up. A short comment with where you're listening from is very welcome.",
)

SUPPORT_ASKS = (
    "If these long, slow evenings of facts help you rest, a like or a subscribe helps the channel reach more people who could use one.",
    "Subscribing is the easiest way to find the next one, and a like helps other night owls find this one.",
    "If you enjoy these, subscribing really does help, and it means the next one will be waiting for you.",
    "And if you'd like more nights like this, subscribing and leaving a like are the simplest ways to help the channel grow.",
)

THANKS = (
    "And to everyone who comes back night after night, thank you.",
    "To the regulars: it's good to have you back.",
    "Thank you for spending part of your night here.",
    "",
)


def _pick(options: tuple[str, ...], seed: int, salt: int) -> str:
    return options[(seed // (7 ** salt)) % len(options)]


def seed_for(subject: str) -> int:
    return int(hashlib.sha1(str(subject or "").lower().encode("utf-8")).hexdigest(), 16)


def clean_teaser(text: str, *, max_words: int = 75) -> str:
    """The model's "Tonight, we…" lines: plain sentences, no questions or exclamations, capped in length."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    t = t.replace("!", ".")
    sents = [s for s in re.split(r"(?<=[.?])\s+", t) if s and not s.endswith("?")]
    out, n = [], 0
    for s in sents:
        w = len(s.split())
        if n + w > max_words:
            break
        out.append(s)
        n += w
    return " ".join(out)


def intro(subject: str, *, teaser: str = "", fallback: str = "", seed: int | None = None) -> str:
    """Two short paragraphs: the welcome (greeting, settle, comment ask, support ask, thanks), then tonight's
    teaser. ``fallback`` ("Tonight, we're taking a slow journey through X.") is used when the teaser is empty."""
    s = seed_for(subject) if seed is None else int(seed)
    parts = [_pick(GREETINGS, s, 0), _pick(SETTLE, s, 1), _pick(COMMENT_ASKS, s, 2), _pick(SUPPORT_ASKS, s, 3),
             _pick(THANKS, s, 4)]
    welcome = " ".join(p for p in parts if p)
    tonight = clean_teaser(teaser) or str(fallback or "").strip()
    return welcome + ("\n\n" + tonight if tonight else "")
