"""
ENGINE/providers/Stream/R-002/R-002.py — VidFast

Type: m3u8 | mp4
Flow:
  1. GET /movie/<tmdb_id> or /tv/<tmdb_id>/<s>/<e> — scrape the \"en\":\"<token>\" blob.
  2. enc-vidfast?text=<token>&version=1 -> { result: { servers, stream, token } }
  3. POST <servers> with X-CSRF-Token -> encrypted servers blob.
  4. POST dec-vidfast { text, version } -> { result: [{ name, data }] }
  5. For each server: POST <stream>/<data> -> encrypted, then dec-vidfast -> { result: { url, tracks } }

Headers required:
  - User-Agent: standard browser UA
  - Referer: https://vidfast.pro/
  - X-Requested-With: XMLHttpRequest   (steps 3 & 5 POST calls)
  - X-CSRF-Token: <token from step 2>  (steps 3 & 5 POST calls)

Ported from Streamplay's VidFastProvider.
"""
from __future__ import annotations

import re

from ENGINE.providers.base import Provider, LinkData, Result, Stream, Subtitle
from ENGINE.tools.http import get_client, UA
from ENGINE.tools.encdec import enc_dec_get, enc_dec_post

_API = "https://vidfast.pro"
_VERSION = "1"


class R002Provider(Provider):
    id = "R-002"
    name = "VidFast"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        if not data.tmdb_id:
            return result
        try:
            base_headers = {
                "User-Agent": UA,
                "Referer": f"{_API}/",
                "X-Requested-With": "XMLHttpRequest",
            }

            # Step 1: GET watch page -> extract encoded token
            if data.season is None:
                page_url = f"{_API}/movie/{data.tmdb_id}"
            else:
                page_url = f"{_API}/tv/{data.tmdb_id}/{data.season}/{data.episode}"

            client = await get_client()
            page_res = await client.get(page_url, headers=base_headers, timeout=15)
            if page_res.status_code >= 400:
                return result

            m = re.search(r'\\\"en\\\":\\\"(.*?)\\\"', page_res.text)
            encoded_text = m.group(1) if m else None
            if not encoded_text:
                return result

            # Step 2: decrypt token -> servers/stream/csrf
            enc_json = await enc_dec_get(f"enc-vidfast?text={encoded_text}&version={_VERSION}")
            meta = (enc_json or {}).get("result")
            if not meta or not meta.get("servers") or not meta.get("stream") or not meta.get("token"):
                return result

            servers_url: str = meta["servers"]
            stream_base: str = meta["stream"]
            token: str = meta["token"]

            post_headers = {**base_headers, "X-CSRF-Token": token}

            # Step 3: POST to servers URL -> encrypted servers blob
            servers_res = await client.post(servers_url, content=b"", headers=post_headers, timeout=15)
            servers_encrypted = servers_res.text
            if not servers_encrypted:
                return result

            # Step 4: decrypt servers list
            servers_root = await enc_dec_post("dec-vidfast", {"text": servers_encrypted, "version": _VERSION})
            servers_list: list = (servers_root or {}).get("result") or []
            if not servers_list:
                return result

            # Step 5: each server -> encrypted stream -> decrypt -> final URL
            import asyncio

            async def fetch_server(server: dict, idx: int) -> None:
                name = server.get("name") or f"Server {idx + 1}"
                server_data = server.get("data")
                if not server_data:
                    return
                try:
                    stream_url = f"{stream_base}/{server_data}"
                    stream_res = await client.post(stream_url, content=b"", headers=post_headers, timeout=15)
                    stream_encrypted = stream_res.text
                    if not stream_encrypted:
                        return
                    stream_root = await enc_dec_post("dec-vidfast", {"text": stream_encrypted, "version": _VERSION})
                    final_url = (stream_root or {}).get("result", {}).get("url")
                    if not final_url:
                        return
                    result.streams.append(Stream(
                        url=final_url,
                        type="m3u8" if ".m3u8" in final_url else "mp4",
                        server=f"R-002 VidFast [{name}]",
                        quality="1080p",
                        playback_headers={"Referer": f"{_API}/"},
                    ))
                    for track in (((stream_root or {}).get("result") or {}).get("tracks") or []):
                        if track.get("file") and track.get("label"):
                            result.subtitles.append(Subtitle(
                                url=track["file"],
                                language=track["label"],
                            ))
                except Exception:
                    pass

            await asyncio.gather(*[fetch_server(s, i) for i, s in enumerate(servers_list)])

        except Exception:
            pass
        return result
