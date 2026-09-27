"""
ENGINE/manager/subtitle.py — Subtitle manager.

Flow:
    1. Cache → return instantly if hit (1hr TTL)
    2. Fan-out to all subtitle providers concurrently
    3. Deduplicate by URL
    4. Cache + return
"""
from __future__ import annotations

import asyncio
import time

from ENGINE.cache.cache import get as cache_get, set as cache_set, subtitle_key
from ENGINE.manager.health import record, should_run
from ENGINE.providers.base import safe_run, TimedOut, LinkData
from ENGINE.providers.Subtitle.registry import get_all
from CATALOG.tmdb import get_content_kind
from config import get_settings

_s = get_settings()
_SUB_TTL = 3600  # subtitles cached 1 hour

# Subtitles are best-effort/optional — the player works fine without them.
# The app's HTTP client has a 30s read timeout on this endpoint, so the
# WHOLE fan-out (not just each provider) must return comfortably inside
# that window, or the app throws SocketTimeoutException client-side and
# shows "No connection" even though the server eventually replied 200.
# A single slow/CF-protected provider (e.g. YIFY via FlareSolverr) can
# otherwise use its full provider_timeout_ms (45s) and blow past that.
_SUBTITLE_FANOUT_BUDGET_S = 20


async def get_subtitles(req, *, fresh: bool = False) -> dict:
    t0 = time.monotonic()
    langs = req.languages or ["en"]
    key = subtitle_key(req.tmdb_id, req.type, req.season, req.episode, langs)

    if not fresh:
        cached = await cache_get(key)
        if cached:
            return {"ok": True, "subtitles": cached.get("subtitles", []),
                    "cached": True, "took_ms": int((time.monotonic() - t0) * 1000)}

    # title was previously hardcoded to "" here. ALL 4 subtitle providers
    # (R-201..R-204) search their site by title alone — an empty title means
    # every provider silently returns zero results on every single request,
    # regardless of provider health. Resolve it from TMDB, same as
    # ENGINE/manager/stream.py and ENGINE/manager/download.py do.
    meta = await get_content_kind(req.tmdb_id, req.type)
    data = LinkData(
        tmdb_id=req.tmdb_id, type=req.type, title=getattr(req, "title", "") or meta["title"] or "",
        imdb_id=req.imdb_id, season=req.season, episode=req.episode,
    )
    # Attach duration_ms hint so providers can fingerprint the exact file
    data.duration_ms = getattr(req, "duration_ms", 0)  # type: ignore[attr-defined]

    providers = [p for p in get_all() if await should_run(p.id)]
    subs = []
    seen: set[str] = set()

    async def invoke(p):
        t_start = time.monotonic()
        result = await safe_run(p, data, _s.provider_timeout_ms)
        ms = int((time.monotonic() - t_start) * 1000)
        local = []
        for sub in result.subtitles:
            if not sub.url or sub.url in seen:
                continue
            seen.add(sub.url)
            # Infer format from URL extension if provider didn't set it
            fmt = getattr(sub, "format", "") or ""
            if not fmt:
                for ext in ("vtt", "srt", "ass", "ssa", "sub", "sbv", "lrc"):
                    if f".{ext}" in sub.url.lower():
                        fmt = ext
                        break
                else:
                    fmt = "srt"
            local.append({
                "provider":        p.name,
                "language":        sub.language,
                "label":           sub.label or p.name,
                "url":             sub.url,
                "format":          fmt,
                # Fetch headers — whatever the provider says the CDN needs.
                "playback_headers": sub.playback_headers,
            })
        outcome = "found" if local else "failed" if isinstance(result, TimedOut) else "empty"
        await record(p.id, outcome, ms)
        return local

    tasks = {asyncio.ensure_future(invoke(p)): p for p in providers}
    if tasks:
        # Collect whatever finishes inside the budget; don't wait for stragglers.
        # Unlike wait_for(gather(...)), this keeps results from any task that
        # completes before the deadline even if others are still running —
        # and cancels only what's left outstanding once the budget is hit.
        done, pending = await asyncio.wait(tasks.keys(), timeout=_SUBTITLE_FANOUT_BUDGET_S)
        for t in pending:
            t.cancel()
            # A cutoff still counts against the provider's health score —
            # otherwise a chronically-slow provider never trips its circuit
            # breaker (its own record() call inside invoke() never runs
            # once cancelled) and keeps getting tried, and cut off, forever.
            await record(tasks[t].id, "failed", _SUBTITLE_FANOUT_BUDGET_S * 1000)
        results = [t.result() for t in done if not t.cancelled() and t.exception() is None]
    else:
        results = []
    for r in results:
        if isinstance(r, list):
            subs.extend(r)

    if subs:
        await cache_set(key, {"subtitles": subs}, ttl=_SUB_TTL)

    return {
        "ok": bool(subs),
        "subtitles": subs,
        "cached": False,
        "took_ms": int((time.monotonic() - t0) * 1000),
    }
