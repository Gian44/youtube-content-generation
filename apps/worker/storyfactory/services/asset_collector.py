"""Asset collector - downloads licensed background footage from Pexels, Pixabay, and local sources.

Supports story-aware asset collection: each story gets background videos
that are contextually relevant to its content and category.
"""

import hashlib
import os
import random
import time
import uuid
from pathlib import Path

import httpx

from storyfactory.channel_context import get_current_channel_id, resolve_api_key
from storyfactory.config import get_settings
from storyfactory.db.engine import get_session
from storyfactory.db.models import Asset
from storyfactory.logger import get_logger
from storyfactory.services.api_tracker import track_api_call

log = get_logger("asset_collector")

# ============================================================
# Category-aware search queries for background footage
# Each category maps to a list of atmospheric/relevant queries
# that produce visually engaging stock footage.
# ============================================================
CATEGORY_SEARCH_QUERIES = {
    "aita": [
        "family dinner table", "living room conversation", "people talking",
        "couple arguing", "kitchen family", "restaurant dining",
        "city apartment interior", "suburban house", "coffee shop conversation",
        "pensive person window", "walking alone street",
    ],
    "relationships": [
        "couple walking sunset", "romantic dinner", "city lights night",
        "rainy window reflection", "coffee for two", "empty park bench",
        "ocean sunset", "candle light dinner", "holding hands close up",
        "autumn leaves path", "morning sunrise",
    ],
    "cheating": [
        "person alone night city", "dark room shadows", "rain on window",
        "empty bed morning", "broken glass close up", "city night moody",
        "driving alone highway", "sad person sitting", "storm clouds",
        "phone screen dark room", "walking away silhouette",
    ],
    "revenge": [
        "dramatic sunset clouds", "lightning storm", "fire flames close up",
        "chess pieces strategy", "person walking confidently", "city skyline power",
        "crashing ocean waves", "dark smoke rising", "glowing embers",
        "sunrise new beginning", "eagle soaring sky",
    ],
    "workplace_drama": [
        "office building exterior", "business meeting room", "typing keyboard",
        "city commute morning", "corporate hallway", "desk workspace",
        "skyscraper windows", "busy street commuters", "elevator doors",
        "coffee break office", "modern office interior",
    ],
    "entitled_parents": [
        "shopping mall crowd", "grocery store aisle", "playground children",
        "suburban neighborhood", "restaurant busy", "airport terminal",
        "school building exterior", "parking lot", "waiting room",
        "family park picnic", "department store",
    ],
    "family_drama": [
        "family gathering dinner", "holiday decorations home", "old photo album",
        "house exterior suburban", "kitchen cooking", "backyard garden",
        "family reunion park", "birthday celebration", "wedding venue",
        "moving boxes home", "fireplace living room",
    ],
    "confessions": [
        "person writing journal", "candle flame dark", "rain window night",
        "empty church interior", "fog morning nature", "mirror reflection",
        "quiet library", "moonlight through trees", "solitary walk beach",
        "train window passing scenery", "dimly lit room",
    ],
    "scary_stories": [
        "dark forest night", "abandoned house exterior", "foggy road",
        "creepy hallway", "full moon clouds", "old staircase dark",
        "flickering lights", "misty graveyard", "shadow on wall",
        "thunder lightning night", "dark basement stairs",
    ],
    "creepy_encounters": [
        "dark alley night", "empty parking garage", "security camera footage",
        "streetlight fog", "abandoned building", "night drive road",
        "dark woods path", "empty street night", "stairwell shadows",
        "rainy night city", "lonely bus stop",
    ],
    "wholesome": [
        "golden hour nature", "puppy playing park", "friends laughing together",
        "sunrise mountain peak", "flower garden blooming", "rainbow after rain",
        "children playing outside", "warm sunshine meadow", "cozy fireplace",
        "ocean waves peaceful", "butterfly flower garden",
    ],
    "mysteries": [
        "fog over lake", "old map close up", "detective desk papers",
        "clock gears close up", "abandoned room", "dusty bookshelf",
        "key and lock close up", "compass navigation", "mysterious forest path",
        "old photographs sepia", "lighthouse night fog",
    ],
    "customer_service_drama": [
        "retail store interior", "cash register close up", "shopping mall busy",
        "customer service desk", "restaurant kitchen busy", "phone ringing office",
        "fast food restaurant", "hotel lobby", "waiting in line queue",
        "credit card payment", "delivery truck driving",
    ],
}

