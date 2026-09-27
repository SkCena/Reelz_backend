"""
ENGINE/manager/download.py — Download manager.

Flow:
    1. Cache → return instantly if hit
    2. Fan-out to all Download providers (mp4/direct links)
    3. Fan-out to all Stream providers → resolve HLS masters → extract quality variants
    4. Merge all results: every quality from every provider
    5. Deduplicate by (quality, type) — keep best per slot
    6. Cache + return

Key design:
    - Download providers produce direct mp4/hls links
    - Stream providers also contribute: their m3u8 URLs are resolved to
      quality-specific index.m3u8 files so the app can download .ts segments
    - Result schema: {label, type, url, language, size_bytes, premium}
"""
from __future__ import annotations

import asyncio
import time

from CATALOG.tmdb import get_content_kind
from ENGINE.cache.cache import get as cache_get, set as cache_set, download_key
from ENGINE.cache.ttl_policy import pick_best_ttl, ttl_to_ms
from ENGINE.manager.health import record, should_run
from ENGINE.providers.base import safe_run, TimedOut, LinkData
from ENGINE.providers.Download.registry import get_all as get_download_providers
from ENGINE.providers.Stream.registry import get_all as get_stream_providers
from ENGINE.tools.hls import resolve_master
from config import get_settings

_s = get_settings()

_QUAL_WEIGHT = {"4k": 500, "2160p": 500, "1080p": 400, "720p": 300, "480p": 200, "360p": 100, "240p": 50}

def _qual_weight(label: str) -> int:
    return _QUAL_WEIGHT.get(label.lower().replace(" ", ""), 0)


async def _collect_from_download_providers(data: LinkData) -> list[dict]:
    """Fan-out to all dedicated Download providers."""
    providers = [p for p in get_download_providers() if await should_run(p.id)]
    collected = []

    async def invoke(p):
        t0 = time.monotonic()
        result = await safe_run(p, data, _s.provider_timeout_ms)
        ms = int((time.monotonic() - t0) * 1000)
        local = []
        for item in result.downloads:
            if not item.url:
                continue
            lbl = (item.quality or "").strip()
            if not lbl:
                # Infer from URL; fall back to "1080p" so the link is never silently dropped
                import re as _re
                m = _re.search(r"(2160|1080|720|480|360|240)p?", item.url, _re.I)
                lbl = (m.group(1) + "p") if m else "1080p"
            local.append({
                "provider":      p.name,
                "provider_id":   p.id,
                "label":         lbl,
                "type":          item.type,
                "url":           item.url,
                "language":      item.language,
                "size_bytes":    item.size_bytes,
                "headers":         item.headers,
                "expires_at_ms":   item.expires_at_ms,
                # Download headers — whatever the provider says the CDN needs.
                "playback_headers": item.playback_headers,
            })
        outcome = "found" if local else "failed" if isinstance(result, TimedOut) else "empty"
        await record(p.id, outcome, ms)
        return local

    results = await asyncio.gather(*[invoke(p) for p in providers], return_exceptions=True)
    for r in results:
        if isinstance(r, list):
            collected.extend(r)
    return collected


