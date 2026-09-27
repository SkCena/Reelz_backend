"""
ENGINE/providers/Stream/R_026/R_026.py — VidLink

Type: m3u8
Flow:
  1. Encrypt TMDB ID via enc-dec.app/api/enc-vidlink
  2. GET vidlink.pro/api/b/movie/<enc> or /api/b/tv/<enc>/<s>/<e>
  3. Extract stream.playlist (m3u8 URL with optional embedded headers)
"""
from __future__ import annotations

import json
from urllib.parse import urlparse, parse_qs

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA
from ENGINE.tools.encdec import enc_dec_get

_API = "https://vidlink.pro"


class R026Provider(Provider):
    id = "R-026"
    name = "VidLink"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            # Step 1: encrypt TMDB id
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

            # Extract optional embedded headers from query string
            try:
                parsed = urlparse(playlist)
                qs = parse_qs(parsed.query)
                h_raw = qs.get("headers", [None])[0]
                if h_raw:
                    extra = json.loads(h_raw)
                    headers = {**headers, **extra}
            except Exception:
                pass

            result.streams.append(Stream(
                url=playlist,
                type="m3u8",
                server="R-026 VidLink",
                quality="1080p",
                playback_headers={"Referer": f"{_API}/", "Origin": _API, "User-Agent": UA},
            ))
        except Exception:
            pass
        return result
