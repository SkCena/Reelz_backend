"""
ENGINE/providers/Stream/R-031/R-031.py — VidSrcXYZ

Type: m3u8
Flow:
  1. GET vidsrc-embed.su/embed/movie?imdb=<id>  or /embed/tv?imdb=<id>&season=&episode=
  2. Scrape <iframe src> from embed page
  3. GET iframe URL (Referer = iframe URL itself) -> scrape `src: '...'` -> prorcpUrl
  4. GET prorcpUrl (Referer = iframe URL) -> decrypt with one of 11 keyed methods
  5. Resolve {v1}..{v4} domain placeholders in final URLs

Headers required:
  - Step 1 GET:  User-Agent only
  - Step 2 GET:  Referer: <iframe_url>   (required — server 403s without it)
  - Step 3 GET:  Referer: <iframe_url>   (procp endpoint validates iframe referer)
  - Playback:    Referer: <base_of_prorcpUrl>

Ported from Streamplay's VidSrcXyzProvider.
"""
from __future__ import annotations

import base64
import re

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA
from ENGINE.tools.scraper import parse

_BASE = "https://vidsrc-embed.su"

_VSUBS = {
    "v1": "shadowlandschronicles.com",
    "v2": "cloudnestra.com",
    "v3": "thepixelpioneer.com",
    "v4": "putgate.org",
    "v5": "",
}


# ── Pure helpers (no forward refs) ───────────────────────────────────────────

def _b64dl(s: str) -> str:
    """base64-decode to latin-1 string (URL-safe aware)."""
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * ((4 - len(s) % 4) % 4)
    return base64.b64decode(s).decode("latin-1")


def _codes(s: str) -> list[int]:
    return [ord(c) for c in s]


def _fc(codes: list[int]) -> str:
    return "".join(chr(c) for c in codes)


def _rot13(s: str) -> str:
    out = []
    for ch in s:
        c = ord(ch)
        if 97 <= c <= 109 or 65 <= c <= 77:
            out.append(chr(c + 13))
        elif 110 <= c <= 122 or 78 <= c <= 90:
            out.append(chr(c - 13))
        else:
            out.append(ch)
    return "".join(out)


def _from_hex_pairs(s: str) -> str:
    pairs = re.findall("..", s)
    return "".join(chr(int(h, 16)) for h in pairs)


def _method_TsA2KGDGux(x: str) -> str:
    return _fc([c - 7 for c in _codes(_b64dl("".join(reversed(x))))])


def _method_ux8qjPHC66(x: str) -> str:
    key = "X9a(O;FMV2-7VO5x;Ao\x05:dN1NoFs?j,"
    rev = "".join(reversed(x))
    decoded = _from_hex_pairs(rev)
    return _fc([c ^ ord(key[i % len(key)]) for i, c in enumerate(_codes(decoded))])


def _method_xTyBxQyGTA(x: str) -> str:
    filtered = "".join(c for i, c in enumerate(reversed(x)) if i % 2 == 0)
    return _b64dl(filtered)


def _method_IhWrImMIGL(x: str) -> str:
    rev = "".join(reversed(x))
    rot = _rot13(rev)
    return _b64dl("".join(reversed(rot)))


def _method_o2VSUnjnZl(x: str) -> str:
    frm = "xyzabcdefghijklmnopqrstuvwXYZABCDEFGHIJKLMNOPQRSTUVW"
    to  = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    mp  = dict(zip(frm, to))
    return "".join(mp.get(c, c) for c in x)


def _method_eSfH1IRMyL(x: str) -> str:
    shifted = _fc([c - 1 for c in _codes("".join(reversed(x)))])
    return _from_hex_pairs(shifted)


def _method_Oi3v1dAlaM(x: str) -> str:
    return _fc([c - 5 for c in _codes(_b64dl("".join(reversed(x))))])


def _method_sXnL9MQIry(x: str) -> str:
    xor_key = "pWB9V)[*4I`nJpp?ozyB~dbr9yt!_n4u"
    hex_dec = _from_hex_pairs(x)
    dec = _fc([c ^ ord(xor_key[i % len(xor_key)]) for i, c in enumerate(_codes(hex_dec))])
    shifted = _fc([c - 3 for c in _codes(dec)])
    return _b64dl(shifted)


def _method_JoAHUMCLXV(x: str) -> str:
    return _fc([c - 3 for c in _codes(_b64dl("".join(reversed(x))))])


def _method_KJHidj7det(x: str) -> str:
    key = "3SAY~#%Y(V%>5d/Yg$G[Lh1rK4a;7ok"
    inner = _b64dl(x[10: len(x) - 16])
    return _fc([c ^ ord(key[i % len(key)]) for i, c in enumerate(_codes(inner))])


