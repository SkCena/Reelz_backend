"""
api/stream.py — Stream route.
cache_ttl_ms: computed per-provider by ENGINE/cache/ttl_policy.py
              Short for rotating-token providers (R_009: 5min), longer for stable CDNs (R_008: 4h).
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from api.auth import verify
from api.envelope import ok, err
from api.cache_headers import set_cache
from api.media_request import parse_tmdb_id, EngineRequest

router = APIRouter(prefix="/api/v1", tags=["Stream"])


class StreamRequestBody(BaseModel):
    id:      str = Field(...)
    type:    str = Field(...)
    season:  int = Field(0, ge=0)
    episode: int = Field(0, ge=0)


def _merge_headers(base: dict, referer, origin, user_agent) -> dict:
    """
    Merge referer/origin/user_agent into the existing headers dict.
    None values are skipped — only headers the provider actually declared are included.
    The existing headers dict is never mutated.
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
    Best-effort subtitle fetch for bundling into /stream responses.
    Mirrors api/subtitle.py's shaping so the app gets the same schema
    (url, language, label, format, enabled, headers) it expects inline
    on each stream track. Never raises — a subtitle provider failure
    must not break stream resolution.
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


@router.post("/stream")
async def resolve_stream(
    req: StreamRequestBody,
    response: Response,
    fresh: int = Query(0),
    warp: str = Query("off"),
    user_id: Optional[str] = Depends(verify),
):
    tmdb_id = parse_tmdb_id(req.id)

    # Enrich with IMDB ID from TMDB (for providers that need it internally).
    # System stays TMDB-based; this is just a lookup to help IMDB-dependent providers.
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

    from ENGINE.manager.stream import get_streams
    result = await get_streams(engine_req, fresh=bool(fresh), warp_mode=warp)

    raw_streams = result.get("streams", [])
    best = result.get("stream")
    if not best and raw_streams:
        best = raw_streams[0]

    streams = []
    seen = set()
    for s in raw_streams:
        url = s.get("url", "")
        if not url:
            continue
        # Don't guess language: if provider didn't set it, label by quality
        # instead of wrongly claiming "English" for Hindi audio.
        lang = (s.get("language") or "").strip()
        quality = (s.get("quality") or "").strip()
        if lang and lang.lower() not in ("unknown", "und"):
            name = lang
        elif quality:
            name = quality
        else:
            name = "Stream"
        if name in seen:
            # Make duplicate names unique with quality suffix
            if quality and quality not in name:
                name = f"{name} {quality}"
            if name in seen:
                continue
        seen.add(name)
        streams.append({
            "name":      name,
            "url":       url,
            "type":      "hls" if s.get("type") in ("m3u8", "hls") else "mp4",
            "headers":   _merge_headers(s.get("headers"), s.get("referer"), s.get("origin"), s.get("user_agent")),
            "subtitles": [],
        })

    if not streams and best:
        _blang = (best.get("language") or "").strip()
        _bqual = (best.get("quality") or "").strip()
        _bname = _blang if _blang and _blang.lower() not in ("unknown", "und") else (_bqual or "Stream")
        streams = [{
            "name":      _bname,
            "url":       best.get("url", ""),
            "type":      "hls" if best.get("type") in ("m3u8", "hls") else "mp4",
            "headers":   _merge_headers(best.get("headers"), best.get("referer"), best.get("origin"), best.get("user_agent")),
            "subtitles": [],
        }]

    if not streams:
        set_cache(response, None)
        return err("No streams available for this title")

    # Bundle subtitles into every stream track so the player has them
    # without a second round-trip to /subtitles. Best-effort: if the
    # subtitle providers fail or return nothing, streams still play fine.
    subs = await _fetch_subtitles(engine_req)
    if subs:
        for s in streams:
            s["subtitles"] = subs

    cache_ttl_ms = result.get("cache_ttl_ms") or None
    cf_max_age_s = result.get("cf_max_age_s") or None

    now_ms = int(time.time() * 1000)
    stream_expiries = [s.get("expires_at_ms") for s in raw_streams if s.get("expires_at_ms")]
    if stream_expiries:
        expires_at_ms = min(stream_expiries)
    elif cache_ttl_ms:
        expires_at_ms = now_ms + cache_ttl_ms
    else:
        expires_at_ms = now_ms + 3_600_000

    set_cache(response, cache_ttl_ms, cf_max_age_s=cf_max_age_s)
    return ok({
        "streams":       streams,
        "expires_at_ms": expires_at_ms,
    }, cache_ttl_ms=cache_ttl_ms)
