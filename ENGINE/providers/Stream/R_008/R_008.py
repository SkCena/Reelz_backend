"""
ENGINE/providers/Stream/R-008/R_008.py — DahmerMovies

Browses an Apache-style directory listing and picks matching files.
Base URL: https://a.111477.xyz
Movie path: /movies/<Title> (<Year>)/
TV path:    /tvs/<Title>/Season <N>/
Ported from Streamplay's DahmerMoviesProvider.
"""
from __future__ import annotations

import re
from urllib.parse import quote

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA

_API = "https://a.111477.xyz"
_QUALITY_RE = re.compile(r"\d{3,4}[pP]\.?(.*?)\.(mkv|mp4|avi)", re.I)
_RESOLUTION_RE = re.compile(r"(1080p|2160p|720p|480p)", re.I)


def _pad2(n: int | None) -> str:
    if n is None:
        return "0"
    return f"0{n}" if n < 10 else str(n)


def _quality_tags(filename: str) -> str:
    m = _QUALITY_RE.search(filename or "")
    return m.group(1).replace(".", " ").strip() if m else (filename or "")


def _quality_label(filename: str) -> str | None:
    m = _RESOLUTION_RE.search(filename or "")
    return m.group(1) if m else None


class R008Provider(Provider):
    id = "R-008"
    name = "DahmerMovies"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            if not data.title:
                return result
            if data.season is None and not data.year:
                return result

            if data.season is None:
                path = f"/movies/{data.title.replace(':', '')} ({data.year})/"
            else:
                path = f"/tvs/{data.title.replace(':', ' -')}/Season {data.season}/"

            url = _API + quote(path)
            client = await get_client()
            headers = {"User-Agent": UA, "Referer": f"{_API}/"}
            res = await client.get(url, headers=headers, timeout=60)
            if res.status_code >= 400:
                return result

            if data.season is None:
                ep_re = re.compile(r"(1080p|2160p)", re.I)
            else:
                ep_re = re.compile(rf"S{_pad2(data.season)}E{_pad2(data.episode)}", re.I)

            # Parse links from directory listing
            for m in re.finditer(r'href="([^"]+)"', res.text):
                href = m.group(1)
                link_text = href.split("/")[-1]
                if not ep_re.search(link_text):
                    continue
                tags = _quality_tags(link_text)
                link = href if href.startswith("http") else _API + ("" if href.startswith("/") else "/") + href
                result.streams.append(Stream(
                    url=link,
                    type="m3u8" if ".m3u8" in link else "mp4",
                    server=f"R-008 DahmerMovies {tags}".strip(),
                    quality=_quality_label(link_text),
                    playback_headers={"Referer": f"{_API}/"},
                ))
        except Exception:
            pass
        return result
