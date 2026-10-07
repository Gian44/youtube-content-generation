"""Prompt templates. OUTLINE/SEGMENT descend from StoryFactory's sleep_facts prompts but now write
FROM research notes (Wikipedia article + linked articles) rather than from the model's memory."""

TOPIC_PROMPT = """You pick subjects for a calm "facts to fall asleep to" YouTube channel. Each video is a
three-hour, single-subject narration, so a subject must be rich enough to sustain three hours of
specific, true, gentle detail — and it must have a substantial English Wikipedia article.

Category for today: {{category}}

Subjects already used (never propose these or near-duplicates):
{{used}}

Rejected this round (too thin or missing on Wikipedia — do not propose again):
{{avoid}}

Propose {{n}} candidates. Prefer concrete, evocative subjects over abstractions: "The Silk Road"
over "trade", "Honeybees" over "insects", "The Great Barrier Reef" over "coral". Mix the familiar
with the pleasantly obscure. Nothing violent, political, medical, or distressing — this plays while
people fall asleep.

Return JSON:
{"candidates": [{"topic": "<short display name, 1-4 words, title case>", "wikipedia_title": "<exact English Wikipedia article title>"}]}"""

OUTLINE_PROMPT = """You are the head writer for a calm "facts to fall asleep to" channel.
Plan a LONG, soothing, single-topic video (about {{target_minutes}} minutes when
narrated slowly) as an ordered outline of "movements".

TOPIC (the entire video stays on this ONE subject): {{topic}}

The research corpus is built from the Wikipedia article "{{wiki_title}}" and the articles it
links to. Its lead paragraph:
{{lead}}

Its section headings (use these to decide what the movements should cover — the writer will
only have notes on what the research actually contains):
{{sections}}

A "movement" is a distinct sub-area of the topic explored for several minutes.
Plan exactly {{num_movements}} movements that flow naturally from one to the next,
together covering the topic broadly and gently. Order them so the journey feels calm and
continuous, easing the listener toward sleep: begin with the most concrete and familiar,
drift toward the quieter, stranger corners late in the video.

HARD RULES:
- Every movement must stay on "{{topic}}". Never drift to unrelated domains.
- Each movement has 6-12 short factual "beats" grounded in the sections above.
- THIS IS FOR SLEEP. Build the movements from the descriptive, wonder-filled, slow material:
  geography, how things form and work, daily rhythms, creatures and plants, craftsmanship,
  quiet history, the senses. Articles always carry sections on threats, destruction, disease,
  conflict, politics, controversies and statistics of loss — do NOT make movements of those.
  At most one calm sentence of acknowledgement if it would be dishonest to omit it entirely.
- No hype, no loud hooks, no second-person calls to action.

Output as JSON:
{
  "title": "...",                  // calm, sleep-style title
  "hook": "...",                   // one gentle opening sentence (no "welcome back", no hype)
  "movements": [                   // exactly {{num_movements}} items, in order
    {"heading": "...", "beats": ["...", "..."]}
  ],
  "image_search_queries": ["..."] // 8-15 short stock-photo search queries on-topic (landscapes, animals, places, objects)
}"""

SEGMENT_PROMPT = """You are narrating a calm "facts to fall asleep to" video about ONE topic.
Write the narration for the CURRENT movement only — flowing, serene prose meant to
be read slowly aloud to help someone fall asleep.

TOPIC (never drift off it): {{topic}}

CURRENT MOVEMENT: {{movement_heading}}
Beats to cover (in any order, with gentle connective prose):
{{movement_beats}}

RESEARCH NOTES — the facts for this movement. Write from these. Each note is tagged with
its source article in [brackets].
{{notes}}

What has been narrated so far (for continuity — do NOT repeat it):
{{running_summary}}

The previous movement ended like this (continue smoothly from it; do not repeat it):
{{previous_tail}}

HARD RULES:
- Aim for about {{target_words}} words for THIS movement.
- FACTS COME FROM THE NOTES. Use the specific names, dates, numbers and places the notes
  give you. If the notes do not support a detail, do not invent one — say less, or speak
  more generally. Never fabricate a statistic, a quotation, or a named person.
- Depth over breadth: dwell on one specific thing at a time (a particular place, animal,
  craftsman, year, mechanism) and explain it properly before moving on.
- Keep it restful. Pass over destruction, disease, conflict, politics and alarming statistics
  in a sentence at most, even if the notes dwell on them; choose the notes that describe,
  explain and evoke.
- Calm, slow, unhurried tone. Vary sentence length. Mostly plain, concrete words.
- Continuous PROSE only. No headings, bullets, lists or numbering.
- BANNED: "did you know", "fun fact", "interestingly", "it is worth noting", "in conclusion",
  "imagine", "picture this", "let's", "we", "you" (except at most one quiet aside per
  movement), rhetorical questions, exclamation marks, "welcome back", calls to action.
- Do not restate the topic's definition; the listener already knows what the video is about.
- Begin mid-flow as a natural continuation.

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
  "description": "<2-3 calm sentences describing the video. No hashtags, no emojis.>",
  "tags": ["<8-12 lowercase tags: sleep, facts, the topic, calm, relaxation, bedtime, and related words>"]
}"""

DESCRIPTION_FOOTER = """Narration is AI-generated; facts are drawn from the public sources below.

Sources (Wikipedia, CC BY-SA 4.0):
{{sources}}"""
