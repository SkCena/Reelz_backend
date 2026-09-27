"""
ENGINE/providers/Download/R_102/R_102.py — VidEasy Download

Type: mp4 | hls
Produces download-ready MP4 links and HLS quality variants from VidEasy.

Flow:
  1. Fan out across VidEasy servers concurrently
  2. Decrypt response via enc-dec.app/dec-videasy
  3. MP4 sources → direct DownloadItem
  4. HLS sources → resolve_master → one DownloadItem per quality
"""
from __future__ import annotations

import asyncio
from urllib.parse import quote

from ENGINE.providers.base import Provider, LinkData, Result, DownloadItem
from ENGINE.tools.http import get_client, UA
from ENGINE.tools.encdec import enc_dec_post
from ENGINE.tools.hls import resolve_master

_API = "https://api.videasy.net"
_ORIGIN = "https://www.cineby.sc"

_SERVERS = [
    "myflixerzupcloud",
    "1movies",
    "moviebox",
    "primewire",
    "m4uhd",
    "hdmovie",
    "cdn",
    "primesrcme",
    "visioncine",
    "overflix",
    "superflix",
    "cuevana",
    "lamovie",
    "mb-flix",
]


class R102Provider(Provider):
    id = "R-102"
    name = "VidEasy Download"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        if not data.title or not data.tmdb_id:
            return result
        try:
            client = await get_client()
            headers = {
                "Accept": "*/*",
                "User-Agent": UA,
                "Origin": _ORIGIN,
                "Referer": f"{_ORIGIN}/",
            }
            enc_title = quote(quote(data.title))
            lock = asyncio.Lock()

            async def fetch_server(server: str) -> None:
                try:
                    if data.season is None:
                        url = (
                            f"{_API}/{server}/sources-with-title"
                            f"?title={enc_title}&mediaType=movie"
                            f"&year={data.year or ''}&tmdbId={data.tmdb_id}"
                            f"&imdbId={data.imdb_id or ''}"
                        )
                    else:
                        url = (
                            f"{_API}/{server}/sources-with-title"
                            f"?title={enc_title}&mediaType=tv"
                            f"&year={data.year or ''}&tmdbId={data.tmdb_id}"
                            f"&episodeId={data.episode}&seasonId={data.season}"
                            f"&imdbId={data.imdb_id or ''}"
                        )

                    res = await client.get(url, headers=headers, timeout=10)
                    enc_blob = res.text.strip()
                    if not enc_blob:
                        return

                    dec = await enc_dec_post("dec-videasy", {"text": enc_blob, "id": data.tmdb_id})
                    if not dec:
                        return

                    dec_result = dec.get("result") or {}
                    sources = dec_result.get("sources", [])

                    local: list[DownloadItem] = []

                    for src in (sources if isinstance(sources, list) else []):
                        link = src.get("url", "")
                        quality = src.get("quality") or ""
                        if not link:
                            continue

                        if ".mp4" in link or ".mkv" in link:
                            # Direct MP4/MKV download
                            lbl = str(quality) if quality else "1080p"
                            if not lbl.endswith("p") and lbl.isdigit():
                                lbl += "p"
                            local.append(DownloadItem(
                                url=link,
                                type="mp4",
                                quality=lbl,
                                language="English",
                                headers=dict(headers),
                                playback_headers={"Referer": f"{_ORIGIN}/"},
                            ))
                        elif ".m3u8" in link:
                            # Resolve HLS master → per-quality index.m3u8
                            variants = await resolve_master(link, headers=headers)
                            for v in variants:
                                local.append(DownloadItem(
                                    url=v["url"],
                                    type="hls",
                                    quality=v["quality"],
                                    language="English",
                                    headers=dict(headers),
                                    playback_headers={"Referer": f"{_ORIGIN}/"},
                                ))

                    async with lock:
                        result.downloads.extend(local)
                except Exception:
                    pass

            await asyncio.gather(*[fetch_server(s) for s in _SERVERS])
        except Exception:
            pass
        return result
