"""
ENGINE/manager/shorts.py — Shorts manager.

Trailers/clips. Cached 24 hours — they never change.
Paginated: page 1 = first 20, page 2 = next 20, etc.
"""
from __future__ import annotations

import asyncio
import time

from ENGINE.cache.cache import get as cache_get, set as cache_set, shorts_key
from ENGINE.cache.ttl_policy import get_provider_ttl, ttl_to_ms
from ENGINE.manager.health import record, should_run
from ENGINE.providers.base import safe_run, TimedOut, LinkData
from ENGINE.providers.Shorts.registry import get_all
from config import get_settings

_s = get_settings()


async def get_shorts(*, tmdb_id: int, media_type: str, page: int = 1, fresh: bool = False) -> dict:
    t0 = time.monotonic()
    key = shorts_key(tmdb_id, media_type, page)

    if not fresh:
        cached = await cache_get(key)
        if cached:
            items = cached.get("shorts", [])
            provider_ids = list({s.get("provider_id", "") for s in items if s.get("provider_id")})
            if provider_ids:
                best_app_s = min(get_provider_ttl(pid)[0] for pid in provider_ids)
                best_cf_s  = min(get_provider_ttl(pid)[1] for pid in provider_ids)
            else:
                best_app_s, best_cf_s = 86400, 72000
            return {
                "ok":           True,
                "shorts":       items,
                "page":         page,
                "cached":       True,
                "took_ms":      int((time.monotonic() - t0) * 1000),
                "cache_ttl_ms": ttl_to_ms(best_app_s),
                "cf_max_age_s": best_cf_s,
            }

    data = LinkData(tmdb_id=tmdb_id, type=media_type, title="")
    providers = [p for p in get_all() if await should_run(p.id)]
    all_shorts = []

    async def invoke(p):
        t_start = time.monotonic()
        result = await safe_run(p, data, _s.provider_timeout_ms)
        ms = int((time.monotonic() - t_start) * 1000)
        local = []
        for s in result.shorts:
            if not s.url:
                continue
            local.append({
                "provider":         p.name,
                "provider_id":      p.id,
                "title":            s.title,
                "url":              s.url,
                "thumbnail":        s.thumbnail,
                # Playback headers — whatever the provider says the CDN needs.
                "playback_headers":  s.playback_headers,
            })
        outcome = "found" if local else "failed" if isinstance(result, TimedOut) else "empty"
        await record(p.id, outcome, ms)
        return local

    results = await asyncio.gather(*[invoke(p) for p in providers], return_exceptions=True)
    for r in results:
        if isinstance(r, list):
            all_shorts.extend(r)

    page_size = 20
    page_items = all_shorts[(page - 1) * page_size: page * page_size]

    # ── Compute TTL from per-provider policy ──────────────────────────────────
    # Shorts TTL is based on the weakest-link provider in the batch.
    # R-301 (TMDB/YouTube) = 86400s, R-302 (Archive.org) = 604800s — both very long.
    # Any unknown provider falls back to a conservative 1h.
    if page_items:
        provider_ids = list({s.get("provider_id", "") for s in page_items if s.get("provider_id")})
        if provider_ids:
            best_app_s = min(get_provider_ttl(pid)[0] for pid in provider_ids)
            best_cf_s  = min(get_provider_ttl(pid)[1] for pid in provider_ids)
        else:
            best_app_s, best_cf_s = 86400, 72000  # 24h default for trailers
        await cache_set(key, {"shorts": page_items}, ttl=best_app_s)
    else:
        best_app_s, best_cf_s = 0, 0

    return {
        "ok":           bool(page_items),
        "shorts":       page_items,
        "page":         page,
        "cached":       False,
        "took_ms":      int((time.monotonic() - t0) * 1000),
        "cache_ttl_ms": ttl_to_ms(best_app_s),
        "cf_max_age_s": best_cf_s,
    }