# Fallback queries for categories not in the mapping
FALLBACK_QUERIES = [
    "abstract motion background", "nature timelapse", "ocean waves",
    "city nightscape timelapse", "aurora borealis", "flowing water stream",
    "space nebula stars", "underwater coral reef", "clouds sky timelapse",
    "geometric patterns motion", "particle effects dark", "rain on glass",
]

# Pagination / backoff for large image pulls (sleep slideshows want 100+ images).
_PEXELS_PER_PAGE = 80          # Pexels hard max per page
_PIXABAY_PER_PAGE = 100        # Pixabay allows 3..200
_PIXABAY_MIN_PER_PAGE = 3      # Pixabay rejects per_page < 3
_MAX_PAGES = 25                # safety bound so a query can't loop forever
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_BACKOFF_SECONDS = (1.0, 3.0)  # waits before each retry of a rate-limited request

# Blocked query terms - avoid copyrighted/branded content
BLOCKED_TERMS = [
    "minecraft", "fortnite", "roblox", "mario", "sonic", "zelda",
    "pokemon", "gta", "call of duty", "apex legends", "valorant",
    "league of legends", "overwatch", "fifa", "nba", "nfl",
    "disney", "marvel", "dc comics", "star wars", "harry potter",
    "tiktok", "instagram", "youtube", "reddit",
    "subway surfer", "subway surfers", "parkour",
]


def collect_story_relevant_assets(
    story,
    count: int = 5,
    asset_type: str = "video",
) -> list[Asset]:
    """Collect background assets relevant to a specific story.

    Uses the story's category and content to find contextually
    appropriate background footage.

    Args:
        story: A Story object with category, title, and body fields
        count: Number of assets to collect (more = better coverage for looping)
        asset_type: 'video' or 'image'

    Returns:
        List of collected Asset records with story-relevant content
    """
    settings = get_settings()

    # Step 1: Get AI-generated search keywords from the story text
    search_queries = _get_story_search_queries(story, count=3)

    # Step 2: Fall back to category-based queries if AI queries are empty
    if not search_queries:
        search_queries = _get_category_queries(
            getattr(story, "category", ""), count=3
        )

    log.info(
        "story_asset_queries",
        story_id=getattr(story, "id", "unknown")[:8],
        category=getattr(story, "category", ""),
        queries=search_queries,
    )

    all_assets = []

    # Step 3: Search with each query to get diverse, relevant footage
    for query in search_queries:
        if len(all_assets) >= count:
            break

        remaining = count - len(all_assets)
        assets = collect_background_assets(
            query=query,
            count=min(remaining, 3),
            asset_type=asset_type,
        )
        all_assets.extend(assets)

    # Step 4: If we still don't have enough, try category fallbacks
    if len(all_assets) < count:
        category = getattr(story, "category", "")
        fallback_queries = _get_category_queries(category, count=2)
        for query in fallback_queries:
            if len(all_assets) >= count:
                break
            remaining = count - len(all_assets)
            assets = collect_background_assets(
                query=query,
                count=min(remaining, 2),
                asset_type=asset_type,
            )
            all_assets.extend(assets)

    # Step 5: If still not enough, use generic fallbacks
    if len(all_assets) < 1:
        fallback = random.choice(FALLBACK_QUERIES)
        all_assets.extend(
            collect_background_assets(query=fallback, count=count, asset_type=asset_type)
        )

    log.info(
        "story_assets_collected",
        story_id=getattr(story, "id", "unknown")[:8],
        count=len(all_assets),
    )
    return all_assets


