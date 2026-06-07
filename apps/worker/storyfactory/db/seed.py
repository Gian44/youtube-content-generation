"""Database seed data - default prompt templates and sample data."""

import uuid
from datetime import datetime, timezone, timedelta

from rich.console import Console

from storyfactory.db.engine import init_db, get_session
from storyfactory.db.models import (
    SettingsModel,
    PromptTemplate,
    DailyBatch,
    Story,
    Asset,
)

console = Console()

# ============================================
# Default Prompt Templates
# ============================================

DEFAULT_PROMPTS = [
    {
        "name": "topic_selection",
        "description": "Select the daily topic/category based on weights and cooldown",
        "category": "topic",
        "template": """You are the topic selector for a Reddit-style story YouTube channel.

Available categories with weights:
{{categories_with_weights}}

Recently used categories (within cooldown):
{{recent_categories}}

Select ONE category for today's content batch. Consider:
1. Higher weight = more popular with audience
2. Avoid recently used categories
3. Seasonal relevance
4. Audience engagement patterns

Respond with ONLY the category key (e.g., "aita", "revenge", "workplace_drama").""",
        "variables": ["categories_with_weights", "recent_categories"],
    },
    {
        "name": "short_story_generation",
        "description": "Generate a short fictional story (35-60 seconds, 100-160 words)",
        "category": "story",
        "template": """Write an original fictional short story in the style of a Reddit {{category}} post.

Requirements:
- 100-160 words total
- Target duration: 35-60 seconds when read aloud
- Voice persona: {{persona}}
- Must be completely original fiction
- Do NOT reference Reddit or claim this is a real post

Structure:
1. HOOK (first sentence, must grab attention in 3 seconds): Start with a shocking, intriguing, or emotionally charged statement.
2. SETUP: Briefly establish the situation and characters.
3. ESCALATION: Build tension or conflict.
4. TWIST/PAYOFF: Deliver a surprising or satisfying conclusion.
5. COMMENT BAIT: End with a provocative question to drive engagement.

Topic hints: {{topic_hints}}
Avoid these hooks (already used recently): {{used_hooks}}

Output as JSON:
{
  "title": "...",
  "hook": "...",
  "body": "...",
  "comment_bait": "...",
  "word_count": <number>
}""",
        "variables": ["category", "persona", "topic_hints", "used_hooks"],
    },
    {
        "name": "long_form_story_generation",
        "description": "Generate a longer story for the long-form compilation video",
        "category": "story",
        "template": """Write an original fictional story in the style of a Reddit {{category}} post.

This story is for a longer YouTube video compilation. It should be more detailed and nuanced than a Short.

Requirements:
- 300-600 words
- Target duration: 2-4 minutes when read aloud
- Voice persona: {{persona}}
- Must be completely original fiction
- More character development and plot complexity than a Short
- Do NOT reference Reddit or claim this is a real post

Structure:
1. HOOK: Compelling opening line
2. BACKSTORY: Character and relationship context
3. INCITING INCIDENT: What triggered the conflict
4. ESCALATION: 2-3 escalating events
5. CLIMAX: Peak of conflict
6. RESOLUTION/TWIST: Outcome
7. REFLECTION: Brief narrator reflection
8. COMMENT BAIT: Engagement question

Topic hints: {{topic_hints}}

Output as JSON:
{
  "title": "...",
  "hook": "...",
  "body": "...",
  "comment_bait": "...",
  "word_count": <number>
}""",
        "variables": ["category", "persona", "topic_hints"],
    },
    {
        "name": "recap_short_script",
        "description": "Write a transformative recap narration for one Short cut from a TV/movie segment",
        "category": "story",
        "template": """You are writing a punchy, ORIGINAL recap narration for a YouTube Short.

The Short is one segment of "{{show_title}}" ({{episode_label}}), covering the
part of the episode from {{start_time}} to {{end_time}} (segment {{segment_index}} of {{total_segments}}).

Context about this moment (may be empty):
{{context}}

Write a TRANSFORMATIVE, commentary-style recap — your own words describing and
reacting to what happens. This is NOT a transcript or subtitle dump.

Requirements:
- Voice persona: {{persona}}
- Target 45-59 seconds when read aloud (about 110-150 words)
- Structure: (1) a 3-second HOOK that creates a curiosity gap, (2) a 1-2 sentence
  recap of what happens in this segment, (3) a CTA inviting viewers to watch/subscribe
- Do NOT quote dialogue verbatim; paraphrase and add insight/commentary
- Do NOT include spoilers beyond this segment
- Keep it spoiler-light, energetic, and original

Output as JSON:
{
  "title": "...",         // Short title (<= 90 chars), include the show name
  "hook": "...",          // the opening line
  "body": "...",          // the full narration to be spoken
  "comment_bait": "...",  // a question/CTA to drive comments
  "word_count": <number>
}""",
        "variables": [
            "show_title", "episode_label", "start_time", "end_time",
            "segment_index", "total_segments", "context", "persona",
        ],
    },
    {
        "name": "sleep_facts_long_form",
        "description": "Write a calm, single-topic 'facts to fall asleep to' long-form narration",
        "category": "story",
        "template": """You are the writer for a calm "facts to fall asleep to" channel.

Write a long, soothing, single-topic narration about ONE subject only:

TOPIC: {{topic}}

Reference notes (factual grounding — may be empty):
{{topic_hints}}

HARD RULES:
- The ENTIRE script must stay on the single topic "{{topic}}". Never drift into
  unrelated domains (e.g. if the topic is Ancient Egypt, do NOT talk about space
  or animals unless directly about Ancient Egypt).
- Calm, slow, gentle tone suitable for falling asleep. No loud hooks, no hype.
- Accurate, interesting facts. Vary sentence length; keep it flowing and serene.
- Aim for roughly {{target_minutes}} minutes of narration when read slowly
  (about {{target_words}} words). Write the full body, not an outline.

Output as JSON:
{
  "title": "...",                  // a calm, topic-specific title
  "hook": "...",                   // a gentle 1-sentence opening
  "body": "...",                   // the FULL calm narration (one continuous script)
  "asset_keywords": ["..."],       // 5-10 short visual keywords on-topic for stock footage
  "image_search_queries": ["..."], // 3-6 short stock-footage search queries on-topic
  "word_count": <number>
}""",
        "variables": ["topic", "topic_hints", "target_minutes", "target_words"],
    },
    {
        "name": "sleep_facts_outline",
        "description": "Plan a long (~3h) calm sleep video as an ordered list of on-topic 'movements'",
        "category": "story",
        "template": """You are the head writer for a calm "facts to fall asleep to" channel.
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
}""",
        "variables": ["topic", "topic_hints", "target_minutes", "num_movements"],
    },
    {
        "name": "sleep_facts_segment",
        "description": "Expand one outline movement into flowing calm bedtime narration",
        "category": "story",
        "template": """You are narrating a calm "facts to fall asleep to" video about ONE topic.
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
}""",
        "variables": [
            "topic", "movement_heading", "movement_beats",
            "running_summary", "previous_tail", "target_words",
        ],
    },
    {
        "name": "hook_generation",
        "description": "Generate attention-grabbing hooks for stories",
        "category": "hook",
        "template": """Generate 5 unique, attention-grabbing opening hooks for a {{category}} story.

Each hook must:
- Be 1-2 sentences max
- Create immediate curiosity or shock
- Work as a YouTube Short's first 3 seconds
- Be completely different in style from the others

Avoid these recently used hooks:
{{used_hooks}}

Category context: {{category_description}}

Output as JSON array:
["hook1", "hook2", "hook3", "hook4", "hook5"]""",
        "variables": ["category", "category_description", "used_hooks"],
    },
    {
        "name": "metadata_generation",
        "description": "Generate YouTube video title, description, and tags",
        "category": "metadata",
        "template": """Generate YouTube SEO metadata for a {{video_type}} video.

Category: {{category}}
Stories included: {{story_summaries}}

Requirements:
- Title: max 100 chars, attention-grabbing, include relevant keywords
- Description: 200-500 words, include story teasers, timestamps (if long-form), and the required disclosure
- Tags: 15-30 relevant tags for YouTube discovery
- MUST include this disclosure in the description: "{{disclosure_line}}"

Output as JSON:
{
  "title": "...",
  "description": "...",
  "tags": ["tag1", "tag2", ...],
  "pinned_comment": "..."
}""",
        "variables": ["video_type", "category", "story_summaries", "disclosure_line"],
    },
    {
        "name": "thumbnail_text_generation",
        "description": "Generate text overlay for video thumbnails",
        "category": "thumbnail",
        "template": """Generate thumbnail text for a {{video_type}} YouTube video.

Category: {{category}}
Main story hook: {{main_hook}}

Requirements:
- 2-5 words of bold text
- Maximum impact
- Creates curiosity gap
- Uses emotional trigger words

Output 3 options as JSON:
["option1", "option2", "option3"]""",
        "variables": ["video_type", "category", "main_hook"],
    },
    {
        "name": "pinned_comment_generation",
        "description": "Generate an engaging pinned comment",
        "category": "comment",
        "template": """Generate a pinned comment for a {{video_type}} YouTube video about {{category}} stories.

The comment should:
- Ask a provocative question
- Encourage viewers to share their own stories/opinions
- Be conversational and relatable
- Not sound like a bot

Output as plain text (the comment only).""",
        "variables": ["video_type", "category"],
    },
    {
        "name": "policy_review",
        "description": "Review content for policy violations",
        "category": "policy",
        "template": """Review the following story for content policy violations.

Story:
---
{{story_text}}
---

Check for these violations:
1. Explicit sexual content
2. Sexual content involving minors
3. Graphic violence (detailed gore/torture)
4. Hate speech / protected-class attacks
5. Real-person accusations
6. Real private information / doxxing
7. Self-harm detail or promotion
8. Instructions for illegal activity
9. Copyrighted characters or branded plots
10. Excessive profanity (more than 3 instances)
11. Medical/legal/financial advice claims
12. Real-world defamation-style claims

Output as JSON:
{
  "is_clean": true/false,
  "flags": [
    {
      "type": "flag_type",
      "severity": "block|rewrite|warn|info",
      "description": "why this was flagged",
      "excerpt": "the problematic text"
    }
  ],
  "suggested_rewrites": ["list of rewrite suggestions if severity is rewrite"]
}""",
        "variables": ["story_text"],
    },
    {
        "name": "story_rewrite",
        "description": "Rewrite flagged content to be policy-compliant",
        "category": "rewrite",
        "template": """Rewrite the following story to fix policy violations while keeping the core narrative.

Original Story:
---
{{story_text}}
---

Violations to fix:
{{violations}}

Requirements:
- Keep the same overall plot and structure
- Fix only the flagged issues
- Maintain the same word count range (±20%)
- Keep the same voice/persona style: {{persona}}
- Preserve the hook and comment bait quality

Output as JSON:
{
  "title": "...",
  "hook": "...",
  "body": "...",
  "comment_bait": "...",
  "word_count": <number>,
  "changes_made": ["description of each change"]
}""",
        "variables": ["story_text", "violations", "persona"],
    },
    {
        "name": "title_variation",
        "description": "Generate title variations for A/B testing",
        "category": "title_variation",
        "template": """Generate 5 title variations for a YouTube {{video_type}} video.

Original title: {{original_title}}
Category: {{category}}

Each variation should:
- Be under 100 characters
- Use different styles (question, statement, shock, emotional, curiosity gap)
- Be SEO-optimized
- Avoid clickbait that doesn't match the content

Output as JSON array:
["title1", "title2", "title3", "title4", "title5"]""",
        "variables": ["video_type", "original_title", "category"],
    },
]


