"""
ENGINE/providers/Stream/R-029/R-029.py — VidZee

Type: m3u8 | mp4
Flow:
  1. Fan out across servers sr=1..8 concurrently.
  2. GET player.vidzee.wtf/api/server?id=<tmdb>&sr=<n>[&ss=<s>&ep=<e>]
  3. Decrypt each encrypted link with AES-256-CBC
     key = "pleasedontscrapemesaywallahi" padded/truncated to 32 bytes.
     encoded as base64("<ivB64>:<ciphertextB64>").
  4. Push m3u8/mp4 streams + subtitle tracks.

Headers required:
  - API GET: Referer: https://player.vidzee.wtf/  (server returns 403 without it)
  - Playback: Referer = value from response json.headers.referer (server-specific)
    Falls back to https://player.vidzee.wtf/ if not present.

Ported from Streamplay's VidzeeProvider.
"""
from __future__ import annotations

import asyncio
import base64

from ENGINE.providers.base import Provider, LinkData, Result, Stream, Subtitle
from ENGINE.tools.http import get_client, UA

_BASE = "https://player.vidzee.wtf"
_REFERER = f"{_BASE}/"

# Key = "pleasedontscrapemesaywallahi" NUL-padded to 32 bytes
_SECRET = b"pleasedontscrapemesaywallahi"
_KEY = (_SECRET + b"\x00" * 32)[:32]


def _decrypt(enc: str) -> str:
    """AES-256-CBC decrypt. enc = base64('<ivB64>:<ctB64>')."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding as _pad

    inner = base64.b64decode(enc + "==").decode("utf-8", errors="replace")
    idx = inner.index(":")
    iv = base64.b64decode(inner[:idx] + "==")
    ct = base64.b64decode(inner[idx + 1:] + "==")
    cipher = Cipher(algorithms.AES(_KEY), modes.CBC(iv))
    dec = cipher.decryptor()
    padded = dec.update(ct) + dec.finalize()
    unpadder = _pad.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8", errors="replace")


class R029Provider(Provider):
    id = "R-029"
    name = "VidZee"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        if not data.tmdb_id:
            return result
        try:
            client = await get_client()
            lock = asyncio.Lock()

            async def fetch_server(sr: int) -> None:
                try:
                    if data.season is not None:
                        url = (
                            f"{_BASE}/api/server"
                            f"?id={data.tmdb_id}&sr={sr}"
                            f"&ss={data.season}&ep={data.episode}"
                        )
                    else:
                        url = f"{_BASE}/api/server?id={data.tmdb_id}&sr={sr}"

                    # Referer is required — vidzee returns 403 without it
                    res = await client.get(
                        url,
                        headers={"User-Agent": UA, "Referer": _REFERER},
                        timeout=12,
                    )
                    if res.status_code >= 400:
                        return
                    j = res.json()

                    global_hdrs: dict = {}
                    if isinstance(j.get("headers"), dict):
                        global_hdrs = {k: str(v) for k, v in j["headers"].items()}

                    urls = j.get("url", [])
                    if not isinstance(urls, list):
                        return

                    streams_local: list[Stream] = []
                    subs_local: list[Subtitle] = []

                    for obj in urls:
                        enc_link: str = obj.get("link", "")
                        name: str = obj.get("name", "VidZee")
                        vtype: str = obj.get("type", "hls")
                        lang: str = obj.get("lang", "Unknown")
                        flag: str = obj.get("flag", "")
                        if not enc_link.strip():
                            continue

                        try:
                            final_url = _decrypt(enc_link)
                        except Exception:
                            final_url = enc_link

                        # Validate URL
                        if not final_url.startswith("http"):
                            continue

                        # Use the referer the server told us; each sub-server may
                        # have a different CDN that checks Origin/Referer
                        referer = global_hdrs.get("referer", _REFERER)
                        display = f"VidZee {name} ({lang} - {flag})" if flag.strip() else f"VidZee {name} ({lang})"
                        stream_type = "m3u8" if vtype.lower() == "hls" else "mp4"

                        streams_local.append(Stream(
                            url=final_url,
                            type=stream_type,
                            server=f"R-029 {display}",
                            quality="1080p",
                            headers={**global_hdrs, "Referer": referer},
                            playback_headers={"Referer": referer},
                        ))

                    for sub in (j.get("tracks") or []):
                        sub_lang = sub.get("lang", "Unknown")
                        sub_url = sub.get("url", "")
                        if sub_url.strip():
                            subs_local.append(Subtitle(language=sub_lang, url=sub_url))

                    async with lock:
                        result.streams.extend(streams_local)
                        result.subtitles.extend(subs_local)
                except Exception:
                    pass

            await asyncio.gather(*[fetch_server(sr) for sr in range(1, 9)])
        except Exception:
            pass
        return result
