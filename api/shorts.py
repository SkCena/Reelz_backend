"""
api/shorts.py — Shorts.
cache_ttl_ms: computed per-provider by ENGINE/cache/ttl_policy.py
              R-301 (TMDB/YouTube): 24h. R-302 (Archive.org): 7 days.
"""
from __future__ import annotations
import base64, hashlib, json
from typing import Optional
from fastapi import APIRouter, Depends, Query, Response
from api.auth import verify
from api.envelope import ok
from api.cache_headers import set_cache

router = APIRouter(prefix="/api/v1", tags=["Shorts"])

def _decode_page(cursor):
    if not cursor: return 1
    try: return json.loads(base64.urlsafe_b64decode(cursor)).get("page", 1)
    except: return 1

def _encode_cursor(page):
    return base64.urlsafe_b64encode(json.dumps({"page": page}).encode()).decode()

def _make_id(url, idx):
    return hashlib.md5(f"{url}:{idx}".encode()).hexdigest()[:16]

def _merge_headers(base: dict, referer, origin, user_agent) -> dict:
    if not referer and not origin and not user_agent:
        return base or {}
    merged = dict(base or {})
    if referer:    merged["Referer"]    = referer
    if origin:     merged["Origin"]     = origin
    if user_agent: merged["User-Agent"] = user_agent
    return merged


@router.get("/shorts")
async def get_shorts(
    response: Response,
    cursor: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=50),
    fresh: int = Query(0),
    user_id: Optional[str] = Depends(verify),
):
    page = _decode_page(cursor)
    from ENGINE.manager.shorts import get_shorts as engine_shorts
    result = await engine_shorts(tmdb_id=0, media_type="movie", page=page, fresh=bool(fresh))
    raw = result.get("shorts", [])

    # Filter to valid URLs first, THEN slice — so has_more reflects the true
    # pool size, not the already-capped slice (Bug fix: was len(items) >= limit
    # which was always True when providers returned exactly `limit` items and
    # always False when they returned fewer, regardless of whether more existed).
    valid_raw = [s for s in raw if s.get("url")]
    page_raw  = valid_raw[:limit]
    has_more  = len(valid_raw) > limit

    items = [
        {
            "id":        _make_id(s.get("url", ""), i),
            "title":     s.get("title", ""),
            "source":    s.get("provider") or s.get("source") or "original",
            "url":       s.get("url", ""),
            "thumbnail": s.get("thumbnail") or None,
            "headers":   _merge_headers(s.get("headers"), s.get("referer"), s.get("origin"), s.get("user_agent")),
        }
        for i, s in enumerate(page_raw)
    ]

    cache_ttl_ms = result.get("cache_ttl_ms") or None
    cf_max_age_s = result.get("cf_max_age_s") or None

    set_cache(response, cache_ttl_ms, cf_max_age_s=cf_max_age_s)
    return ok({
        "items":       items,
        "has_more":    has_more,
        "next_cursor": _encode_cursor(page + 1) if has_more else None,
    }, cache_ttl_ms=cache_ttl_ms)
