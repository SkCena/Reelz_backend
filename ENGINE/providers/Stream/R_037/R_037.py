"""
ENGINE/providers/Stream/R_037/R_037.py — VidZee (TMDB-keyed stream API)

Free, no auth, TMDB-keyed JSON API with Hindi dub support.

API:
  - Movie: GET https://core.vidzee.wtf/streams/movie/{tmdb_id}?s={server}&e=0
  - TV:    GET https://core.vidzee.wtf/streams/tv/{tmdb_id}/{s}/{e}?s={server}&e=0

Servers: dcloud, tik, ipcloud, v6:Hindi
  - e=0 returns plaintext JSON: {"url": "...m3u8", "language": "...", "headers": {}}
  - Requires Referer: https://player.vidzee.wtf/

Type: hls (m3u8)
"""
from __future__ import annotations

import asyncio

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client


VIDZEE_BASE = "https://core.vidzee.wtf"
VIDZEE_REFERER = "https://player.vidzee.wtf/"

# Servers to try in order; Hindi dub included
SERVERS = ["dcloud", "tik", "ipcloud", "v6:Hindi"]

SERVER_LABELS = {
    "dcloud": "English",
    "tik": "English",
    "ipcloud": "English",
    "v6:Hindi": "Hindi",
}


async def _fetch_server(client, tmdb_id: int, is_tv: bool, season: int, episode: int,
                        server: str) -> dict | None:
    """Fetch stream from one VidZee server. Returns dict or None."""
    try:
        if is_tv:
            url = f"{VIDZEE_BASE}/streams/tv/{tmdb_id}/{season}/{episode}?s={server}&e=0"
        else:
            url = f"{VIDZEE_BASE}/streams/movie/{tmdb_id}?s={server}&e=0"
        resp = await client.get(url, headers={"Referer": VIDZEE_REFERER}, timeout=12)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not data or data.get("error") or not data.get("url"):
            return None
        return data
    except Exception:
        return None


class R037Provider(Provider):
    id = "R-037"
    name = "VidZee"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            tmdb_id = data.tmdb_id
            if not tmdb_id:
                return result

            is_tv = data.type == "tv"
            season = data.season or 1
            episode = data.episode or 1

            client = await get_client()

            # Try all servers in parallel
            tasks = [
                _fetch_server(client, tmdb_id, is_tv, season, episode, srv)
                for srv in SERVERS
            ]
            responses = await asyncio.gather(*tasks)

            for srv, resp in zip(SERVERS, responses):
                if not resp:
                    continue
                url = resp.get("url", "")
                if not url:
                    continue
                lang = resp.get("language", "") or SERVER_LABELS.get(srv, "")
                # Normalize language label
                if "hindi" in lang.lower() or srv == "v6:Hindi":
                    lang_label = "Hindi"
                elif lang.lower() in ("auto", ""):
                    lang_label = SERVER_LABELS.get(srv, "English")
                else:
                    lang_label = lang

                headers = resp.get("headers") or {}
                # VidZee CDN needs the player referer
                playback_headers = {"Referer": VIDZEE_REFERER}
                playback_headers.update(headers)

                result.streams.append(Stream(
                    url=url,
                    type="hls",
                    quality="Auto",
                    language=lang_label,
                    server=f"R-037 VidZee [{srv}]",
                    playback_headers=playback_headers,
                ))

            return result
        except Exception:
            return result
