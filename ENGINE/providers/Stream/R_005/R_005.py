"""
ENGINE/providers/Stream/R-005/R_005.py — AllMovieLand

Direct JSON API via allmovieland.io.
GET /api/v2/embed?tmdb=<id>&type=movie|tv&season=&episode=
Returns { link, sources:[{file,label}] }
Ported from Streamplay's AllMovielandProvider.
"""
from __future__ import annotations

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA

_API = "https://allmovieland.io"


class R005Provider(Provider):
    id = "R-005"
    name = "AllMovieLand"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            if data.season is None:
                url = f"{_API}/api/v2/embed?tmdb={data.tmdb_id}&type=movie"
            else:
                url = (
                    f"{_API}/api/v2/embed?tmdb={data.tmdb_id}"
                    f"&type=tv&season={data.season}&episode={data.episode}"
                )

            client = await get_client()
            headers = {"User-Agent": UA, "Referer": f"{_API}/"}
            res = await client.get(url, headers=headers, timeout=15)
            if res.status_code >= 400:
                return result

            j = res.json()
            sources = j.get("sources") or []
            if not sources and j.get("link"):
                # Fallback: single link
                link = j["link"]
                result.streams.append(Stream(
                    url=link,
                    type="m3u8" if ".m3u8" in link else "iframe",
                    server="R-005 AllMovieLand",
                    headers={"Referer": f"{_API}/"},
                    playback_headers={"Referer": f"{_API}/"},
                ))
                return result

            for src in sources if isinstance(sources, list) else []:
                file_url = src.get("file") or src.get("url", "")
                if not file_url:
                    continue
                result.streams.append(Stream(
                    url=file_url,
                    type="m3u8" if ".m3u8" in file_url else "mp4",
                    server="R-005 AllMovieLand",
                    quality=src.get("label"),
                    headers={"Referer": f"{_API}/"},
                    playback_headers={"Referer": f"{_API}/"},
                ))
        except Exception:
            pass
        return result
