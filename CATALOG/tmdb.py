"""
CATALOG/tmdb.py — TMDB API client used exclusively by the CATALOG layer.

Schema v3 MediaCard:
  { id, title, poster_url, rating, media_type }
  — no backdrop_url, release_year, genres, language, section_tag

Schema v3 Detail adds maturity_rating, seasons only need season_number.
Schema v3 Episode: id, episode_number, season_number, name, overview, still_url, runtime.

get_content_kind() is the single authoritative TMDB enrichment function used
by ENGINE managers to detect anime / asian / bollywood content.
Providers NEVER call TMDB directly — only managers do, through this function.
"""
from __future__ import annotations

from typing import Any, Optional

from ENGINE.cache.cache import get as cache_get, set as cache_set
from ENGINE.tools.http import get_client, UA
from config import get_settings

_s = get_settings()

# ── Image URL helpers ─────────────────────────────────────────────────────────

def _img(path: Optional[str], size: str) -> Optional[str]:
    if not path:
        return None
    return f"{_s.tmdb_image_base}/{size}{path}"

def poster(path: Optional[str]) -> Optional[str]:
    return _img(path, _s.tmdb_poster_size)

def backdrop(path: Optional[str]) -> Optional[str]:
    return _img(path, _s.tmdb_backdrop_size)

def still(path: Optional[str]) -> Optional[str]:
    return _img(path, _s.tmdb_still_size)

def profile(path: Optional[str]) -> Optional[str]:
    return _img(path, _s.tmdb_profile_size)


# ── Low-level fetcher ─────────────────────────────────────────────────────────

async def _get(path: str, params: dict | None = None) -> Optional[dict]:
    if not _s.tmdb_api_key:
        return None
    p   = {"api_key": _s.tmdb_api_key, **(params or {})}
    url = f"{_s.tmdb_base_url}{path}"
    try:
        client = await get_client()
        r = await client.get(url, params=p, headers={"User-Agent": UA}, timeout=10)
        if r.status_code >= 400:
            return None
        return r.json()
    except Exception:
        return None


async def _cached_get(key: str, path: str, params: dict | None = None, ttl: int = 3600) -> Optional[dict]:
    hit = await cache_get(key)
    if hit is not None:
        return hit
    data = await _get(path, params)
    if data:
        await cache_set(key, data, ttl=ttl)
    return data


# ── Genre lookup ──────────────────────────────────────────────────────────────

async def get_genres(media_type: str = "movie") -> list[dict]:
    key   = f"tmdb:genres:{media_type}"
    cached = await cache_get(key)
    if cached:
        return cached
    mtype = "movie" if media_type == "movie" else "tv"
    data  = await _get(f"/genre/{mtype}/list")
    genres = data.get("genres", []) if data else []
    if genres:
        await cache_set(key, genres, ttl=86_400)
    return genres


# ── Search ────────────────────────────────────────────────────────────────────

async def search_multi(query: str, page: int = 1) -> dict:
    key    = f"tmdb:search:multi:{query}:{page}"
    cached = await cache_get(key)
    if cached:
        return cached
    data   = await _get("/search/multi", {"query": query, "page": page, "include_adult": "false"})
    result = data or {"results": [], "total_pages": 0, "total_results": 0}
    await cache_set(key, result, ttl=300)
    return result


async def search_typed(query: str, media_type: str, page: int = 1) -> dict:
    mtype  = "movie" if media_type == "movie" else "tv"
    key    = f"tmdb:search:{mtype}:{query}:{page}"
    cached = await cache_get(key)
    if cached:
        return cached
    data   = await _get(f"/search/{mtype}", {"query": query, "page": page, "include_adult": "false"})
    result = data or {"results": [], "total_pages": 0, "total_results": 0}
    await cache_set(key, result, ttl=300)
    return result


# ── Discover ──────────────────────────────────────────────────────────────────

_SORT_MAP = {
    "popularity": "popularity.desc",
    "rating":     "vote_average.desc",
    "newest":     "primary_release_date.desc",
}

# TMDB watch provider IDs (India region)
OTT_PROVIDERS = {
    "netflix":   "8",
    "prime":     "119",
    "hotstar":   "122",
    "sonyliv":   "237",
    "zee5":      "232",
    "appletv":   "350",
}

