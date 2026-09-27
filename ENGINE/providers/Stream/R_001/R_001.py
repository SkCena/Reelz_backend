"""
ENGINE/providers/Stream/R-001/R-001.py — 2Embed

Type: iframe embed
Flow:
  1. Require imdb_id (2embed uses IMDB ids, not TMDB).
  2. Build embed URL from imdb_id.
  3. GET the page and extract the actual iframe src (data-src on #iframesrc).
     Falls back to returning the constructed embed URL directly as iframe if scrape fails.

Headers required:
  - Referer: https://www.2embed.cc/  (must be set on the GET request)
  - User-Agent: standard browser UA

Ported from Streamplay's TwoEmbedProvider.
"""
from __future__ import annotations

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA
from ENGINE.tools.scraper import parse


class R001Provider(Provider):
    id = "R-001"
    name = "2Embed"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        if not data.imdb_id:
            return result
        try:
            if data.season is None:
                embed_url = f"https://www.2embed.cc/embed/{data.imdb_id}"
            else:
                embed_url = (
                    f"https://www.2embed.cc/embedtv/{data.imdb_id}"
                    f"?s={data.season}&e={data.episode}"
                )

            client = await get_client()
            headers = {
                "User-Agent": UA,
                "Referer": "https://www.2embed.cc/",
            }
            res = await client.get(embed_url, headers=headers, timeout=15)
            if res.status_code >= 400:
                return result

            soup = parse(res.text)
            iframe_tag = soup.find("iframe", id="iframesrc")
            iframe_src = iframe_tag.get("data-src") if iframe_tag else None

            if iframe_src:
                result.streams.append(Stream(
                    url=iframe_src,
                    type="iframe",
                    server="R-001 2Embed",
                    playback_headers={},
                ))
            else:
                # Fallback: return the embed URL itself
                result.streams.append(Stream(
                    url=embed_url,
                    type="iframe",
                    server="R-001 2Embed",
                    playback_headers={},
                ))
        except Exception:
            pass
        return result
