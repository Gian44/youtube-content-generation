"""Prompt templates ported verbatim from StoryFactory (db/seed.py: sleep_facts_outline / sleep_facts_segment)."""

OUTLINE_PROMPT = """You are the head writer for a calm "facts to fall asleep to" channel.
Plan a LONG, soothing, single-topic video (about {{target_minutes}} minutes when
narrated slowly) as an ordered outline of "movements".

TOPIC (the entire video stays on this ONE subject): {{topic}}

Reference notes (factual grounding — may be empty):
{{topic_hints}}

A "movement" is a distinct sub-area of the topic explored for several minutes.
Plan exactly {{num_movements}} movements that flow naturally from one to the next,
together covering the topic broadly and gently (history, how it works, notable
facts, places, creatures, mysteries, etc. — whatever fits THIS topic). Order them
so the journey feels calm and continuous, easing the listener toward sleep.

HARD RULES:
- Every movement must stay on "{{topic}}". Never drift to unrelated domains.
- Each movement has 6-12 short factual "beats" (single calm facts/ideas to expand).
- No hype, no loud hooks, no second-person calls to action.

Output as JSON:
{
  "title": "...",                  // calm, sleep-style title (e.g. mentions hours / falling asleep)
  "hook": "...",                   // one gentle opening sentence (no "welcome back", no hype)
  "movements": [                   // exactly {{num_movements}} items, in order
    {"heading": "...", "beats": ["...", "..."]}
  ],
  "asset_keywords": ["..."],       // 8-15 short on-topic visual keywords for stock images
  "image_search_queries": ["..."] // 8-15 short stock-image search queries on-topic
}"""

SEGMENT_PROMPT = """You are narrating a calm "facts to fall asleep to" video about ONE topic.
Write the narration for the CURRENT movement only — flowing, serene prose meant to
be read slowly aloud to help someone fall asleep.

TOPIC (never drift off it): {{topic}}

CURRENT MOVEMENT: {{movement_heading}}
Facts to weave in (cover these, in any order, adding gentle connective prose):
{{movement_beats}}

What has been narrated so far (for continuity — do NOT repeat it):
{{running_summary}}

The previous movement ended like this (continue smoothly from it; do not repeat it):
{{previous_tail}}

HARD RULES:
- Aim for about {{target_words}} words for THIS movement.
- Calm, slow, gentle, monotone-leaning tone. Vary sentence length; keep it flowing.
- Continuous PROSE only. No headings, no bullet points, no lists, no numbers like
  "1." — lists break the rhythm and wake the listener.
- No second person hype ("you won't believe"), no CTAs, no "welcome back".
- Accurate, interesting, low-stakes facts. Stay entirely on "{{topic}}".
- Begin mid-flow as a natural continuation; do not re-introduce the whole topic.

Output as JSON:
{
  "body": "..."   // the narration prose for this movement only
}"""

METADATA_PROMPT = """You write YouTube metadata for a calm "facts to fall asleep to" channel.
Topic: {{topic}}. Video length: about {{hours}} hour(s). First 600 characters of the narration:
{{opening}}

Return JSON:
{
  "title": "<at most 95 characters, pattern: Calm Facts About <Topic> to Fall Asleep To (<N> Hours); no clickbait, no emojis>",
  "description": "<2-3 calm sentences describing the video, then a blank line, then exactly this line: Narration is AI-generated; facts are drawn from public sources.>",
  "tags": ["<8-12 lowercase tags: sleep, facts, the topic, calm, relaxation, bedtime, and related words>"]
}"""
