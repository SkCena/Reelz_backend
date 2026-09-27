"""
ENGINE/providers/Stream/R-007/R_007.py — VaplayerV2

GET /api.php?tmdb=<id>&type=movie|tv&season=&episode=
Returns { data: { stream_urls: [...] }, default_subs: [...] }
Ported from Streamplay's VaplayerProvider.
"""
from __future__ import annotations

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA

_API = "https://streamdata.vaplayer.ru"
_REFERER = "https://nextgencloudfabric.com/"


class R007Provider(Provider):
    id = "R-007"
    name = "VaplayerV2"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            if data.season is None:
                url = f"{_API}/api.php?tmdb={data.tmdb_id}&type=movie"
            else:
                url = (
                    f"{_API}/api.php?tmdb={data.tmdb_id}"
                    f"&type=tv&season={data.season}&episode={data.episode}"
                )

            client = await get_client()
            headers = {"User-Agent": UA, "Referer": _REFERER}
            j = (await client.get(url, headers=headers, timeout=15)).json()

            stream_urls = (j.get("data") or {}).get("stream_urls") if isinstance(j, dict) else None
            if not stream_urls:
                return result

            for idx, stream_url in enumerate(stream_urls):
                if not stream_url:
                    continue
                result.streams.append(Stream(
                    url=stream_url,
                    type="m3u8" if ".m3u8" in stream_url else "mp4",
                    server=f"R-007 VaplayerV2 Server {idx + 1}",
                    headers={"Referer": _REFERER},
                    playback_headers={"Referer": _REFERER},
                ))
        except Exception:
            pass
        return result