def _get_story_search_queries(story, count: int = 3) -> list[str]:
    """Use AI to extract visual search keywords from a story.

    Generates short, stock-footage-friendly search terms that describe
    the visual setting and mood of the story.

    Falls back to category-based queries in dry-run mode or on error.
    """
    settings = get_settings()

    if settings.dry_run:
        return _get_category_queries(
            getattr(story, "category", ""), count=count
        )

    # Only use AI if OpenAI is configured for the active channel
    openai_key = resolve_api_key("text.openai", "openai_api_key")
    if not openai_key or openai_key.startswith("sk-your"):
        return _get_category_queries(
            getattr(story, "category", ""), count=count
        )

    try:
        from storyfactory.services.ai_provider import generate_text, AIProvider

        title = getattr(story, "title", "")
        body = getattr(story, "body", "")
        # Use first 300 chars of body to keep token usage low
        body_snippet = body[:300] if body else ""

        prompt = (
            f"Given this story title and excerpt, generate exactly {count} short "
            f"stock video search queries (2-4 words each) that describe the visual "
            f"setting, mood, or atmosphere of the story. These will be used to search "
            f"for background footage on stock video sites like Pexels and Pixabay.\n\n"
            f"Rules:\n"
            f"- Each query should be 2-4 words\n"
            f"- Focus on visual scenes, settings, and moods\n"
            f"- Do NOT include character names or story-specific details\n"
            f"- Do NOT include copyrighted/branded terms\n"
            f"- Queries should match real stock footage categories\n\n"
            f"Title: {title}\n"
            f"Excerpt: {body_snippet}\n\n"
            f'Return a JSON object with a "queries" array of strings.'
        )

        raw = generate_text(
            prompt=prompt,
            provider=AIProvider.OPENAI,
            temperature=0.7,
            max_tokens=150,
            response_format="json",
        )

        import json
        data = json.loads(raw)
        queries = data.get("queries", [])

        # Validate and sanitize
        valid_queries = []
        for q in queries[:count]:
            q = str(q).strip().lower()
            if len(q) < 3 or len(q) > 60:
                continue
            # Check against blocked terms
            blocked = False
            for term in BLOCKED_TERMS:
                if term in q:
                    blocked = True
                    break
            if not blocked:
                valid_queries.append(q)

        if valid_queries:
            log.info("ai_search_queries_generated", queries=valid_queries)
            return valid_queries

    except Exception as e:
        log.warning("ai_search_queries_failed", error=str(e))

    # Fallback to category-based queries
    return _get_category_queries(
        getattr(story, "category", ""), count=count
    )


