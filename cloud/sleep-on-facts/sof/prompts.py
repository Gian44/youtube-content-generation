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
over "trade", "The Mariana Trench" over "oceans", "The Great Barrier Reef" over "coral".
Prefer subjects people already watch long documentaries about and would recognise from a thumbnail
— famous places, objects, eras, creatures and phenomena with a sense of scale or mystery (Jupiter,
the Roman Empire, the Mariana Trench, Black Holes, Pompeii, Blue Whales, Antarctica) — over niche
ones with no audience. Avoid everyday commodities and crafts (paper, rice, soap). Nothing violent, political, medical, or distressing — this plays while
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
together covering the topic broadly and gently.

STORY SHAPE FOR THIS VIDEO — "{{format_name}}":
{{format_guidance}}
Shape the order of the movements around it, so this video plays out differently from one
that merely lists facts — but keep every heading a plain, descriptive chapter title (they become
the video's chapters, which viewers scan and search). Adapt it naturally to the subject; never force
it into something untrue. Within the shape, ease the listener toward sleep: start concrete
and familiar, drift toward the quieter, stranger corners late in the video.

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
    {"heading": "...", "beats": ["...", "..."]}   // heading = a plain, descriptive chapter title a viewer could
                                                   // search for, 2-7 words: "How the Moon Formed", "The Far Side",
                                                   // "Tides and Eclipses". Not poetic ("The First Light of Its Making").
  ],
  "image_search_queries": ["..."], // 10-15 stock-photo searches that show THE SUBJECT ITSELF (e.g. "octopus underwater close up", "octopus tentacles reef")
  "image_setting_queries": ["..."], // 4-6 searches for its natural setting/places only (e.g. "coral reef underwater", "deep ocean floor") — used sparingly
  "image_keywords": ["..."]        // 3-8 lowercase NAMES that identify this subject and nothing else: its own name and spelling variants, proper names of its famous parts, sites, people or missions (Saturn: "saturn", "cassini", "titan"; Indus Valley Civilisation: "indus", "harappa", "mohenjo-daro"). NEVER generic words such as "planet", "rings", "ruins", "archaeological", "forest", "river", "engine"
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
- Unhurried pace. Vary sentence length. Mostly plain, concrete words.
- Continuous PROSE only. No headings, bullets, lists or numbering.
- BANNED: "did you know", "fun fact", "interestingly", "it is worth noting", "in conclusion",
  "imagine", "picture this", "let's", "we", "you", rhetorical questions, exclamation marks,
  "welcome back", calls to action.
- Do not restate the topic's definition; the listener already knows what the video is about.
- Begin mid-flow as a natural continuation, with a plain concrete fact — not a metaphor,
  not a scene-setting flourish ("In the slow grammar of the sea…").
- End the movement on its last fact. No closing paragraph that sums the movement up.

{{style}}

Output as JSON:
{
  "body": "..."   // the narration prose for this movement only
}"""

TITLE_HOOK_RULE = ("<4-10 words, Title Case: the single most intriguing TRUE thing this video explains, as a statement or a how/why line that makes a curious stranger want to hear the rest. It must be supported by the chapters and the narration. Name the subject (or its usual short form). Good: 'The Moon Is Slowly Drifting Away From Earth', 'Why Saturn's Rings Will Not Last Forever', 'How Paper Was Made Before Machines', 'Half the People on Earth Live on Rice'. Never: fear words (scary, terrifying, deadly, disturbing, dangerous), hype words (shocking, insane, secret, mind-blowing, unbelievable), question marks, exclamation marks, 'you', emojis, mood words (calm, gentle, quiet, peaceful, soothing).>")
THUMB_TEXT_RULE = ("<2-4 words in capitals for the thumbnail, the hook compressed so it reads at a glance, e.g. 'DRIFTING AWAY', 'RINGS WON'T LAST', 'MADE BY HAND', 'HALF THE WORLD'. True, no hype, no fear words, no question marks, do not repeat the subject's name.>")

METADATA_PROMPT = """You write YouTube metadata for a calm "facts to fall asleep to" channel.
Topic: {{topic}}. Video length: about {{hours}} hour(s). First 600 characters of the narration:
{{opening}}

What the video covers, chapter by chapter:
{{headings}}