def _method_playerjs(x: str) -> str:
    try:
        a = x[2:]
        def _b1(s: str) -> str:
            return base64.b64encode(s.encode("latin-1")).decode()
        for k in ["*,4).(_)()", "33-*.4/9[6", ":]&*1@@1=&", "=(=:19705/", "%?6497.[:4"]:
            a = a.replace("/@#@/" + _b1(k), "")
        return _b64dl(a)
    except Exception:
        return ""


_DECRYPT_METHODS: dict = {
    "TsA2KGDGux": _method_TsA2KGDGux,
    "ux8qjPHC66": _method_ux8qjPHC66,
    "xTyBxQyGTA": _method_xTyBxQyGTA,
    "IhWrImMIGL": _method_IhWrImMIGL,
    "o2VSUnjnZl": _method_o2VSUnjnZl,
    "eSfH1IRMyL": _method_eSfH1IRMyL,
    "Oi3v1dAlaM": _method_Oi3v1dAlaM,
    "sXnL9MQIry": _method_sXnL9MQIry,
    "JoAHUMCLXV": _method_JoAHUMCLXV,
    "KJHidj7det": _method_KJHidj7det,
    "playerjs":   _method_playerjs,
}


class R031Provider(Provider):
    id = "R-031"
    name = "VidSrcXYZ"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        imdb_id = data.imdb_id
        if not imdb_id:
            return result
        try:
            client = await get_client()
            ua_headers = {"User-Agent": UA}

            # Step 1: embed page -> iframe src
            if data.season is None:
                embed_url = f"{_BASE}/embed/movie?imdb={imdb_id}"
            else:
                embed_url = (
                    f"{_BASE}/embed/tv?imdb={imdb_id}"
                    f"&season={data.season}&episode={data.episode}"
                )

            r1 = await client.get(embed_url, headers=ua_headers, timeout=15)
            soup1 = parse(r1.text)
            tag = soup1.find("iframe")
            iframe_url: str = tag.get("src", "") if tag else ""
            if iframe_url.startswith("//"):
                iframe_url = "https:" + iframe_url
            if not iframe_url.startswith("http"):
                return result

            # Step 2: iframe -> prorcpUrl
            # Referer must be the iframe URL itself — server returns 403 without it
            r2 = await client.get(
                iframe_url,
                headers={**ua_headers, "Referer": iframe_url},
                timeout=15,
            )
            src_m = re.search(r"src:\s+'(.*?)'", r2.text)
            if not src_m:
                return result
            base_m = re.match(r"(https?://[^/]+)", iframe_url)
            procp_url = (base_m.group(1) if base_m else "") + src_m.group(1)

            # Step 3: procp page -> decrypt
            # Referer = iframe_url (procp validates that the procp was reached from the iframe)
            r3 = await client.get(
                procp_url,
                headers={**ua_headers, "Referer": iframe_url},
                timeout=15,
            )
            html3 = r3.text

            method_id: str | None = None
            content: str | None = None

            pj_m = re.search(r'Playerjs\(\{.*?file:"(.*?)".*?\}\)', html3, re.S)
            if pj_m:
                method_id = "playerjs"
                content = pj_m.group(1)
            else:
                soup3 = parse(html3)
                rep = soup3.find(id="reporting_content")
                if rep:
                    sib = rep.find_next_sibling()
                    if sib:
                        method_id = sib.get("id")
                        content = sib.get_text()

            if not method_id or not content:
                return result

            fn = _DECRYPT_METHODS.get(method_id)
            if not fn:
                return result

            try:
                decrypted = fn(content)
            except Exception:
                return result

            if not decrypted:
                return result

            # Step 4: resolve domain placeholders and push streams
            referer = procp_url.split("rcp")[0] if "rcp" in procp_url else procp_url

            for part in decrypted.split(" or "):
                raw_url = part.strip()
                if not raw_url.startswith("http"):
                    continue
                ver_m = re.search(r"\{(v\d+)\}", raw_url)
                ver_str = ver_m.group(1) if ver_m else ""
                for ver, domain in _VSUBS.items():
                    raw_url = raw_url.replace("{" + ver + "}", domain)

                cap_ver = ver_str[0].upper() + ver_str[1:] if ver_str else ""
                label = f"VidSrcXYZ Server {cap_ver}".strip()

                result.streams.append(Stream(
                    url=raw_url,
                    type="m3u8" if ".m3u8" in raw_url else "mp4",
                    server=f"R-031 {label}",
                    headers={"Referer": referer},
                    playback_headers={"Referer": referer},
                ))

        except Exception:
            pass
        return result
