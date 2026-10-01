"""
api/download.py — Download route.
cache_ttl_ms: computed per-provider by ENGINE/cache/ttl_policy.py
"""
from __future__ import annotations

import re
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from api.auth import verify
from api.envelope import ok, err
from api.cache_headers import set_cache
from api.media_request import parse_tmdb_id, EngineRequest
from USERS.queries import is_premium_user

router = APIRouter(prefix="/api/v1", tags=["Download"])


class StreamRequestBody(BaseModel):
    id:      str = Field(...)
    type:    str = Field(...)
    season:  int = Field(0, ge=0)
    episode: int = Field(0, ge=0)


def _res_height(q: str) -> int:
    m = re.search(r"(\d{3,4})p", (q or "").lower())
    return int(m.group(1)) if m else 0


def _merge_headers(base: dict, referer, origin, user_agent) -> dict:
    """
    Merge referer/origin/user_agent into the existing headers dict.
    None values are skipped. Each download link carries its own headers
    since links may come from different providers.
    """
    if not referer and not origin and not user_agent:
        return base or {}
    merged = dict(base or {})
    if referer:    merged["Referer"]    = referer
    if origin:     merged["Origin"]     = origin
    if user_agent: merged["User-Agent"] = user_agent
    return merged


async def _fetch_subtitles(engine_req, default_lang: str = "en") -> list[dict]:
    """
    Best-effort subtitle fetch bundled into /download responses.
    Same shaping as api/subtitle.py. Never raises — a subtitle provider
    failure must not break download link resolution.
    """
    try:
        sub_req = EngineRequest(
            tmdb_id = engine_req.tmdb_id,
            type    = engine_req.type,
            season  = engine_req.season,
            episode = engine_req.episode,
        )
        sub_req.languages   = [default_lang]  # type: ignore[attr-defined]
        sub_req.duration_ms = 0               # type: ignore[attr-defined]

        from ENGINE.manager.subtitle import get_subtitles as engine_subtitles
        result = await engine_subtitles(sub_req, fresh=False)

        subs = []
        for s in result.get("subtitles", []):
            url = s.get("url", "")
            if not url:
                continue
            lang = s.get("language", "en")
            subs.append({
                "url":      url,
                "language": lang,
                "label":    s.get("label", ""),
                "format":   s.get("format", "srt"),
                "enabled":  lang == default_lang,
                "headers":  _merge_headers(s.get("headers"), s.get("referer"), s.get("origin"), s.get("user_agent")),
            })
        return subs
    except Exception:
        return []


@router.post("/download")
async def get_download_links(
    req: StreamRequestBody,
    response: Response,
    fresh: int = Query(0),
    user_id: Optional[str] = Depends(verify),
):
    from config import get_settings
    if not get_settings().downloads_enabled:
        raise HTTPException(status_code=403, detail="Downloads not available")

    tmdb_id = parse_tmdb_id(req.id)

    # Enrich with IMDB ID from TMDB (for providers that need it internally)
    imdb_id = None
    try:
        from CATALOG.tmdb import get_external_ids
        ext = await get_external_ids(tmdb_id, req.type)
        imdb_id = ext.get("imdb_id")
    except Exception:
        pass

    engine_req = EngineRequest(
        tmdb_id = tmdb_id,
        type    = req.type,
        imdb_id = imdb_id,
        season  = req.season or None,
        episode = req.episode or None,
    )

    is_premium = await is_premium_user(user_id)

    from ENGINE.manager.download import get_downloads
    result = await get_downloads(engine_req, fresh=bool(fresh))

    now_ms = int(time.time() * 1000)
    cache_ttl_ms_preview = result.get("cache_ttl_ms") or None
    default_link_expiry = now_ms + (cache_ttl_ms_preview or 3_600_000)

    links = []
    for link in result.get("links", []):
        url   = link.get("url", "")
        label = link.get("label", "").strip()
        if not url:
            continue
        if not label:
            import re as _re
            m = _re.search(r"(2160|1080|720|480|360|240)p?", url, _re.I)
            label = (m.group(1) + "p") if m else "1080p"
        res = _res_height(label)
        links.append({
            "label":        label,
            "type":         link.get("type") or "mp4",
            "url":          url,
            "language":     link.get("language") or "English",
            "size_bytes":   int(link.get("size_bytes") or 0),
            "premium":      res >= 1080 and not is_premium,
            # Each link carries its own headers — different links may come from
            # different providers with different CDN requirements.
            "headers":      _merge_headers(link.get("headers"), link.get("referer"), link.get("origin"), link.get("user_agent")),
            # Per-link expiry — falls back to the provider's own expiry if given,
            # else the computed default below. The app's download engine reads
            # this (not just the top-level expires_at_ms) to detect stale URLs
            # on resume, so it must be set on every link, not just at the root.
            "expires_at_ms": int(link.get("expires_at_ms") or default_link_expiry),
        })

    if not links:
        set_cache(response, None)
        return err("No download links available")

    cache_ttl_ms = result.get("cache_ttl_ms") or None
    cf_max_age_s = result.get("cf_max_age_s") or None

    # Top-level expires_at_ms mirrors the earliest per-link expiry, so the
    # two values (root metadata vs. per-link content) can never disagree.
    expires_at_ms = min(lnk["expires_at_ms"] for lnk in links)

    # Bundle subtitles alongside the download links (optional field —
    # app treats it as nullable). Best-effort: link resolution still
    # succeeds even if subtitle providers fail or return nothing.
    subs = await _fetch_subtitles(engine_req)

    set_cache(response, cache_ttl_ms, cf_max_age_s=cf_max_age_s)
    payload = {
        "links":         links,
        "expires_at_ms": expires_at_ms,
    }
    if subs:
        payload["subtitles"] = subs
    return ok(payload, cache_ttl_ms=cache_ttl_ms)