async def discover(
    media_type: str = "movie",
    genre_id: Optional[str] = None,
    sort_by: str = "popularity",
    page: int = 1,
    language: Optional[str] = None,
    provider: Optional[str] = None,
    **_kwargs,
) -> dict:
    mtype     = "movie" if media_type == "movie" else "tv"
    tmdb_sort = _SORT_MAP.get(sort_by, "popularity.desc")
    params: dict[str, Any] = {
        "sort_by":        tmdb_sort,
        "page":           page,
        "vote_count.gte": 50,
        "include_adult":  "false",
    }
    # For "newest": only already-released titles, not future/unannounced ones
    if sort_by == "newest":
        from datetime import date, timedelta
        today = date.today().isoformat()
        # last 12 months of releases
        year_ago = (date.today() - timedelta(days=365)).isoformat()
        if mtype == "movie":
            params["primary_release_date.lte"] = today
            params["primary_release_date.gte"] = year_ago
        else:
            params["first_air_date.lte"] = today
            params["first_air_date.gte"] = year_ago
    if genre_id:
        params["with_genres"] = genre_id
    if language:
        params["with_original_language"] = language
    if provider:
        prov_id = OTT_PROVIDERS.get(provider.lower())
        if prov_id:
            params["with_watch_providers"] = prov_id
            params["watch_region"] = "IN"

    cache_key = f"tmdb:discover:{mtype}:{sort_by}:{genre_id}:{page}:{language}:{provider}"
    cached    = await cache_get(cache_key)
    if cached:
        return cached
    data   = await _get(f"/discover/{mtype}", params)
    result = data or {"results": [], "total_pages": 0}
    await cache_set(cache_key, result, ttl=1800)
    return result


# ── Detail ────────────────────────────────────────────────────────────────────

async def get_movie_detail(tmdb_id: int) -> Optional[dict]:
    key = f"tmdb:detail:movie:{tmdb_id}"
    return await _cached_get(
        key,
        f"/movie/{tmdb_id}",
        {"append_to_response": "credits,videos,similar,release_dates"},
        ttl=86_400,
    )


async def get_tv_detail(tmdb_id: int) -> Optional[dict]:
    key = f"tmdb:detail:tv:{tmdb_id}"
    return await _cached_get(
        key,
        f"/tv/{tmdb_id}",
        {"append_to_response": "credits,videos,similar,content_ratings"},
        ttl=86_400,
    )


async def get_season_detail(tmdb_id: int, season_number: int) -> Optional[dict]:
    key = f"tmdb:season:{tmdb_id}:{season_number}"
    return await _cached_get(key, f"/tv/{tmdb_id}/season/{season_number}", ttl=86_400)


async def get_external_ids(tmdb_id: int, media_type: str = "movie") -> dict:
    """Get external IDs (IMDB, etc.) for a TMDB item. Cached for 7 days."""
    path = f"/{'tv' if media_type == 'tv' else 'movie'}/{tmdb_id}/external_ids"
    key = f"tmdb:external:{media_type}:{tmdb_id}"
    result = await _cached_get(key, path, ttl=604_800)
    return result or {}


# ── Curated list fetchers (for feed) ─────────────────────────────────────────

async def _list(path: str, page: int = 1, ttl: int = 3600) -> list[dict]:
    key    = f"tmdb:list:{path}:{page}"
    cached = await cache_get(key)
    if cached is not None:
        return cached
    data  = await _get(path, {"page": page})
    items = (data or {}).get("results", [])
    if items:
        await cache_set(key, items, ttl=ttl)
    return items


async def trending_movies(page: int = 1) -> list[dict]:
    return await _list("/trending/movie/week", page)

async def trending_tv(page: int = 1) -> list[dict]:
    return await _list("/trending/tv/week", page)

async def popular_movies(page: int = 1) -> list[dict]:
    return await _list("/movie/popular", page)

async def popular_tv(page: int = 1) -> list[dict]:
    return await _list("/tv/popular", page)

async def top_rated_movies(page: int = 1) -> list[dict]:
    return await _list("/movie/top_rated", page)

async def top_rated_tv(page: int = 1) -> list[dict]:
    return await _list("/tv/top_rated", page)

async def now_playing_movies(page: int = 1) -> list[dict]:
    return await _list("/movie/now_playing", page)

async def on_the_air_tv(page: int = 1) -> list[dict]:
    return await _list("/tv/on_the_air", page)

async def upcoming_movies(page: int = 1) -> list[dict]:
    return await _list("/movie/upcoming", page)

