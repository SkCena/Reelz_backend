"""
api/discover.py — Discover + Genres.
cache_ttl_ms: 1_800_000 discover / 86_400_000 genres
"""
from __future__ import annotations
import base64, json
from typing import Optional
from fastapi import APIRouter, Depends, Query, Response
from api.auth import verify
from api.envelope import ok
from api.cache_headers import set_cache

router = APIRouter(prefix="/api/v1", tags=["Discover"])
_DISCOVER_TTL = 1_800_000
_GENRES_TTL   = 86_400_000


def _decode_page(cursor):
    if not cursor: return 1
    try: return json.loads(base64.urlsafe_b64decode(cursor)).get("page", 1)
    except: return 1

def _encode_cursor(page):
    return base64.urlsafe_b64encode(json.dumps({"page": page}).encode()).decode()


@router.get("/discover")
async def discover(
    response: Response,
    type: str = Query("movie"),
    genre: Optional[str] = Query(None),
    sort: str = Query("popularity"),
    cursor: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=50),
    language: Optional[str] = Query(None),
    user_id: Optional[str] = Depends(verify),
):
    from CATALOG.tmdb import discover as tmdb_discover, normalise_card
    page = _decode_page(cursor)
    raw  = await tmdb_discover(media_type=type, genre_id=genre, sort_by=sort, page=page, language=language)
    results  = raw.get("results", [])
    items    = [normalise_card(r, type) for r in results[:limit]]
    has_more = page < raw.get("total_pages", 1)
    set_cache(response, _DISCOVER_TTL)
    return ok({
        "items": items,
        "has_more": has_more,
        "next_cursor": _encode_cursor(page + 1) if has_more else None,
    }, cache_ttl_ms=_DISCOVER_TTL)


@router.get("/genres")
async def get_genres(
    response: Response,
    type: str = Query("movie"),
    user_id: Optional[str] = Depends(verify),
):
    from CATALOG.tmdb import get_genres
    genres = await get_genres(type)
    set_cache(response, _GENRES_TTL)
    return ok(
        {"genres": [{"id": str(g["id"]), "name": g["name"]} for g in genres]},
        cache_ttl_ms=_GENRES_TTL,
    )