def run_seed(sample: bool = False):
    """Seed the database with default data."""
    init_db()
    session = get_session()

    try:
        # Seed prompt templates
        console.print("[blue]Seeding prompt templates...[/blue]")
        for prompt_data in DEFAULT_PROMPTS:
            # Only check for the GLOBAL default (channel_id=NULL). A per-channel
            # override shares the name but must not mask a missing global row
            # (the unique index is on (channel_id, name)).
            existing = (
                session.query(PromptTemplate)
                .filter_by(name=prompt_data["name"], channel_id=None)
                .first()
            )
            if not existing:
                template = PromptTemplate(
                    id=str(uuid.uuid4()),
                    name=prompt_data["name"],
                    description=prompt_data["description"],
                    template=prompt_data["template"],
                    variables=prompt_data["variables"],
                    category=prompt_data["category"],
                    version=1,
                    is_active=True,
                )
                session.add(template)
                console.print(f"  [green]✓[/green] {prompt_data['name']}")
            else:
                console.print(f"  [yellow]⊘[/yellow] {prompt_data['name']} (exists)")

        # Seed default settings
        console.print("[blue]Seeding default settings...[/blue]")
        default_settings = {
            "disclosure_line": "These are original fictional stories created for entertainment.",
            "topic_cooldown_days": "2",
            "max_same_hook_per_week": "2",
            "shorts_per_day_min": "3",
            "shorts_per_day_max": "5",
            "long_form_per_day": "1",
        }
        for key, value in default_settings.items():
            existing = session.query(SettingsModel).filter_by(key=key).first()
            if not existing:
                setting = SettingsModel(key=key, value=value)
                session.add(setting)
                console.print(f"  [green]✓[/green] {key} = {value}")

        if sample:
            console.print("[blue]Seeding sample data...[/blue]")
            _seed_sample_data(session)

        session.commit()
        console.print("[bold green]✓ Database seeded successfully![/bold green]")

    except Exception as e:
        session.rollback()
        console.print(f"[bold red]✗ Seed failed: {e}[/bold red]")
        raise
    finally:
        session.close()


