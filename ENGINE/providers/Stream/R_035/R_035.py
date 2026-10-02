"""
ENGINE/providers/Stream/R_035/R_035.py — NetMirror (OTT aggregator)

Netflix, Prime Video, Disney+ Hotstar, and other OTT content via NetMirror API.
TMDB-keyed JSON API — no scraping, no crypto, no captcha.

API:
  - Embed: GET https://net27.cc/api/embed-tmdb/{tmdb_id}?type=movie
           GET https://net27.cc/api/embed-tmdb/{tmdb_id}?type=tv&se={s}&ep={e}
  - Variants: GET https://net27.cc/api/variants-tmdb/{type}/{tmdb_id}
              Returns dub variants (Hindi dub, Tamil dub, etc.)
  - Dubbed: GET /api/embed-tmdb/{id}?type=..&dub={dubSubjectId}&dubdp={detailPath}

Returns direct MP4 URLs with signed tokens (360p to 1080p).
Includes subtitle/caption URLs.

Type: mp4
"""
from __future__ import annotations

import asyncio

from ENGINE.providers.base import Provider, LinkData, Result, Stream, Subtitle
from ENGINE.tools.http import get_client


NETMIRROR_BASE = "https://net27.cc"
NETMIRROR_REFERER = "https://net27.cc/"

# Fallback domains (NetMirror rotates; CNCVerse uses net52.cc)
NETMIRROR_FALLBACKS = [
    "https://net52.cc",
    "https://net51.cc",
]

# Dub languages to include (keep the list focused)
WANTED_DUBS = {"hindi dub", "english dub", "tamil dub", "telugu dub"}

# Skip subtitle-only variants (original audio anyway)
SKIP_SUFFIX = " sub"


async def _fetch_json(client, url: str, timeout: int = 12):
    """Fetch JSON from NetMirror API. Tries fallback domains. Returns dict or None."""
    urls = [url]
    # Add fallback domain variants
    for fb in NETMIRROR_FALLBACKS:
        if NETMIRROR_BASE in url:
            urls.append(url.replace(NETMIRROR_BASE, fb))
    for u in urls:
        try:
            r = await client.get(
                u,
                headers={"Referer": NETMIRROR_REFERER},
                timeout=timeout,
            )
            if r.status_code == 200:
                return r.json()
        except Exception:
            continue
    return None


def _extract_streams(data: dict, label: str, result: Result):
    """Extract MP4 streams and subtitles from embed-tmdb response."""
    if not data or not data.get("ok"):
        return

    # Main MP4 (default quality)
    mp4_url = data.get("mp4")
    resolution = data.get("resolution", "")
    if mp4_url:
        result.streams.append(Stream(
            url=mp4_url,
            type="mp4",
            quality=f"{resolution}p" if resolution else "HD",
            language=label,
            server=f"R-035 NetMirror [{label}]",
            playback_headers={"Referer": NETMIRROR_REFERER},
        ))

    # Quality ladder
    for s in data.get("streams", []):
        url = s.get("url")
        res = s.get("resolution")
        if url and res:
            # Skip if same as main URL
            if url == mp4_url:
                continue
            result.streams.append(Stream(
                url=url,
                type="mp4",
                quality=f"{res}p",
                language=label,
                server=f"R-035 NetMirror [{label}]",
                playback_headers={"Referer": NETMIRROR_REFERER},
            ))

    # Subtitles/captions
    for cap in data.get("captions", []):
        lang = cap.get("lang", "")
        name = cap.get("name", lang)
        url = cap.get("url")
        if url and lang:
            # Proxy relative URLs through NetMirror
            if url.startswith("/"):
                url = NETMIRROR_BASE + url
            result.subtitles.append(Subtitle(
                url=url,
                language=lang,
                label=f"{name} [NetMirror]",
                format="srt",
                playback_headers={"Referer": NETMIRROR_REFERER},
            ))


class R035Provider(Provider):
    id = "R-035"
    name = "NetMirror OTT"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            tmdb_id = data.tmdb_id
            if not tmdb_id:
                return result

            is_tv = data.type == "tv"
            type_str = "tv" if is_tv else "movie"

            client = await get_client()

            # Build base query
            if is_tv:
                season = data.season or 1
                episode = data.episode or 1
                base_query = f"?type=tv&se={season}&ep={episode}"
            else:
                base_query = "?type=movie"

            # 1. Fetch default (original audio) embed
            default_url = f"{NETMIRROR_BASE}/api/embed-tmdb/{tmdb_id}{base_query}"
            default_data, variants_data = await asyncio.gather(
                _fetch_json(client, default_url),
                _fetch_json(client, f"{NETMIRROR_BASE}/api/variants-tmdb/{type_str}/{tmdb_id}"),
            )

            _extract_streams(default_data, "Original", result)

            # 2. Fetch wanted dub variants in parallel
            if variants_data:
                dub_tasks = []
                for v in variants_data.get("variants", []):
                    lang = (v.get("language") or "").lower()
                    # Skip subtitle-only and unwanted dubs
                    if lang.endswith(SKIP_SUFFIX):
                        continue
                    if lang not in WANTED_DUBS and lang != "default":
                        continue
                    if lang == "default":
                        continue  # Already fetched as default

                    dub_id = v.get("dubSubjectId")
                    detail_path = v.get("detailPath")
                    if dub_id and detail_path:
                        dub_url = (
                            f"{NETMIRROR_BASE}/api/embed-tmdb/{tmdb_id}"
                            f"{base_query}&dub={dub_id}&dubdp={detail_path}"
                        )
                        # Clean label: "Hindi dub" -> "Hindi"
                        label = v.get("language", "").replace(" dub", "").title()
                        dub_tasks.append((label, _fetch_json(client, dub_url)))

                # Run dub fetches in parallel
                if dub_tasks:
                    dub_results = await asyncio.gather(
                        *[task for _, task in dub_tasks],
                        return_exceptions=True,
                    )
                    for (label, _), dub_data in zip(dub_tasks, dub_results):
                        if isinstance(dub_data, dict):
                            _extract_streams(dub_data, label, result)

        except Exception:
            pass
        return result
