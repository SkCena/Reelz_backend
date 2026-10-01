"""
ENGINE/providers/Stream/R-033/R-033.py — VixSrc

Type: m3u8
Flow:
  1. GET https://vixsrc.to/api/movie/{tmdb} or /api/tv/{tmdb}/{s}/{e} -> {src: "/embed/<id>?token=..&expires=..&lang=.."}
  2. GET https://vixsrc.to{src} -> HTML, regex window.masterPlaylist {token, expires, url}
  3. Master playlist = {url}?token=..&expires=..&h=1
"""
from __future__ import annotations

import re

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA


class R033Provider(Provider):
    id = "R-033"
    name = "VixSrc"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        if not data.tmdb_id:
            return result

        try:
            client = await get_client()
            is_tv = data.type == "tv"

            if is_tv and data.season and data.episode:
                api_url = f"https://vixsrc.to/api/tv/{data.tmdb_id}/{data.season}/{data.episode}"
            else:
                api_url = f"https://vixsrc.to/api/movie/{data.tmdb_id}"

            r = await client.get(
                api_url,
                headers={"Referer": "https://vixsrc.to/", "User-Agent": UA},
                timeout=20,
            )
            if r.status_code >= 400:
                return result

            try:
                body = r.json()
            except Exception:
                return result

            src = body.get("src")
            if not src:
                return result

            embed_url = f"https://vixsrc.to{src}"
            r2 = await client.get(
                embed_url,
                headers={"Referer": "https://vixsrc.to/", "User-Agent": UA},
                timeout=20,
            )
            if r2.status_code >= 400:
                return result

            html = r2.text

            # window.masterPlaylist = { params: { 'token': '...', 'expires': '...', ... }, url: '...' }
            # Grab a chunk after window.masterPlaylist and parse the fields
            token = None
            expires = None
            url = None

            idx = html.find("window.masterPlaylist")
            if idx >= 0:
                chunk = html[idx:idx + 800]
                mt = re.search(r"'token'\s*:\s*'([^']+)'", chunk)
                me = re.search(r"'expires'\s*:\s*'([^']+)'", chunk)
                mu = re.search(r"url\s*:\s*'([^']+)'", chunk)
                if mt:
                    token = mt.group(1)
                if me:
                    expires = me.group(1)
                if mu:
                    url = mu.group(1)

            if not url:
                return result

            playlist = url
            if token and expires:
                sep = "&" if "?" in url else "?"
                playlist = f"{url}{sep}token={token}&expires={expires}&h=1"

            result.streams.append(Stream(
                url=playlist,
                type="m3u8",
                server="R-033 VixSrc",
                quality="Auto",
            ))
        except Exception:
            pass
        return result