def _seed_sample_data(session):
    """Create sample batch and stories for development/testing."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    batch = DailyBatch(
        id=str(uuid.uuid4()),
        date=today,
        category="aita",
        status="completed",
        shorts_count=3,
        long_form_count=1,
        completed_at=datetime.now(timezone.utc),
    )
    session.add(batch)

    sample_stories = [
        {
            "title": "AITA for refusing to share my lottery winnings with family?",
            "hook": "I won $50,000 on a scratch ticket, and my family thinks they're entitled to half.",
            "body": "Last month I bought a scratch ticket on a whim at the gas station. I almost threw it away, but something told me to scratch it. When I saw the $50,000 prize, I nearly passed out. I told my mom, which was my first mistake. Within hours, my entire family knew. My brother called demanding $10,000 because he 'always supported me.' My aunt said she deserved some because she drove me to the store once. My mom thinks I should split it evenly among all siblings. I said no. I'm using it to pay off my student loans. Now half my family won't speak to me, and my mom says I'm being selfish.",
            "comment_bait": "Would you share your lottery winnings with family? How much?",
            "word_count": 128,
            "estimated_duration_seconds": 45,
            "voice_persona": "dramatic",
            "type": "short",
        },
        {
            "title": "AITA for reporting my neighbor's party at 3 AM?",
            "hook": "My neighbor threw a party so loud it set off car alarms, and now the whole building hates me.",
            "body": "I work early morning shifts starting at 5 AM. Last Saturday my neighbor decided to throw what sounded like a music festival in his apartment. Bass was shaking my walls. Glasses fell off my shelf. I politely knocked at midnight and asked them to turn it down. They laughed and closed the door. By 2 AM, car alarms were going off in the parking lot. I called in a noise complaint. Police showed up, party ended. Now my neighbor has turned the entire floor against me, saying I'm a 'fun killer.' Other neighbors who complained privately won't back me up publicly.",
            "comment_bait": "At what point would you call in a noise complaint?",
            "word_count": 122,
            "estimated_duration_seconds": 42,
            "voice_persona": "sarcastic",
            "type": "short",
        },
        {
            "title": "AITA for walking out of my own birthday dinner?",
            "hook": "My family threw me a birthday dinner but spent the whole time roasting me.",
            "body": "My family organized a dinner for my 30th birthday. I was really touched — until they started. First it was 'jokes' about me being single. Then my career choices. Then my apartment. My sister made a slideshow of my most embarrassing moments. Everyone was laughing. I sat there for an hour trying to be a good sport, but when my dad said 'at least you have your health' as the 'nice' comment, I just got up and left. My mom called me dramatic. My sister said it was 'all in fun.' My friends say I had every right to leave. My family says I ruined my own birthday.",
            "comment_bait": "Would you have stayed or walked out?",
            "word_count": 130,
            "estimated_duration_seconds": 46,
            "voice_persona": "calm",
            "type": "short",
        },
    ]

    import hashlib

    for i, story_data in enumerate(sample_stories):
        content_hash = hashlib.sha256(story_data["body"].encode()).hexdigest()
        story = Story(
            id=str(uuid.uuid4()),
            batch_id=batch.id,
            category="aita",
            type=story_data["type"],
            title=story_data["title"],
            hook=story_data["hook"],
            body=story_data["body"],
            comment_bait=story_data["comment_bait"],
            word_count=story_data["word_count"],
            estimated_duration_seconds=story_data["estimated_duration_seconds"],
            voice_persona=story_data["voice_persona"],
            originality_hash=content_hash,
            novelty_score=0.85,
            prompt_used="sample_seed",
            raw_output="sample_seed",
            policy_status="clean",
            policy_flags=[],
            order_index=i,
        )
        session.add(story)
        console.print(f"  [green]✓[/green] Story: {story_data['title'][:50]}...")

    console.print(f"  [green]✓[/green] Sample batch created for {today}")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    run_seed(sample=True)