Return JSON:
{
  "title": "<at most 95 characters, pattern: Calm Facts About <Topic> to Fall Asleep To (<N> Hours); no clickbait, no emojis>",
  "title_hook": "{{title_hook_rule}}",
  "thumb_text": "{{thumb_text_rule}}",
  "description": "<2-3 plain sentences saying what the video covers, named concretely (places, creatures, crafts, years). No hashtags, no emojis, no questions, no 'not X but Y', no colon reveals, no words like remarkable, journey, tapestry, testament.>",
  "tags": ["<8-12 lowercase tags: sleep, facts, the topic, calm, relaxation, bedtime, and related words>"]
}"""

DESCRIPTION_FOOTER = """Facts in this video are drawn from the public sources below.

Sources (Wikipedia, CC BY-SA 4.0):
{{sources}}"""


# Story shapes rotate from video to video (YouTube's inauthentic-content policy looks for videos
# that "play out the same way with only the topic swapped"). Each has an opening line spoken at
# the very start of the video and planning guidance for the outline.
STORY_FORMATS = {
    "journey": {
        "name": "A slow journey",
        "opening": "a slow journey through {subject}",
        "guidance": "Travel through the subject as a gentle physical journey: from its edges inward, from one "
                    "end to the other, along its rivers, down through its layers, or room by room. Each movement "
                    "is the next stretch of the route; headings read like places along the way.",
    },
    "day": {
        "name": "A day and a night",
        "opening": "a day and a night with {subject}",
        "guidance": "Follow the subject through one long cycle of time: a day from dawn to deep night, the turning "
                    "seasons, a working day, a life cycle, or one orbit or rotation. Each movement is the next hour, "
                    "season or stage, and describes what is true of the subject at that moment.",
    },
    "story": {
        "name": "Its long story",
        "opening": "the long, quiet story of {subject}",
        "guidance": "Tell the subject's story through time in calm chronological order: how it began or formed, how "
                    "it grew or was discovered and understood, and how it is today. Each movement is a chapter of "
                    "that story; avoid battles and disasters, dwell on making, building, slow change and daily life.",
    },
    "parts": {
        "name": "Piece by piece",
        "opening": "{subject}, piece by piece",
        "guidance": "Take the subject apart gently, one piece at a time: its parts, layers, materials, members or "
                    "features. Each movement looks closely at one piece, explains how it works and how it fits with "
                    "the others, then sets it down before picking up the next.",
    },
}
STORY_ORDER = ["journey", "story", "day", "parts"]

OPENING_LINE = "This is Sleep On Facts. Tonight: {opening}."

CLOSING_HEADING = "Three Quiet Things to Keep"

CLOSING_PROMPT = """You are closing a calm, three-hour "facts to fall asleep to" video about {{topic}}.
This closing segment is the same every night in shape (it is the channel's signature) but its
content is specific to tonight's subject.

What the video covered, movement by movement (heading — opening lines):
{{covered}}

Write the closing segment, about {{target_words}} words:
- Begin exactly with: "Three quiet things to keep from tonight."
- Then three short paragraphs, one per thing: a single memorable, TRUE fact drawn from the
  material above, said simply, followed by one or two plain sentences that stay with that fact
  (where it is, what it is made of, what it does), not a moral or a metaphor drawn from it.
  Never start two paragraphs the same way; never use a stock phrase such as "it is lovely to
  remember". No new facts beyond the material above.
- End with one short line wishing the listener a good night's sleep. No calls to action, no
  "subscribe", no questions, no exclamation marks.

{{style}}

Output as JSON:
{"body": "..."}"""


REPACKAGE_PROMPT = """You rewrite the YouTube packaging of a long "facts to fall asleep to" video that is already
published. Its narration and chapters stay as they are; only the title hook, the thumbnail text and the
chapter titles change.

Subject: {{topic}}
Its current description (summary, then chapters):
{{description}}

Chapters to rename, in order ({{n}} of them):
{{chapters}}

Return JSON:
{
  "title_hook": "{{title_hook_rule}}",
  "thumb_text": "{{thumb_text_rule}}",
  "chapters": ["<exactly {{n}} plain, descriptive chapter titles in the same order, 2-7 words each, no numbering, e.g. 'How the Moon Formed', 'The Far Side', 'Tides and Eclipses' — say what the chapter is about, not a poetic image>"]
}"""

METADATA_PROMPT = METADATA_PROMPT.replace("{{title_hook_rule}}", TITLE_HOOK_RULE).replace("{{thumb_text_rule}}", THUMB_TEXT_RULE)
REPACKAGE_PROMPT = REPACKAGE_PROMPT.replace("{{title_hook_rule}}", TITLE_HOOK_RULE).replace("{{thumb_text_rule}}", THUMB_TEXT_RULE)
