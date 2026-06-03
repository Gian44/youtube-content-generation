"""Canonical content defaults shared across the worker.

These were previously duplicated as module-level constants in
``pipeline/topic_selector.py`` and ``pipeline/story_generator.py``. They are
centralized here so channel-config resolution, the migration backfill, and the
pipeline all agree on the same baseline. A channel's stored ``config`` may
override ``category_weights`` / ``category_hints`` / ``voice_personas``.
"""

from __future__ import annotations

STORY_CATEGORIES: list[str] = [
    "aita", "relationships", "cheating", "revenge", "workplace_drama",
    "entitled_parents", "family_drama", "confessions", "scary_stories",
    "creepy_encounters", "wholesome", "mysteries", "customer_service_drama",
]

DEFAULT_CATEGORY_WEIGHTS: dict[str, int] = {
    "aita": 15, "relationships": 12, "cheating": 10, "revenge": 12,
    "workplace_drama": 10, "entitled_parents": 8, "family_drama": 8,
    "confessions": 5, "scary_stories": 5, "creepy_encounters": 3,
    "wholesome": 5, "mysteries": 3, "customer_service_drama": 4,
}

DEFAULT_CATEGORY_HINTS: dict[str, str] = {
    "aita": "moral dilemmas, family conflicts, social etiquette, boundary-setting",
    "relationships": "dating drama, long-term relationship issues, breakups, trust",
    "cheating": "infidelity discovery, betrayal, confrontation, aftermath",
    "revenge": "creative revenge, karma, justice served, petty retribution",
    "workplace_drama": "toxic bosses, coworker conflicts, unfair policies, quitting stories",
    "entitled_parents": "outrageous parenting, public meltdowns, unreasonable demands",
    "family_drama": "inheritance disputes, favoritism, toxic relatives, holiday disasters",
    "confessions": "secret reveals, guilty consciences, hidden truths, cathartic admissions",
    "scary_stories": "supernatural encounters, unexplained events, eerie discoveries",
    "creepy_encounters": "stalkers, strange strangers, unsettling experiences",
    "wholesome": "kindness from strangers, heartwarming surprises, found family",
    "mysteries": "unexplained disappearances, cold cases, strange coincidences",
    "customer_service_drama": "Karens, impossible demands, retail horror stories",
}

DEFAULT_VOICE_PERSONAS: list[str] = [
    "calm", "dramatic", "sarcastic", "confession", "horror", "warm",
]
