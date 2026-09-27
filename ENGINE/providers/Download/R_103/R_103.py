"""
ENGINE/providers/Download/R_103/R_103.py — VidLink Download

Type: hls (resolved from m3u8 master)
Produces per-quality HLS index.m3u8 download links from VidLink.

Flow:
  1. Encrypt TMDB ID via enc-dec.app/enc-vidlink
  2. GET vidlink.pro/api/b/movie/<enc> or /api/b/tv/<enc>/<s>/<e>
  3. Extract stream.playlist (HLS master)
  4. resolve_master → one DownloadItem per quality variant
"""
from __future__ import annotations

import json
from urllib.parse import urlparse, parse_qs

from ENGINE.providers.base import Provider, LinkData, Result, DownloadItem
from ENGINE.tools.http import get_client, UA
from ENGINE.tools.encdec import enc_dec_get
from ENGINE.tools.hls import resolve_master

_API = "https://vidlink.pro"


class R103Provider(Provider):
    id = "R-103"
    name = "VidLink Download"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            enc_data = await enc_dec_get(f"enc-vidlink?text={data.tmdb_id}")
            token = (enc_data or {}).get("result")
            if not token:
                return result

            headers = {
                "User-Agent": UA,
                "Connection": "keep-alive",
                "Referer": f"{_API}/",
                "Origin": _API,
            }

            if data.season is None:
                api_url = f"{_API}/api/b/movie/{token}"
            else:
                api_url = f"{_API}/api/b/tv/{token}/{data.season}/{data.episode}"

            client = await get_client()
            res = await client.get(api_url, headers=headers, timeout=15)
            if res.status_code >= 400:
                return result

            j = res.json()
            playlist = (j.get("stream") or {}).get("playlist", "")
            if not playlist:
                return result

            # Merge any embedded headers from query string
            try:
                parsed = urlparse(playlist)
                qs = parse_qs(parsed.query)
                h_raw = qs.get("headers", [None])[0]
                if h_raw:
                    extra = json.loads(h_raw)
                    headers = {**headers, **extra}
            except Exception:
                pass

            variants = await resolve_master(playlist, headers=headers)
            for v in variants:
                result.downloads.append(DownloadItem(
                    url=v["url"],
                    type="hls",
                    quality=v["quality"],
                    language="English",
                    headers=headers,
                    playback_headers={"Referer": f"{_API}/", "Origin": _API},
                ))
        except Exception:
            pass
        return result