async def bollywood_movies(page: int = 1) -> list[dict]:
    key    = f"tmdb:list:bollywood:{page}"
    cached = await cache_get(key)
    if cached is not None:
        return cached
    data  = await _get("/discover/movie", {"with_original_language": "hi", "sort_by": "popularity.desc", "vote_count.gte": 30, "page": page})
    items = (data or {}).get("results", [])
    if items:
        await cache_set(key, items, ttl=3600)
    return items

async def anime_tv(page: int = 1) -> list[dict]:
    key    = f"tmdb:list:anime:{page}"
    cached = await cache_get(key)
    if cached is not None:
        return cached
    data  = await _get("/discover/tv", {"with_genres": "16", "with_original_language": "ja", "sort_by": "popularity.desc", "page": page})
    items = (data or {}).get("results", [])
    if items:
        await cache_set(key, items, ttl=3600)
    return items

async def kdrama(page: int = 1) -> list[dict]:
    key    = f"tmdb:list:kdrama:{page}"
    cached = await cache_get(key)
    if cached is not None:
        return cached
    data  = await _get("/discover/tv", {"with_original_language": "ko", "sort_by": "popularity.desc", "vote_count.gte": 20, "page": page})
    items = (data or {}).get("results", [])
    if items:
        await cache_set(key, items, ttl=3600)
    return items


# ── Shape normalizers ─────────────────────────────────────────────────────────

def _year(d: dict, is_movie: bool) -> Optional[str]:
    date = d.get("release_date") if is_movie else d.get("first_air_date")
    return str(date)[:4] if date else None


def normalise_card(d: dict, media_type: str) -> dict:
    """
    Convert a raw TMDB result to schema v3 MediaCard wire shape.
    """
    rd = d.get("release_date") or d.get("first_air_date") or ""
    return {
        "id":         f"{media_type}:{d['id']}",
        "title":      d.get("title") or d.get("name") or "",
        "poster_url": poster(d.get("poster_path")),
        "rating":     round(d.get("vote_average", 0.0), 1),
        "media_type": media_type,
        "release_date": rd,
        "year":       rd[:4] if rd else "",
    }


def _maturity_rating_movie(d: dict) -> Optional[str]:
    """Extract US rating from release_dates for movies."""
    release_dates = d.get("release_dates", {}).get("results", [])
    for entry in release_dates:
        if entry.get("iso_3166_1") == "US":
            for rd in entry.get("release_dates", []):
                cert = rd.get("certification", "")
                if cert:
                    return cert
    return None


def _maturity_rating_tv(d: dict) -> Optional[str]:
    """Extract US rating from content_ratings for TV."""
    content_ratings = d.get("content_ratings", {}).get("results", [])
    for entry in content_ratings:
        if entry.get("iso_3166_1") == "US":
            return entry.get("rating") or None
    return None


def normalise_detail(d: dict, media_type: str) -> dict:
    """Full detail dict — schema v3 shape."""
    is_movie = media_type == "movie"
    tmdb_id  = d["id"]

    cast_raw = d.get("credits", {}).get("cast", [])[:20]
    cast = [
        {
            "name":      c["name"],
            "character": c.get("character", ""),
            "photo_url": profile(c.get("profile_path")),
        }
        for c in cast_raw
    ]

    videos  = d.get("videos", {}).get("results", [])
    trailer = next(
        (f"https://www.youtube.com/watch?v={v['key']}"
         for v in videos
         if v.get("site") == "YouTube" and v.get("type") == "Trailer"),
        None,
    )

    similar_raw = d.get("similar", {}).get("results", [])[:12]
    similar     = [normalise_card(s, media_type) for s in similar_raw]

    seasons = []
    for s in d.get("seasons", []):
        if s.get("season_number", 0) == 0:
            continue
        seasons.append({"season_number": s["season_number"]})

    genres = [g["name"] for g in d.get("genres", [])]

    maturity_rating = _maturity_rating_movie(d) if is_movie else _maturity_rating_tv(d)

    return {
        "id":              f"{media_type}:{tmdb_id}",
        "title":           d.get("title") or d.get("name") or "",
        "tagline":         d.get("tagline") or None,
        "overview":        d.get("overview", ""),
        "poster_url":      poster(d.get("poster_path")),
        "backdrop_url":    backdrop(d.get("backdrop_path")),
        "release_year":    _year(d, is_movie),
        "rating":          round(d.get("vote_average", 0.0), 1),
        "runtime":         d.get("runtime") or (d.get("episode_run_time") or [None])[0],
        "media_type":      media_type,
        "maturity_rating": maturity_rating,
        "genres":          genres,
        "status":          d.get("status"),
        "trailer_url":     trailer,
        "cast":            cast,
        "seasons":         seasons,
        "similar":         similar,
        "cache_ttl_ms":    3_600_000,
    }


