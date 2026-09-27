"""
ENGINE/providers/Stream/R-003/R_003.py — VidRock

AES-CBC encrypted TMDB id → /api/<type>/<encoded> → sources map.
Key: x7k9mPqT2rWvY8zA5bC3nF6hJ2lK4mN9  (IV = first 16 bytes of key)
Ported from Streamplay's VidrockProvider / vidrockEncode().
"""
from __future__ import annotations

import base64
import urllib.parse
from typing import Optional

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA

_API = "https://vidrock.ru"
_KEY = b"x7k9mPqT2rWvY8zA5bC3nF6hJ2lK4mN9"


def _encode(tmdb_id: int, content_type: str, season: Optional[int], episode: Optional[int]) -> str:
    """AES-256-CBC encrypt the TMDB id string, return URL-safe base64."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding

    plain = (
        f"{tmdb_id}_{season}_{episode}"
        if content_type == "tv" and season is not None and episode is not None
        else str(tmdb_id)
    )
    iv = _KEY[:16]
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plain.encode()) + padder.finalize()
    cipher = Cipher(algorithms.AES(_KEY), modes.CBC(iv))
    enc = cipher.encryptor()
    ct = enc.update(padded) + enc.finalize()
    return base64.b64encode(ct).decode().replace("+", "-").replace("/", "_").replace("=", "")


class R003Provider(Provider):
    id = "R-003"
    name = "VidRock"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            ctype = "tv" if data.season is not None else "movie"
            encoded = _encode(data.tmdb_id, ctype, data.season, data.episode)
            headers = {"Origin": _API, "User-Agent": UA, "Referer": f"{_API}/"}

            client = await get_client()
            res = await client.get(f"{_API}/api/{ctype}/{encoded}", headers=headers, timeout=15)
            sources = res.json()
            if not isinstance(sources, dict):
                return result

            for key, src in sources.items():
                raw_url: str = src.get("url", "") if isinstance(src, dict) else ""
                lang: str = src.get("language", "Unknown") if isinstance(src, dict) else "Unknown"
                if not raw_url or raw_url == "null":
                    continue

                safe_url = raw_url
                if "%" in raw_url:
                    try:
                        safe_url = urllib.parse.unquote(raw_url)
                    except Exception:
                        safe_url = raw_url

                if "/playlist/" in safe_url:
                    try:
                        pl = (await client.get(safe_url, headers=headers, timeout=15)).json()
                        if isinstance(pl, list):
                            for item in pl:
                                u = item.get("url") if isinstance(item, dict) else None
                                if u:
                                    result.streams.append(Stream(
                                        url=u,
                                        type="m3u8" if ".m3u8" in u else "mp4",
                                        server=f"R-003 VidRock-{key}",
                                        quality=str(item.get("resolution", "")) + "p" if item.get("resolution") else None,
                                        headers=headers,
                                        playback_headers={"Referer": f"{_API}/", "Origin": _API},
                                    ))
                    except Exception:
                        pass
                else:
                    result.streams.append(Stream(
                        url=safe_url,
                        type="m3u8" if ".m3u8" in safe_url else "mp4",
                        server=f"R-003 VidRock-{key}",
                        quality=lang,
                        headers=headers,
                        playback_headers={"Referer": f"{_API}/", "Origin": _API},
                    ))
        except Exception:
            pass
        return result
