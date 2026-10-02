"""
ENGINE/providers/Stream/R-040/R-040.py — AniZone (Anime, multi-audio)

Type: m3u8 (HLS)
Flow:
  1. GET https://anizone.to/anime?search={title} -> find anime page
  2. GET {anime_url}/{episode} -> <media-player src="..."> = HLS URL
  3. <track> elements = subtitles

Headers: Referer: https://anizone.to/

Multi-audio anime provider. Title-based search (no TMDB->MAL mapping needed).

Based on streamline's anime.js implementation (server-side, cheerio-based).
Verified live 2026-10-02.
"""
from __future__ import annotations

import re
from urllib.parse import quote

from ENGINE.providers.base import Provider, LinkData, Result, Stream, Subtitle
from ENGINE.tools.http import get_client, UA

_BASE = "https://anizone.to"
_REFERER = "https://anizone.to/"


class R040Provider(Provider):
    id = "R-040"
    name = "AniZone"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        if not data.title:
            return result
        try:
            client = await get_client()
            headers = {
                "User-Agent": UA,
                "Referer": _REFERER,
            }

            # Step 1: Search for anime by title
            search_url = f"{_BASE}/anime?search={quote(data.title)}"
            search_resp = await client.get(search_url, headers=headers, timeout=15)
            if search_resp.status_code >= 400:
                return result

            # Find anime page link: div.truncate > a href
            # Look for first result link
            html = search_resp.text
            # Pattern for anime links
            link_match = re.search(r'href="(/anime/[^"]+)"', html)
            if not link_match:
                return result

            anime_path = link_match.group(1)

            # Step 2: Get episode page
            # For movies, episode is 1. For TV, use episode number.
            ep = data.episode if data.episode else 1
            ep_url = f"{_BASE}{anime_path}/{ep}"
            ep_resp = await client.get(ep_url, headers=headers, timeout=15)
            if ep_resp.status_code >= 400:
                return result

            ep_html = ep_resp.text

            # Step 3: Extract HLS URL from <media-player src="...">
            player_match = re.search(r'<media-player[^>]+src="([^"]+)"', ep_html)
            if not player_match:
                # Try alternative pattern
                player_match = re.search(r'src="(https?://[^"]+\.m3u8[^"]*)"', ep_html)

            if player_match:
                stream_url = player_match.group(1)
                result.streams.append(Stream(
                    url=stream_url,
                    type="m3u8",
                    server="AniZone",
                    quality="",
                    language="Japanese",  # Default, may have multi-audio
                    playback_headers={"Referer": _REFERER},
                ))

            # Step 4: Extract subtitles from <track> elements
            for track_match in re.finditer(
                r'<track[^>]+src="([^"]+)"[^>]*srclang="([^"]*)"[^>]*label="([^"]*)"', ep_html
            ):
                sub_url, sub_lang, sub_label = track_match.groups()
                result.subtitles.append(Subtitle(
                    url=sub_url,
                    language=sub_lang or "en",
                    label=sub_label or sub_lang or "English",
                ))

        except Exception:
            pass

        return result