def normalise_episode(ep: dict, season_number: int) -> dict:
    """Schema v3 episode shape: id, episode_number, season_number, name, overview, still_url, runtime."""
    return {
        "id":             f"ep:{ep.get('id', '')}",
        "episode_number": ep.get("episode_number", 0),
        "season_number":  season_number,
        "name":           ep.get("name", ""),
        "overview":       ep.get("overview", ""),
        "still_url":      still(ep.get("still_path")),
        "runtime":        ep.get("runtime"),
    }


# ── Content kind enrichment (used by ENGINE managers only) ───────────────────

_ANIME_KEYWORDS = {
    "anime", "manga adaptation", "based on manga",
    "based on light novel", "shounen", "shōnen", "seinen", "isekai",
}
_ASIAN_COUNTRIES = {"KR", "CN", "TW", "HK", "TH", "VN"}


def _detect_kind(media_type: str, countries: list, genre_ids: list, keywords: list) -> str:
    is_animation = 16 in genre_ids
    kw = {k.lower() for k in keywords}
    if ("JP" in countries and is_animation) or (kw & _ANIME_KEYWORDS):
        return "anime"
    if set(countries) & _ASIAN_COUNTRIES:
        return "asian"
    if "IN" in countries:
        return "bollywood"
    return media_type


async def get_content_kind(tmdb_id: int, media_type: str) -> dict:
    """
    Return content-kind metadata for ENGINE managers:
        { kind, is_anime, is_asian, is_bollywood, org_title, title, year }

    Results are cached 24 h independently so they survive stream cache expiry.
    Falls back to neutral defaults when TMDB key is absent or the request fails.

    Rules:
      - ENGINE managers call this; providers NEVER do.
      - This is the ONLY place in the codebase that fetches TMDB for kind detection.

    Note: `title` here is the display title used to build LinkData for ALL
    ENGINE providers (stream, download, subtitle). Several providers —
    all 4 subtitle scrapers among them — search by title only and silently
    return nothing if it's empty. Never drop this field.
    """
    default = {
        "kind":         media_type,
        "is_anime":     False,
        "is_asian":     False,
        "is_bollywood": False,
        "org_title":    None,
        "title":        None,
        "year":         None,
    }

    if not _s.tmdb_api_key:
        return default

    cache_key = f"tmdb:meta:{media_type}:{tmdb_id}"
    cached = await cache_get(cache_key)
    if cached and cached.get("title"):
        # Guard against stale cache entries written before `title`/`year`
        # were added to this payload — fall through to a fresh fetch
        # instead of silently returning a title-less result for up to 24h.
        return cached

    mtype = "movie" if media_type == "movie" else "tv"
    data = await _get(
        f"/{mtype}/{tmdb_id}",
        {"append_to_response": "keywords"},
    )
    if not data:
        return default

    countries = data.get("origin_country") or [
        c.get("iso_3166_1", "") for c in data.get("production_countries", [])
    ]
    genre_ids = [g["id"] for g in data.get("genres", []) if "id" in g]
    kw_block  = data.get("keywords", {})
    kw_list   = kw_block.get("keywords") or kw_block.get("results", [])
    keywords  = [k.get("name", "") for k in kw_list]

    kind      = _detect_kind(media_type, countries, genre_ids, keywords)
    org_title = data.get("original_title") or data.get("original_name")
    title     = data.get("title") or data.get("name") or org_title
    date      = data.get("release_date") if media_type == "movie" else data.get("first_air_date")
    year      = int(str(date)[:4]) if date and str(date)[:4].isdigit() else None

    meta = {
        "kind":         kind,
        "is_anime":     kind == "anime",
        "is_asian":     kind == "asian",
        "is_bollywood": kind == "bollywood",
        "org_title":    org_title,
        "title":        title,
        "year":         year,
    }
    await cache_set(cache_key, meta, ttl=86_400)
    return meta