def collect_topic_assets(
    queries: list[str],
    count: int = 8,
    asset_type: str = "video",
) -> list[Asset]:
    """Collect background assets that match an explicit list of topic queries.

    Used by topic-driven pipelines (e.g. Sleep On Facts) where the on-screen
    visuals must match the script's subject rather than a drama category. Each
    query is sanitized against ``BLOCKED_TERMS`` by ``collect_background_assets``.
    Falls back to generic atmospheric footage if nothing matches.
    """
    clean_queries = [str(q).strip() for q in (queries or []) if str(q).strip()]
    if not clean_queries:
        clean_queries = [random.choice(FALLBACK_QUERIES)]

    # Spread the target across the queries (ceil), so ~150 images come from a
    # diverse set of on-topic searches rather than one over-fetched query.
    per_query = max(3, -(-count // len(clean_queries)))

    all_assets: list[Asset] = []
    seen_urls: set[str] = set()
    for query in clean_queries:
        if len(all_assets) >= count:
            break
        remaining = count - len(all_assets)
        batch = collect_background_assets(
            query=query, count=min(per_query, remaining), asset_type=asset_type
        )
        for asset in batch:
            url = getattr(asset, "original_url", None)
            if url and url in seen_urls:
                continue  # de-duplicate across queries
            if url:
                seen_urls.add(url)
            all_assets.append(asset)
            if len(all_assets) >= count:
                break

    if len(all_assets) < 1:
        fallback = random.choice(FALLBACK_QUERIES)
        all_assets.extend(
            collect_background_assets(query=fallback, count=count, asset_type=asset_type)
        )

    log.info(
        "topic_assets_collected",
        queries=clean_queries[:5],
        requested=count,
        count=len(all_assets),
    )
    return all_assets


def _get_category_queries(category: str, count: int = 3) -> list[str]:
    """Get search queries based on the story category."""
    queries = CATEGORY_SEARCH_QUERIES.get(category, FALLBACK_QUERIES)
    # Pick random subset to get variety
    if len(queries) > count:
        return random.sample(queries, count)
    return queries[:count]


def collect_background_assets(
    query: str | None = None,
    count: int = 3,
    asset_type: str = "video",
    category: str | None = None,
) -> list[Asset]:
    """Collect background assets from approved providers.

    Args:
        query: Search query for stock footage
        count: Number of assets to collect
        asset_type: 'video' or 'image'
        category: Optional story category for category-aware defaults

    Returns:
        List of collected Asset records
    """
    settings = get_settings()

    # Use category-aware query if none specified
    if query is None:
        if category:
            query = random.choice(
                CATEGORY_SEARCH_QUERIES.get(category, FALLBACK_QUERIES)
            )
        else:
            query = random.choice(FALLBACK_QUERIES)

    # Validate query against blocked terms
    query_lower = query.lower()
    for blocked in BLOCKED_TERMS:
        if blocked in query_lower:
            log.warning("blocked_query_term", query=query, blocked=blocked)
            query = random.choice(FALLBACK_QUERIES)
            break

    assets = []

    # Resolve per-channel provider keys (falling back to global env)
    pexels_key = resolve_api_key("assets.pexels", "pexels_api_key")
    pixabay_key = resolve_api_key("assets.pixabay", "pixabay_api_key")

    # Try Pexels first
    if pexels_key and pexels_key != "your-pexels-api-key":
        pexels_assets = _collect_from_pexels(query, count, asset_type, pexels_key)
        assets.extend(pexels_assets)

    # Fill remaining from Pixabay
    remaining = count - len(assets)
    if remaining > 0 and pixabay_key and pixabay_key != "your-pixabay-api-key":
        pixabay_assets = _collect_from_pixabay(query, remaining, asset_type, pixabay_key)
        assets.extend(pixabay_assets)

    # Check local assets as fallback
    remaining = count - len(assets)
    if remaining > 0:
        local_assets = _collect_local_assets(query, remaining, asset_type)
        assets.extend(local_assets)

    if settings.dry_run and not assets:
        assets = _create_dry_run_assets(query, count, asset_type)

    log.info("assets_collected", query=query, count=len(assets))
    return assets


def _get_with_backoff(client, url: str, *, params: dict, headers: dict | None = None):
    """GET with a small retry on rate-limit/transient errors. Returns the response
    (possibly non-200) or ``None`` on a transport error."""
    for attempt in range(len(_BACKOFF_SECONDS) + 1):
        try:
            response = client.get(url, params=params, headers=headers, timeout=30)
        except Exception as e:  # noqa: BLE001 — transport error → caller treats as no data
            log.warning("asset_http_error", url=url, error=str(e))
            return None
        if response.status_code in _RETRYABLE_STATUS and attempt < len(_BACKOFF_SECONDS):
            time.sleep(_BACKOFF_SECONDS[attempt])
            continue
        return response
    return response


def _collect_from_pexels(query: str, count: int, asset_type: str, api_key: str) -> list[Asset]:
    """Collect assets from Pexels API, paginating until ``count`` is reached.

    Pexels Video Search API: GET https://api.pexels.com/videos/search
    Pexels Photo Search API: GET https://api.pexels.com/v1/search
    """
    channel_id = get_current_channel_id()
    session = get_session()
    assets: list[Asset] = []
    seen_urls: set[str] = set()
    is_video = asset_type == "video"
    url = "https://api.pexels.com/videos/search" if is_video else "https://api.pexels.com/v1/search"
    key_field = "videos" if is_video else "photos"
    endpoint = "videos/search" if is_video else "v1/search"

    try:
        page = 1
        with httpx.Client() as client:
            while len(assets) < count and page <= _MAX_PAGES:
                per_page = max(1, min(_PEXELS_PER_PAGE, count - len(assets)))
                response = _get_with_backoff(
                    client,
                    url,
                    params={
                        "query": query,
                        "per_page": per_page,
                        "page": page,
                        "orientation": "landscape",
                    },
                    headers={"Authorization": api_key},
                )
                track_api_call(
                    provider="pexels",
                    endpoint=endpoint,
                    status_code=(response.status_code if response else None),
                )
                if response is None or response.status_code != 200:
                    if response is not None:
                        log.warning(
                            "pexels_api_error", status=response.status_code, body=response.text[:200]
                        )
                    break

                items = response.json().get(key_field, [])
                if not items:
                    break

                before = len(assets)
                for item in items:
                    if len(assets) >= count:
                        break
                    if is_video:
                        video_files = item.get("video_files", [])
                        best_file = next(
                            (
                                vf for vf in video_files
                                if vf.get("quality") == "hd" or vf.get("height", 0) >= 720
                            ),
                            video_files[0] if video_files else None,
                        )
                        if not best_file:
                            continue
                        original_url = best_file.get("link", "")
                        width = best_file.get("width", 0)
                        height = best_file.get("height", 0)
                        duration = item.get("duration", 0)
                        kind = "Video"
                    else:
                        original_url = item.get("src", {}).get("original", "")
                        width = item.get("width", 0)
                        height = item.get("height", 0)
                        duration = None
                        kind = "Photo"

                    if not original_url or original_url in seen_urls:
                        continue
                    seen_urls.add(original_url)

                    creator = item.get("user", {}).get("name", "Unknown")
                    asset = Asset(
                        id=str(uuid.uuid4()),
                        channel_id=channel_id,
                        provider="pexels",
                        type=asset_type,
                        original_url=original_url,
                        creator=creator,
                        license="Pexels License (Free)",
                        attribution=f"{kind} by {creator} from Pexels",
                        source_query=query,
                        checksum="",
                        duration_seconds=duration,
                        width=width,
                        height=height,
                        local_path="",
                        usage_count=0,
                        policy_status="verified",
                    )
                    session.add(asset)
                    assets.append(asset)

                # No new (non-duplicate) items this page → the result set is
                # exhausted; stop paging to avoid burning API quota on repeats.
                if len(assets) == before:
                    break
                page += 1

        session.commit()

    except Exception as e:
        session.rollback()
        log.error("pexels_collection_failed", error=str(e))
    finally:
        session.close()

    return assets


def _collect_from_pixabay(query: str, count: int, asset_type: str, api_key: str) -> list[Asset]:
    """Collect assets from Pixabay API.

    Pixabay Video Search API: GET https://pixabay.com/api/videos/
    Pixabay Image Search API: GET https://pixabay.com/api/
    """
    channel_id = get_current_channel_id()
    session = get_session()
    assets: list[Asset] = []
    seen_urls: set[str] = set()
    is_video = asset_type == "video"
    url = "https://pixabay.com/api/videos/" if is_video else "https://pixabay.com/api/"
    endpoint = f"api/{'videos/' if is_video else ''}"

    try:
        page = 1
        with httpx.Client() as client:
            while len(assets) < count and page <= _MAX_PAGES:
                # Pixabay rejects per_page < 3, so floor it and slice locally.
                per_page = max(_PIXABAY_MIN_PER_PAGE, min(_PIXABAY_PER_PAGE, count - len(assets)))
                response = _get_with_backoff(
                    client,
                    url,
                    params={
                        "key": api_key,
                        "q": query,
                        "per_page": per_page,
                        "page": page,
                        "safesearch": "true",
                    },
                )
                track_api_call(
                    provider="pixabay",
                    endpoint=endpoint,
                    status_code=(response.status_code if response else None),
                )
                if response is None or response.status_code != 200:
                    if response is not None:
                        log.warning(
                            "pixabay_api_error", status=response.status_code, body=response.text[:200]
                        )
                    break

                items = response.json().get("hits", [])
                if not items:
                    break

                before = len(assets)
                for item in items:
                    if len(assets) >= count:
                        break
                    if is_video:
                        videos = item.get("videos", {})
                        best = videos.get("large", {})
                        if not best.get("url"):
                            best = videos.get("medium", {})
                        if not best.get("url"):
                            best = videos.get("small", {})
                        original_url = best.get("url", "")
                        width = best.get("width", 0)
                        height = best.get("height", 0)
                        duration = item.get("duration", 0)
                    else:
                        original_url = item.get("largeImageURL", "")
                        width = item.get("imageWidth", 0)
                        height = item.get("imageHeight", 0)
                        duration = None

                    if not original_url or original_url in seen_urls:
                        continue
                    seen_urls.add(original_url)

                    creator = item.get("user", "Unknown")
                    asset = Asset(
                        id=str(uuid.uuid4()),
                        channel_id=channel_id,
                        provider="pixabay",
                        type=asset_type,
                        original_url=original_url,
                        creator=creator,
                        license="Pixabay License (Free)",
                        attribution=f"By {creator} from Pixabay",
                        source_query=query,
                        checksum="",
                        duration_seconds=duration,
                        width=width,
                        height=height,
                        local_path="",
                        usage_count=0,
                        policy_status="verified",
                    )
                    session.add(asset)
                    assets.append(asset)

                # No new (non-duplicate) items this page → the result set is
                # exhausted; stop paging to avoid burning API quota on repeats.
                if len(assets) == before:
                    break
                page += 1

        session.commit()

    except Exception as e:
        session.rollback()
        log.error("pixabay_collection_failed", error=str(e))
    finally:
        session.close()

    return assets


def _collect_local_assets(query: str, count: int, asset_type: str) -> list[Asset]:
    """Collect assets from local uploads folder."""
    settings = get_settings()
    local_dir = Path(settings.local_storage_path) / "assets" / asset_type
    if not local_dir.exists():
        return []

    session = get_session()
    assets = []

    try:
        extensions = {
            "video": [".mp4", ".webm", ".mov"],
            "image": [".jpg", ".jpeg", ".png", ".webp"],
            "audio": [".mp3", ".wav", ".ogg"],
        }
        valid_ext = extensions.get(asset_type, [])

        files = [
            f for f in local_dir.iterdir()
            if f.is_file() and f.suffix.lower() in valid_ext
        ]

        # Sort by least used
        for file_path in files[:count]:
            checksum = hashlib.md5(file_path.read_bytes()).hexdigest()

            existing = session.query(Asset).filter_by(checksum=checksum).first()
            if existing:
                existing.usage_count += 1
                assets.append(existing)
                continue

            asset = Asset(
                id=str(uuid.uuid4()),
                channel_id=get_current_channel_id(),
                provider="local",
                type=asset_type,
                original_url=f"file://{file_path}",
                creator="Local Upload",
                license="User-provided",
                attribution="Local asset",
                source_query=query,
                checksum=checksum,
                local_path=str(file_path),
                usage_count=1,
                policy_status="verified",
            )
            session.add(asset)
            assets.append(asset)

        session.commit()

    except Exception as e:
        session.rollback()
        log.error("local_collection_failed", error=str(e))
    finally:
        session.close()

    return assets


def _create_dry_run_assets(query: str, count: int, asset_type: str) -> list[Asset]:
    """Create placeholder assets for dry-run mode."""
    session = get_session()
    assets = []

    try:
        for i in range(count):
            asset = Asset(
                id=str(uuid.uuid4()),
                channel_id=get_current_channel_id(),
                provider="local",
                type=asset_type,
                original_url=f"dry-run://placeholder-{i}",
                creator="Dry Run",
                license="Placeholder",
                attribution="Dry run placeholder asset",
                source_query=query,
                checksum=f"dryrun_{i}_{hashlib.md5(query.encode()).hexdigest()[:8]}",
                local_path="",
                usage_count=0,
                policy_status="verified",
            )
            session.add(asset)
            assets.append(asset)

        session.commit()
    except Exception as e:
        session.rollback()
        log.error("dry_run_assets_failed", error=str(e))
    finally:
        session.close()

    return assets


def download_assets(assets: list[Asset], output_dir: str = "./data/assets") -> list[Asset]:
    """Download a list of assets to local storage."""
    for asset in assets:
        if not asset.local_path or not os.path.exists(asset.local_path):
            local_path = download_asset(asset, output_dir)
            if local_path:
                asset.local_path = local_path
    return assets


def _download_extension(asset_type: str) -> str:
    """File extension to save a downloaded asset under, by asset type."""
    return {"video": ".mp4", "audio": ".mp3", "image": ".jpg"}.get(asset_type, ".jpg")


def download_asset(asset: Asset, output_dir: str = "./data/assets") -> str:
    """Download an asset file to local storage.

    Returns:
        Local file path
    """
    if asset.local_path and os.path.exists(asset.local_path):
        return asset.local_path

    if asset.original_url.startswith("dry-run://") or asset.original_url.startswith("file://"):
        return asset.local_path or ""

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    ext = _download_extension(asset.type)
    filename = f"{asset.id[:8]}_{asset.provider}{ext}"
    output_path = os.path.join(output_dir, filename)

    try:
        with httpx.Client(follow_redirects=True) as client:
            response = client.get(asset.original_url, timeout=120)
            response.raise_for_status()

            with open(output_path, "wb") as f:
                f.write(response.content)

        # Update asset with local path and checksum
        session = get_session()
        try:
            db_asset = session.query(Asset).filter_by(id=asset.id).first()
            if db_asset:
                db_asset.local_path = output_path
                db_asset.checksum = hashlib.md5(response.content).hexdigest()
            session.commit()
        finally:
            session.close()

        log.info("asset_downloaded", asset_id=asset.id, path=output_path)
        return output_path

    except Exception as e:
        log.error("asset_download_failed", asset_id=asset.id, error=str(e))
        return ""


def validate_asset_license(asset: Asset) -> bool:
    """Validate that an asset has a verified license."""
    settings = get_settings()

    if asset.policy_status == "verified":
        return True

    if asset.policy_status == "rejected":
        return False

    if asset.provider in ("pexels", "pixabay", "jamendo"):
        # These providers serve free / Creative Commons licensed media.
        return True

    if asset.provider == "local":
        # User-provided assets are assumed to be licensed
        return True

    if settings.fail_safe_on_license_unknown:
        log.warning("unknown_license_rejected", asset_id=asset.id, provider=asset.provider)
        return False

    return True
