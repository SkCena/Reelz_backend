"""
ENGINE/providers/Shorts/R_303/R_303.py — iFunny (SeoCloud WeFeed) Shorts

Fixed from live network capture (2026-09-26):
  - User-Agent must be Android Chrome mobile UA (Windows UA gets different/empty response)
  - X-Request-Lang: en header is required
  - Sec-Fetch-Mode: cors and Sec-Fetch-Site: cross-site must be present
  - Response structure confirmed: media.video[0].url, media.cover.url
  - code=0 means success; items are under data.items
"""
from __future__ import annotations

import asyncio
import random

from ENGINE.providers.base import Provider, LinkData, Result, Short
from ENGINE.tools.http import get_client

# Exact UA from live Android network capture
_UA = (
    "Mozilla/5.0 (Linux; Android 10; K) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/139.0.0.0 Mobile Safari/537.36"
)

_BASE_URL = "https://api.seocloud.biz/wefeed-seo-bff/post/list-trending/group"
_ORIGIN   = "https://ifunny.club"
_REFERER  = "https://ifunny.club/"

_SEO_KEYS = [
    "lol-loop-GfRk3lGcil2",
    "meme-and-scream-yvTjtEYXgS4",
    "shortv-QIHhE5Hp1m5",
    "netflix-and-more-GHlH5Uah4Y6",
    "hot-girls-OjV29uXW1w4",
    "relaxing-central-GjIDo5NSwv5",
    "k-drama-club-IQF5pxWOoK9",
]

_PER_PAGE     = 20
_MAX_PAGE     = 5
_HTTP_TIMEOUT = 10
_TRY_KEYS     = 3

# Exact headers from live Android network capture
_HEADERS = {
    "User-Agent":        _UA,
    "Accept":            "application/json",
    "Accept-Encoding":   "gzip, deflate, br",
    "Accept-Language":   "en-US,en;q=0.9",
    "Origin":            _ORIGIN,
    "Referer":           _REFERER,
    "Sec-Fetch-Dest":    "empty",
    "Sec-Fetch-Mode":    "cors",
    "Sec-Fetch-Site":    "cross-site",
    "Sec-Ch-Ua":         '"Chromium";v="139", "Not;A=Brand";v="99"',
    "Sec-Ch-Ua-Mobile":  "?1",
    "Sec-Ch-Ua-Platform": '"Android"',
    "X-Client-Info":     '{"package_name":"movieboxbuzz","timezone":"Africa/Lagos"}',
    "X-Request-Lang":    "en",
}


class R303Provider(Provider):
    id   = "R-303"
    name = "iFunny WeFeed Shorts"

    async def run(self, data: LinkData) -> Result:  # noqa: ARG002
        result = Result()
        try:
            client = await get_client()
            keys   = random.sample(_SEO_KEYS, min(_TRY_KEYS, len(_SEO_KEYS)))
            page   = random.randint(1, _MAX_PAGE)

            async def fetch_key(seo_key: str) -> list[Short]:
                shorts = []
                try:
                    resp = await client.get(
                        _BASE_URL,
                        params={
                            "seoKey":  seo_key,
                            "page":    page,
                            "perPage": _PER_PAGE,
                        },
                        headers=_HEADERS,
                        timeout=_HTTP_TIMEOUT,
                    )
                    if resp.status_code >= 400:
                        return shorts

                    body = resp.json()
                    if body.get("code", -1) != 0:
                        return shorts

                    data_obj = body.get("data") or {}
                    items    = data_obj.get("items") or data_obj.get("list") or []
                    if not isinstance(items, list):
                        return shorts

                    for item in items:
                        if (item.get("mediaType") or "").upper() != "VIDEO":
                            continue

                        media     = item.get("media") or {}
                        videos    = media.get("video") or []
                        video_url = ""
                        if isinstance(videos, list) and videos:
                            video_url = (videos[0].get("url") or "").strip()

                        if not video_url or not video_url.startswith("http"):
                            continue

                        cover     = media.get("cover") or {}
                        thumb_url = (cover.get("url") or "").strip() or None

                        title = (
                            item.get("title")
                            or item.get("content")
                            or seo_key.split("-")[0].capitalize()
                        ).strip()
                        if len(title) > 120:
                            title = title[:117] + "…"

                        shorts.append(Short(
                            url       = video_url,
                            title     = title,
                            thumbnail = thumb_url,
                            referer   = _REFERER,
                            origin    = _ORIGIN,
                        ))
                except Exception:
                    pass
                return shorts

            all_results = await asyncio.gather(*[fetch_key(k) for k in keys])
            for batch in all_results:
                result.shorts.extend(batch)

        except Exception:
            pass

        return result
