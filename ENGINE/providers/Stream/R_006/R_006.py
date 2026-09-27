"""
ENGINE/providers/Stream/R-006/R_006.py — Xpass

Loads embed page, extracts `var backups=[...]`, fetches each backup JSON,
and walks playlist[0].sources for HLS/MP4 links.
Ported from Streamplay's XpassProvider.
"""
from __future__ import annotations

import json
import re

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA

_API = "https://play.xpass.top"
_BACKUPS_RE = re.compile(r"var backups=(\[[\s\S]*?])\s*(?:;|<)")


def _extract_backups(html: str) -> list[tuple[str, str]]:
    m = _BACKUPS_RE.search(html)
    if not m:
        return []
    try:
        arr = json.loads(m.group(1))
    except Exception:
        return []
    out = []
    for obj in arr:
        name = obj.get("name") if isinstance(obj, dict) else None
        url = obj.get("url") if isinstance(obj, dict) else None
        if name and url:
            out.append((name, url))
    return out


class R006Provider(Provider):
    id = "R-006"
    name = "Xpass"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            base_ref = f"{_API}/"
            if data.season is None:
                embed_url = f"{_API}/e/movie/{data.tmdb_id}"
            else:
                embed_url = f"{_API}/e/tv/{data.tmdb_id}/{data.season}/{data.episode}"

            client = await get_client()
            headers = {"User-Agent": UA, "Referer": base_ref}
            html = (await client.get(embed_url, headers=headers, timeout=15)).text
            backups = _extract_backups(html)

            for name, url in backups:
                try:
                    full_url = url if url.startswith("http") else _API + url
                    j = (await client.get(full_url, headers=headers, timeout=10)).json()
                    sources = (j.get("playlist") or [{}])[0].get("sources", []) if isinstance(j, dict) else []
                    for src in (sources if isinstance(sources, list) else []):
                        file_url: str = src.get("file", "") if isinstance(src, dict) else ""
                        if not file_url or not file_url.startswith("http"):
                            continue
                        src_type: str = src.get("type", "") if isinstance(src, dict) else ""
                        is_m3u8 = re.search(r"hls", src_type, re.I) or ".m3u8" in file_url
                        result.streams.append(Stream(
                            url=file_url,
                            type="m3u8" if is_m3u8 else "mp4",
                            server=f"R-006 Xpass [{name}]",
                            headers={"Referer": base_ref},
                            playback_headers={"Referer": base_ref},
                        ))
                except Exception:
                    continue
        except Exception:
            pass
        return result
