"""
ENGINE/providers/Stream/R-010/R-010.py — PrimeVids (NineTV / moviesapi.club)

Type: m3u8 | iframe
Flow:
  1. GET moviesapi.club/<type>/<id> with Referer=pressplay.top -> scrape <iframe src>
  2. GET iframe URL with Referer=moviesapi.club/ -> search for direct m3u8/mp4
  3. If direct URL found, push as m3u8 stream.
  4. Fallback: return iframe itself as type=iframe.

Headers required:
  - Initial page GET: Referer: https://pressplay.top/
  - Iframe GET: Referer: https://moviesapi.club/
  - Playback (m3u8): Referer: <iframe_url>  (CDN checks iframe origin)
  - Playback (iframe): no special headers needed beyond what the player injects

Ported from Streamplay's NineTvProvider.
"""
from __future__ import annotations

import re

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA

_API = "https://moviesapi.club"
_REFERER = "https://pressplay.top/"


class R010Provider(Provider):
    id = "R-010"
    name = "PrimeVids"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            if data.season is None:
                url = f"{_API}/movie/{data.tmdb_id}"
            else:
                url = f"{_API}/tv/{data.tmdb_id}-{data.season}-{data.episode}"

            client = await get_client()
            headers = {"User-Agent": UA, "Referer": _REFERER}
            res = await client.get(url, headers=headers, timeout=15)
            if res.status_code != 200:
                return result

            # Extract iframe src
            iframe_m = re.search(r'<iframe[^>]+src=["\']([^"\']+)["\']', res.text, re.I)
            if not iframe_m:
                return result
            iframe = iframe_m.group(1)

            # Try to resolve a direct stream from the embed page
            try:
                embed_html = (await client.get(
                    iframe,
                    headers={"User-Agent": UA, "Referer": f"{_API}/"},
                )).text
                for pattern in [
                    r'(https?:[^"\'\\s]+\.m3u8[^"\'\\s]*)',
                    r'file\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
                ]:
                    m = re.search(pattern, embed_html, re.I)
                    if m:
                        link = m.group(1)
                        result.streams.append(Stream(
                            url=link,
                            type="m3u8",
                            server="R-010 PrimeVids",
                            # CDN validates that the request came from the iframe's origin
                            headers={"Referer": iframe},
                            playback_headers={"Referer": iframe},
                        ))
                        return result
            except Exception:
                pass

            # Fallback: return iframe for downstream player
            result.streams.append(Stream(
                url=iframe,
                type="iframe",
                server="R-010 PrimeVids",
                # Referer for the iframe fetch itself
                headers={"Referer": f"{_API}/"},
                playback_headers={},
            ))
        except Exception:
            pass
        return result
