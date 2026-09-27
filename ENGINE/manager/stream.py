"""
ENGINE/manager/stream.py — Stream manager with AI provider ranking.

Flow:
    1. Check cache → return instantly if hit
    2. Enrich with TMDB metadata (anime/asian detection)
    3. Derive content category for context-aware scoring
    4. Fan-out to all eligible providers concurrently
    5. First valid m3u8 wins early (speed); collect all results in parallel
    6. AI-score all results using multi-dimensional provider intelligence
    7. Deduplicate by URL
    8. Cache results
    9. Record rich health signals per provider (not just success/fail)

Scoring philosophy (replaces hardcoded _PROV_BONUS):
    Every provider is ranked by a composite of reliability (success rate,
    latency), availability (quality breadth, m3u8 vs iframe, subtitles, TTL),
    and context fit (how well it performs for this specific content category).
    A provider with 99% success rate but only one 360p iframe stream will
    rank below a provider with 85% success rate that returns 4 HLS qualities
    with subtitles. The system explains every ranking decision in plain text.

Rules:
    - Manager NEVER scrapes
    - Manager NEVER touches provider internals
    - Providers NEVER coordinate with each other
    - Registry NEVER runs provider logic
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional
from urllib.parse import urlparse

from CATALOG.tmdb import get_content_kind
from ENGINE.cache.cache import get as cache_get, set as cache_set, stream_key
from ENGINE.cache.ttl_policy import pick_best_ttl, ttl_to_ms
from ENGINE.manager.health import record, should_run, score_for_ranking, ContentCategory
from ENGINE.providers.base import safe_run, TimedOut, LinkData, Result, Stream
from ENGINE.providers.Stream.registry import get_all
from ENGINE.tools.warp import run_with_warp, normalize_mode
from config import get_settings

_s = get_settings()

# Stream type base score — used as a tiebreaker only, not the primary rank
_TYPE_SCORE  = {"m3u8": 10, "mp4": 6, "iframe": 2}
# Quality label → bonus points (tiebreaker within same provider score)
_QUAL_SCORE  = {"2160p": 100, "4k": 100, "1080p": 80, "720p": 60, "480p": 40, "360p": 20}


def _derive_category(data: LinkData) -> ContentCategory:
    """Map LinkData flags to a ContentCategory for context-aware scoring."""
    if data.is_anime:
        return "anime"
    if data.is_asian:
        return "asian"
    if data.is_bollywood:
        return "bollywood"
    if data.type == "tv":
        return "tv"
    return "movie"


def _stream_quality_score(stream: Stream) -> int:
    """Tiebreaker score for a single stream object."""
    s = _TYPE_SCORE.get(stream.type, 0)
    if stream.quality:
        q = stream.quality.lower().replace(" ", "")
        s += next((v for k, v in _QUAL_SCORE.items() if k in q), 0)
    return s


def _norm(url: str) -> str:
    try:
        p = urlparse(url)
        return p.netloc + p.path
    except Exception:
        return url


def _extract_rich_signals(streams: list[dict]) -> dict:
    """
    Pull signals from a batch of stream dicts to pass to health.record().
    Returns kwargs ready to unpack into record().
    """
    if not streams:
        return {
            "quality_count": 0,
            "has_m3u8": False,
            "has_mp4": False,
            "has_iframe": False,
            "has_subtitles": False,
            "ttl_seconds": 0,
        }
    types = {s.get("type", "") for s in streams}
    qualities = {s.get("quality") for s in streams if s.get("quality")}
    # TTL: take the minimum (weakest link)
    ttls = [s.get("_ttl_seconds", 0) for s in streams if s.get("_ttl_seconds", 0) > 0]
    return {
        "quality_count": len(qualities),
        "has_m3u8": "m3u8" in types,
        "has_mp4": "mp4" in types,
        "has_iframe": "iframe" in types,
        "has_subtitles": any(s.get("_has_subtitles") for s in streams),
        "ttl_seconds": min(ttls) if ttls else 0,
    }


async def _fan_out(data: LinkData, category: ContentCategory) -> tuple[Optional[dict], list[dict]]:
    providers = [p for p in get_all() if await should_run(p.id)]
    if not providers:
        return None, []

    # provider_id → list of stream dicts collected from that provider
    provider_streams: dict[str, list[dict]] = {}
    # provider_id → AI composite score (fetched once, used for final sort)
    provider_scores: dict[str, float] = {}
    lock = asyncio.Lock()

    # Pre-fetch AI scores for all providers (cheap — reads from in-memory deque)
    for p in providers:
        provider_scores[p.id] = await score_for_ranking(p.id, category)

    async def invoke(p) -> list[dict]:
        t0 = time.monotonic()
        result: Result = await safe_run(p, data, _s.provider_timeout_ms)
        ms = int((time.monotonic() - t0) * 1000)

        local: list[dict] = []
        for s in result.streams:
            if not s.url:
                continue
            # Compute TTL for this stream so we can store it as a signal
            from ENGINE.cache.ttl_policy import get_provider_ttl
            ttl_app, _ = get_provider_ttl(p.id, s.type)
            if s.expires_at_ms:
                from ENGINE.cache.ttl_policy import compute_ttl_from_expires
                ttl_app, _ = compute_ttl_from_expires(s.expires_at_ms, p.id, s.type)

            entry = {
                "provider":        p.name,
                "provider_id":     p.id,
                "server":          s.server or p.name,
                "url":             s.url,
                "type":            s.type,
                "quality":         s.quality,
                "headers":         s.headers,
                "playable":        s.type != "iframe",
                "expires_at_ms":   s.expires_at_ms,
                # Playback headers — whatever the provider says the CDN needs.
                # Empty dict means no extra headers required.
                "playback_headers": s.playback_headers,
                "_ttl_seconds":     ttl_app,
                "_has_subtitles":   bool(result.subtitles),
            }
            local.append(entry)

        # Determine outcome
        is_timed_out = isinstance(result, TimedOut)
        outcome = "found" if local else ("failed" if is_timed_out else "empty")

        # Record rich signals back to health tracker
        signals = _extract_rich_signals(local)
        # Pass the last-seen HTTP status from the provider result so the
        # admin panel can show per-provider error code breakdowns.
        http_status = getattr(result, 'http_status', 0)
        await record(
            p.id, outcome, ms,
            http_status=http_status,
            category=category,
            **signals,
        )

        async with lock:
            provider_streams[p.id] = local

        return local

    tasks = {asyncio.ensure_future(invoke(p)): p for p in providers}
    best_m3u8: Optional[dict] = None
    best_mp4:  Optional[dict] = None
    pending = set(tasks)

    # First valid m3u8 wins early — serves the user fast
    while pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for fut in done:
            for entry in (fut.result() or []):
                if entry["type"] == "m3u8" and best_m3u8 is None:
                    best_m3u8 = entry
                elif entry["type"] == "mp4" and best_mp4 is None:
                    best_mp4 = entry
        if best_m3u8:
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            break

    # ── AI ranking: sort all collected streams ────────────────────────────────
    #
    # Primary key:  provider's composite AI score (0–100)
    # Secondary key: stream quality tiebreaker (type + resolution)
    #
    # This replaces the old _score(stream, pid) + hardcoded _PROV_BONUS.
    # A provider with avg_quality_count=4 and m3u8_rate=0.95 outranks one
    # with 99% success but iframe-only output — even if the iframe provider
    # has historically been listed first.

    all_entries: list[tuple[dict, float, int]] = []  # (entry, ai_score, quality_score)
    for pid, streams in provider_streams.items():
        ai_score = provider_scores.get(pid, 50.0)
        for entry in streams:
            qs = _stream_quality_score(Stream(
                url=entry["url"],
                type=entry["type"],
                quality=entry.get("quality"),
            ))
            all_entries.append((entry, ai_score, qs))

    # Sort: highest AI score first, then highest quality score
    all_entries.sort(key=lambda x: (x[1], x[2]), reverse=True)

    # Deduplicate by normalised URL path
    seen: set[str] = set()
    deduped: list[dict] = []
    for entry, _, _ in all_entries:
        k = _norm(entry["url"])
        if k not in seen:
            seen.add(k)
            # Remove internal-only fields before returning
            clean = {k: v for k, v in entry.items() if not k.startswith("_")}
            deduped.append(clean)

    for i, e in enumerate(deduped):
        e["priority"] = i

    winner = best_m3u8 or best_mp4
    # Clean winner too
    if winner:
        winner = {k: v for k, v in winner.items() if not k.startswith("_")}

    return winner, deduped


async def get_streams(req, *, fresh: bool = False, warp_mode: str = "off") -> dict:
    t0 = time.monotonic()
    key = stream_key(req.tmdb_id, req.type, req.season, req.episode)

    if not fresh:
        cached = await cache_get(key)
        if cached:
            streams = cached.get("streams", [])
            best = next((s for s in streams if s.get("type") == "m3u8"), streams[0] if streams else None)
            app_ttl_s, cf_max_age_s = pick_best_ttl(streams) if streams else (0, 0)
            return {
                "ok":           bool(best),
                "stream":       best,
                "streams":      streams,
                "cached":       True,
                "took_ms":      int((time.monotonic() - t0) * 1000),
                "cache_ttl_ms": ttl_to_ms(app_ttl_s),
                "cf_max_age_s": cf_max_age_s,
            }

    meta = await get_content_kind(req.tmdb_id, req.type)
    data = LinkData(
        tmdb_id      = req.tmdb_id,
        type         = req.type,
        # req.title is never populated by the API routes (EngineRequest defaults
        # it to ""), so fall back to the TMDB-resolved title. Many providers
        # (stream, download, and ALL subtitle scrapers) search by title and
        # silently return nothing if this is blank.
        title        = req.title or meta["title"] or "",
        imdb_id      = req.imdb_id,
        year         = req.year or meta["year"],
        season       = req.season,
        episode      = req.episode,
        is_anime     = meta["is_anime"],
        is_asian     = meta["is_asian"],
        is_bollywood = meta["is_bollywood"],
        org_title    = meta["org_title"],
    )

    category = _derive_category(data)
    mode = normalize_mode(warp_mode)
    winner, streams = await run_with_warp(lambda: _fan_out(data, category), mode=mode)

    app_ttl_s, cf_max_age_s = pick_best_ttl(streams) if streams else (0, 0)
    cache_ttl_ms = ttl_to_ms(app_ttl_s)

    if streams and app_ttl_s > 0:
        await cache_set(key, {"streams": streams}, ttl=app_ttl_s)

    return {
        "ok":           winner is not None,
        "stream":       winner,
        "streams":      streams,
        "cached":       False,
        "took_ms":      int((time.monotonic() - t0) * 1000),
        "error":        None if winner else "No streams found",
        "cache_ttl_ms": cache_ttl_ms,
        "cf_max_age_s": cf_max_age_s,
    }
