"""YouTube Shorts SEO/metadata helpers — the optimizer playbook, as pure code.

Distilled rules (Dan, 75M+ views across two Shorts channels):

* **Title** — curiosity-driven with ONE natural keyword and **zero hashtags**
  (hashtags in the title are wasted real estate).
* **Description** — 1-2 lines + **exactly ~3 hashtags** (more dilutes relevance).
* **Tags** — the 3-tier viral formula, 9-12 total:
    - *post-specific* (what this exact video is),
    - *niche* (the audience pool / category),
    - *broad* (general feed: viral, viral shorts, shorts, for you).

These helpers only SHAPE metadata into that form; callers supply the
content-specific words (the planner/LLM writes the curiosity title + hook +
post-specific tags). All functions are pure so they unit-test without I/O.
"""

from __future__ import annotations

import re

YOUTUBE_TITLE_LIMIT = 100
# YouTube sums the characters of all tags; the hard limit is 500. Stay under.
YOUTUBE_TAG_TOTAL_CHARS = 480
MAX_TAGS = 12
HASHTAGS_IN_DESCRIPTION = 3

# Broad/viral tier — reaches the general feed. Fixed set; trimmed by the caps.
BROAD_TAGS: list[str] = ["viral", "viral shorts", "shorts", "for you"]

_HASHTAG_RE = re.compile(r"#\w+")
_WS_RE = re.compile(r"\s+")


def sanitize_title(title: str) -> str:
    """Strip every hashtag and trim to the YouTube title limit.

    The title is prime curiosity real estate — hashtags never belong there.
    """
    text = _HASHTAG_RE.sub("", title or "")
    text = _WS_RE.sub(" ", text).strip()
    return text[:YOUTUBE_TITLE_LIMIT].strip()


def _norm_tag(tag: str) -> str:
    return _WS_RE.sub(" ", (tag or "").strip().lower())


def build_tags(
    post_specific: list[str],
    niche: list[str],
    *,
    broad: list[str] | None = None,
    per_tier_cap: int = 4,
    max_total: int = MAX_TAGS,
) -> list[str]:
    """Assemble the 3-tier tag list (9-12), deduped and within YouTube's limits.

    Each tier contributes up to ``per_tier_cap`` (3-4) tags; duplicates are
    dropped case-insensitively; the combined character budget is enforced so the
    upload never trips YouTube's 500-char tag ceiling.
    """
    broad_tier = BROAD_TAGS if broad is None else broad
    out: list[str] = []
    seen: set[str] = set()
    for tier in (post_specific, niche, broad_tier):
        added = 0
        for raw in tier or []:
            tag = _norm_tag(raw)
            if not tag or tag in seen:
                continue
            if len(out) >= max_total or added >= per_tier_cap:
                break
            seen.add(tag)
            out.append(tag)
            added += 1

    # Enforce the cumulative character budget (YouTube sums tag lengths).
    budget = 0
    final: list[str] = []
    for tag in out:
        budget += len(tag) + 1
        if budget > YOUTUBE_TAG_TOTAL_CHARS:
            break
        final.append(tag)
    return final


def normalize_hashtags(tags: list[str], *, limit: int = HASHTAGS_IN_DESCRIPTION) -> list[str]:
    """Return up to ``limit`` clean, unique ``#word`` hashtags (no spaces)."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in tags or []:
        body = (raw or "").strip().lstrip("#")
        body = re.sub(r"[^0-9A-Za-z_]", "", body)
        if not body:
            continue
        tag = "#" + body
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
        if len(out) >= limit:
            break
    return out


def build_description(hook: str, hashtags: list[str], *, extra: str | None = None) -> str:
    """1-2 line hook + exactly ~3 hashtags, with optional trailing ``extra``.

    ``extra`` carries non-SEO tails the channel needs (music attribution, a
    copyright/recap disclosure) below the hashtag line.
    """
    parts: list[str] = []
    hook_text = (hook or "").strip()
    if hook_text:
        parts.append(hook_text)
    tags = normalize_hashtags(hashtags)
    if tags:
        parts.append(" ".join(tags))
    extra_text = (extra or "").strip()
    if extra_text:
        parts.append(extra_text)
    return "\n\n".join(parts)


def build_short_metadata(
    *,
    title: str,
    hook: str,
    post_specific_tags: list[str],
    niche_tags: list[str],
    hashtags: list[str],
    extra_description: str | None = None,
    broad_tags: list[str] | None = None,
) -> dict:
    """Assemble a full optimizer-shaped Short metadata dict.

    Returns ``{"title", "description", "tags"}`` ready for the uploader.
    """
    return {
        "title": sanitize_title(title),
        "description": build_description(hook, hashtags, extra=extra_description),
        "tags": build_tags(post_specific_tags, niche_tags, broad=broad_tags),
    }


def keyword_tags_from_title(title: str, *, limit: int = 3) -> list[str]:
    """Derive lightweight post-specific tags from a title.

    Fallback when the planner did not supply explicit post-specific tags: take
    the significant words of the (sanitized) title as literal-content tags.
    """
    clean = sanitize_title(title).lower()
    words = [w for w in re.split(r"[^a-z0-9]+", clean) if len(w) > 2]
    stop = {"the", "and", "for", "with", "you", "your", "that", "this", "from", "what", "who"}
    keep = [w for w in words if w not in stop]
    out: list[str] = []
    seen: set[str] = set()
    for w in keep:
        if w not in seen:
            seen.add(w)
            out.append(w)
        if len(out) >= limit:
            break
    return out
