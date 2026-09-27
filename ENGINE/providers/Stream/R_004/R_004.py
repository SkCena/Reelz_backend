"""
ENGINE/providers/Stream/R-004/R_004.py — HexaSU

Flow:
  1. Generate random 32-byte hex API key.
  2. enc-hexa (with X-Api-Key) -> cap token.
  3. GET images endpoint (with X-Cap-Token) -> encrypted blob.
  4. dec-hexa { text, key } -> { result: { sources: [ { server, url } ] } }

Ported from Streamplay's HexaProvider.
"""
from __future__ import annotations

import os

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA
from ENGINE.tools.encdec import enc_dec_get, enc_dec_post

_API = "https://theemoviedb.hexa.su"


def _random_hex32() -> str:
    return os.urandom(32).hex()


class R004Provider(Provider):
    id = "R-004"
    name = "HexaSU"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        if data.is_anime:
            return result
        try:
            key = _random_hex32()
            base_headers = {
                "User-Agent": UA,
                "Referer": "https://hexa.su/",
                "Accept": "text/plain",
                "X-Fingerprint-Lite": "e9136c41504646444",
                "X-Api-Key": key,
            }

            # Step 1: get cap token
            enc = await enc_dec_get("enc-hexa", base_headers)
            token = (enc or {}).get("result", {}).get("token")
            if not token:
                return result

            headers = {**base_headers, "X-Cap-Token": token}

            # Step 2: fetch encrypted sources blob
            if data.season is None:
                url = f"{_API}/api/tmdb/movie/{data.tmdb_id}/images"
            else:
                url = f"{_API}/api/tmdb/tv/{data.tmdb_id}/season/{data.season}/episode/{data.episode}/images"

            client = await get_client()
            encrypted = (await client.get(url, headers=headers, timeout=15)).text
            if not encrypted:
                return result

            # Step 3: decrypt
            dec = await enc_dec_post("dec-hexa", {"text": encrypted, "key": key}, {"Content-Type": "application/json"})
            if not dec or dec.get("status") != 200:
                return result

            for src in (dec.get("result") or {}).get("sources", []):
                src_url = src.get("url", "")
                server_name = src.get("server", "")
                if not src_url or not server_name:
                    continue
                name = server_name[0].upper() + server_name[1:]
                result.streams.append(Stream(
                    url=src_url,
                    type="m3u8" if ".m3u8" in src_url else "mp4",
                    server=f"R-004 HexaSU {name}",
                    headers={"Referer": "https://hexa.su/"},
                    playback_headers={"Referer": f"https://hexa.su/", "Origin": "https://hexa.su"},
                ))
        except Exception:
            pass
        return result