async def _collect_from_stream_providers(data: LinkData) -> list[dict]:
    """
    Fan-out to Stream providers, resolve HLS masters to quality-specific index.m3u8 URLs.
    MP4 streams are returned as-is. iframes are skipped (not downloadable).
    """
    providers = [p for p in get_stream_providers() if await should_run(p.id)]
    collected = []

    async def invoke(p):
        t0 = time.monotonic()
        result = await safe_run(p, data, _s.provider_timeout_ms)
        ms = int((time.monotonic() - t0) * 1000)
        local = []
        for s in result.streams:
            if not s.url or s.type == "iframe":
                continue
            if s.type == "mp4":
                # Use provider quality label; if absent, infer from URL, else use "1080p"
                import re as _re
                mp4_lbl = (s.quality or "").strip()
                if not mp4_lbl:
                    m = _re.search(r"(2160|1080|720|480|360|240)p?", s.url, _re.I)
                    mp4_lbl = (m.group(1) + "p") if m else "1080p"
                local.append({
                    "provider":      p.name,
                    "provider_id":   p.id,
                    "label":         mp4_lbl,
                    "type":          "mp4",
                    "url":           s.url,
                    "language":      "English",
                    "size_bytes":    0,
                    "headers":          s.headers,
                    "expires_at_ms":    s.expires_at_ms,
                    "playback_headers":  s.playback_headers,
                })
            elif s.type in ("m3u8", "hls"):
                variants = await resolve_master(s.url, headers=s.headers)
                for v in variants:
                    local.append({
                        "provider":      p.name,
                        "provider_id":   p.id,
                        "label":         v["quality"],
                        "type":          "hls",
                        "url":           v["url"],
                        "language":      "English",
                        "size_bytes":    0,
                        "headers":          s.headers,
                        "expires_at_ms":    s.expires_at_ms,
                        "playback_headers":  s.playback_headers,
                    })
        outcome = "found" if local else "failed" if isinstance(result, TimedOut) else "empty"
        await record(p.id, outcome, ms)
        return local

    results = await asyncio.gather(*[invoke(p) for p in providers], return_exceptions=True)
    for r in results:
        if isinstance(r, list):
            collected.extend(r)
    return collected


def _merge_and_rank(download_links: list[dict], stream_links: list[dict]) -> list[dict]:
    """
    Merge download provider links + stream-derived links.
    Strategy: prefer dedicated download provider links; fill missing quality
    slots from stream-resolved HLS. Dedup by quality label.
    """
    slots: dict[str, dict] = {}

    def _upsert(entry: dict, priority: int):
        key = entry["label"].lower()
        existing = slots.get(key)
        if existing is None:
            slots[key] = {**entry, "_priority": priority}
        elif priority > existing["_priority"]:
            slots[key] = {**entry, "_priority": priority}

    for e in download_links:
        _upsert(e, 2)
    for e in stream_links:
        _upsert(e, 1)

    ranked = sorted(slots.values(), key=lambda x: _qual_weight(x["label"]), reverse=True)
    for r in ranked:
        r.pop("_priority", None)
    return ranked


async def get_downloads(req, *, fresh: bool = False) -> dict:
    t0 = time.monotonic()
    key = download_key(req.tmdb_id, req.type, req.season, req.episode)

    if not fresh:
        cached = await cache_get(key)
        if cached:
            links = cached.get("links", [])
            app_ttl_s, cf_max_age_s = pick_best_ttl(links) if links else (0, 0)
            return {
                "ok":           True,
                "links":        links,
                "cached":       True,
                "took_ms":      int((time.monotonic() - t0) * 1000),
                "cache_ttl_ms": ttl_to_ms(app_ttl_s),
                "cf_max_age_s": cf_max_age_s,
            }

    meta = await get_content_kind(req.tmdb_id, req.type)
    data = LinkData(
        tmdb_id      = req.tmdb_id,
        type         = req.type,
        # getattr(req, "title", "") is always "" from the API routes — fall
        # back to the TMDB-resolved title. See ENGINE/manager/stream.py for
        # the full rationale (all subtitle scrapers depend on this).
        title        = getattr(req, "title", "") or meta["title"] or "",
        imdb_id      = getattr(req, "imdb_id", None),
        year         = getattr(req, "year", None) or meta["year"],
        season       = req.season,
        episode      = req.episode,
        is_anime     = meta["is_anime"],
        is_asian     = meta["is_asian"],
        is_bollywood = meta["is_bollywood"],
        org_title    = meta["org_title"],
    )

    download_links, stream_links = await asyncio.gather(
        _collect_from_download_providers(data),
        _collect_from_stream_providers(data),
    )

    links = _merge_and_rank(download_links, stream_links)

    app_ttl_s, cf_max_age_s = pick_best_ttl(links) if links else (0, 0)
    cache_ttl_ms = ttl_to_ms(app_ttl_s)

    if links and app_ttl_s > 0:
        await cache_set(key, {"links": links}, ttl=app_ttl_s)

    return {
        "ok":           bool(links),
        "links":        links,
        "cached":       False,
        "took_ms":      int((time.monotonic() - t0) * 1000),
        "error":        None if links else "No download links found",
        "cache_ttl_ms": cache_ttl_ms,
        "cf_max_age_s": cf_max_age_s,
    }
