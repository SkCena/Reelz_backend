"""
ENGINE/providers/Stream/R_030/R_030.py — Peachify

Type: m3u8 | mp4
Flow:
  1. Fan out across 6 eat-peach/peachify sub-servers concurrently
  2. GET <server>/movie/<tmdb_id>  or  /tv/<tmdb_id>/<s>/<e>
  3. JSON body has encrypted `data` field → AES-256-GCM decrypt
     key = 64-hex string (32 bytes); payload = "<ivB64url>.<cipherB64url>.<tagB64url>"
  4. Decrypted JSON has `sources[]` with optional proxy wrapping
     /m3u8-proxy?url=<enc>&headers=<enc>  or  /mp4-proxy?url=<enc>&headers=<enc>
  5. Push final m3u8/mp4 streams
"""
from __future__ import annotations

import asyncio
import base64
import json
from urllib.parse import parse_qs, urlparse

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA

_KEY_HEX = "a8f2a1b5e9c470814f6b2c3a5d8e7f9c1a2b3c4d5e3f7a8b8cad1e2d0a4d5c5b"
_SERVERS = [
    "https://usa.eat-peach.sbs/holly",
    "https://usa.eat-peach.sbs/multi",
    "https://usa.eat-peach.sbs/ice",
    "https://usa.eat-peach.sbs/air",
    "https://usa.eat-peach.sbs/net",
    "https://uwu.peachify.top/moviebox",
]
_PEACHIFY_ORIGIN = "https://peachify.top"


def _b64url_decode(s: str) -> bytes:
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * ((4 - len(s) % 4) % 4)
    return base64.b64decode(s)


def _decrypt(encrypt: str) -> str | None:
    """AES-256-GCM decrypt. encrypt = '<ivB64url>.<cipherB64url>.<tagB64url>'"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        parts = encrypt.split(".")
        if len(parts) != 3:
            return None
        iv = _b64url_decode(parts[0])
        cipher_bytes = _b64url_decode(parts[1])
        tag = _b64url_decode(parts[2])
        key = bytes.fromhex(_KEY_HEX)
        aesgcm = AESGCM(key)
        # GCM: ciphertext + tag concatenated
        plaintext = aesgcm.decrypt(iv, cipher_bytes + tag, None)
        return plaintext.decode("utf-8")
    except Exception:
        return None


def _unwrap_proxy(raw_url: str) -> tuple[str, dict]:
    """Extract real URL + headers from /m3u8-proxy or /mp4-proxy query string."""
    try:
        parsed = urlparse(raw_url)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        real_url = qs.get("url", [raw_url])[0]
        hdrs_raw = qs.get("headers", ["{}"])[0]
        hdrs = json.loads(hdrs_raw) if hdrs_raw else {}
        return real_url, hdrs
    except Exception:
        return raw_url, {}


class R030Provider(Provider):
    id = "R-030"
    name = "Peachify"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            client = await get_client()
            req_headers = {
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.5",
                "Origin": _PEACHIFY_ORIGIN,
                "Referer": f"{_PEACHIFY_ORIGIN}/",
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:139.0) Gecko/20100101 Firefox/139.0",
            }
            lock = asyncio.Lock()

            async def fetch_server(server: str) -> None:
                try:
                    if data.season is None:
                        api_url = f"{server}/movie/{data.tmdb_id}"
                    else:
                        api_url = f"{server}/tv/{data.tmdb_id}/{data.season}/{data.episode}"

                    res = await client.get(api_url, headers=req_headers, timeout=12)
                    if res.status_code >= 400:
                        return

                    try:
                        body = res.json()
                        encrypt = body.get("data", "")
                    except Exception:
                        return

                    if not encrypt:
                        return

                    decrypted_str = _decrypt(encrypt)
                    if not decrypted_str:
                        return

                    payload = json.loads(decrypted_str)
                    provider_name: str = payload.get("providerName", "Peachify")
                    sources = payload.get("sources", [])
                    if not isinstance(sources, list):
                        return

                    local_streams: list[Stream] = []
                    for src in sources:
                        raw_url: str = src.get("url", "")
                        if not raw_url:
                            continue
                        dub: str = src.get("dub", "")
                        src_type: str = src.get("type", "hls")
                        quality: int = src.get("quality", 0)
                        src_hdrs: dict = src.get("headers", {})

                        is_proxy = "/m3u8-proxy" in raw_url or "/mp4-proxy" in raw_url
                        if is_proxy:
                            final_url, proxy_hdrs = _unwrap_proxy(raw_url)
                        else:
                            final_url = raw_url
                            proxy_hdrs = dict(src_hdrs)

                        referer = proxy_hdrs.get("referer") or src_hdrs.get("referer") or f"{_PEACHIFY_ORIGIN}/"
                        origin = proxy_hdrs.get("origin") or src_hdrs.get("origin") or _PEACHIFY_ORIGIN
                        ua = proxy_hdrs.get("user-agent") or src_hdrs.get("user-agent") or UA

                        cap_name = provider_name[0].upper() + provider_name[1:] if provider_name else provider_name
                        label = f"Peachify [{cap_name}]"
                        if dub:
                            label += f" • {dub}"

                        stream_type = "m3u8" if (src_type == "hls" or ".m3u8" in final_url) else "mp4"
                        local_streams.append(Stream(
                            url=final_url,
                            type=stream_type,
                            server=f"R-030 {label}",
                            quality=f"{quality}p" if quality > 0 else None,
                            headers={"Origin": origin, "Referer": referer, "User-Agent": ua},
                            playback_headers={"Referer": referer, "Origin": origin},
                        ))

                    async with lock:
                        result.streams.extend(local_streams)
                except Exception:
                    pass

            await asyncio.gather(*[fetch_server(s) for s in _SERVERS])
        except Exception:
            pass
        return result
