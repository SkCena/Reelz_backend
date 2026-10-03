"""
ENGINE/providers/Stream/R-041/R-041.py — Netnaija (wefeed platform, V3 signed API)

Netnaija uses the same wefeed/oneroom backend as MovieBox.
Same H5 API, same V3 hosts, same signing keys.
Only Referer/Origin differ (netnaija.film).

Hindi + Tamil + Telugu dubs available as separate stream cards.


Flow:
  1. H5 search with IN headers -> subjectId (+ separate "[Hindi]" variant id)
  2. H5 detail -> dubs list (each language variant has its own subjectId)
  3. V3 signed play-info per variant -> direct MP4 on macdn.aoneroom.com
     (requires signCookie as Cookie header for playback)
  4. V3 get-ext-captions -> subtitles with actual language labels

Language honesty: streams are labeled ONLY with the language the API
actually reports ("[Hindi]" title marker or dub lanName). The original
track is labeled "Original", never guessed as English.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
import uuid
from urllib.parse import urlencode

from ENGINE.providers.base import Provider, LinkData, Result, Stream, Subtitle
from ENGINE.tools.http import get_client

# MovieBox API client (vendored, MIT licensed)
try:
    from ENGINE.tools.movieboxapi.async_wrapper import AsyncMovieBoxClient
    _HAS_MB_CLIENT = True
except ImportError:
    _HAS_MB_CLIENT = False
except Exception:
    _HAS_MB_CLIENT = False

# ── Endpoints ─────────────────────────────────────────────────────────────────

_H5_BASE = "https://h5-api.aoneroom.com/wefeed-h5api-bff"

_V3_HOSTS = [
    "https://api8.aoneroom.com",
    "https://api7.aoneroom.com",
    "https://api6.aoneroom.com",
    "https://api5.aoneroom.com",
    "https://api4.aoneroom.com",
    "https://api4sg.aoneroom.com",
    "https://api3.aoneroom.com",
    "https://api6sg.aoneroom.com",
    "https://api.inmoviebox.com",
]
_V3_BOOTSTRAP_PATH = "/wefeed-mobile-bff/tab-operating"
_V3_PLAYINFO_PATH = "/wefeed-mobile-bff/subject-api/play-info"
_V3_CAPTIONS_PATH = "/wefeed-mobile-bff/subject-api/get-ext-captions"

_V3_RETRY_CODES = {403, 407, 429, 500, 502, 503, 504}

# HMAC secrets (base64) for V3 request signing — from the public movieboxapi
# project (putzxdevs/movieboxapi). Overridable via env on key rotation.
_V3_SECRET_DEFAULT = (
    "76iRl07s0xSN9jqmEWAt79EBJZulIQIsV64FZr2O"
)
_V3_SECRET_ALT = (
    "Xqn2nnO41/L92o1iuXhSLHTbXvY4Z5ZZ62m8mSLA"
)

# ── IN region identity ────────────────────────────────────────────────────────

_IN = {
    "locale": "en_IN",
    "language": "en",
    "country": "IN",
    "timezone": "Asia/Kolkata",
    "sp_code": "40401",  # Airtel India (MCC+MNC)
    "accept_language": "en-IN,en;q=0.9",
    "referer_path": "/in",
    "system_language": "en",
    "region": "IN",
}

_LANG_LABELS = {
    "hi": "Hindi",
    "hin": "Hindi",
    "en": "English",
    "eng": "English",
    "id": "Indonesian",
    "ta": "Tamil",
    "te": "Telugu",
    "ml": "Malayalam",
    "kn": "Kannada",
    "bn": "Bengali",
    "mr": "Marathi",
    "pa": "Punjabi",
    "gu": "Gujarati",
}


# ── V3 request signing (HMAC-MD5) ─────────────────────────────────────────────

def _md5_hex(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _b64_decode(value: str) -> bytes:
    padding = (4 - len(value) % 4) % 4
    return base64.b64decode(value + "=" * padding)


def _client_token(ts_ms: int) -> str:
    ts = str(ts_ms)
    return f"{ts},{_md5_hex(ts[::-1].encode())}"


def _sorted_query(url: str) -> str:
    from urllib.parse import parse_qs, urlparse
    qs = parse_qs(urlparse(url).query, keep_blank_values=True)
    if not qs:
        return ""
    parts = []
    for key in sorted(qs.keys()):
        for value in qs[key]:
            parts.append(f"{key}={value}")
    return "&".join(parts)


def _signature(method: str, accept: str, content_type: str, url: str,
               body: str | None, ts_ms: int) -> str:
    from urllib.parse import urlparse
    path = urlparse(url).path or ""
    query = _sorted_query(url)
    canonical_url = f"{path}?{query}" if query else path
    if body is not None:
        bb = body.encode("utf-8")
        body_hash = _md5_hex(bb[:102_400])
        body_len = str(len(bb))
    else:
        body_hash, body_len = "", ""
    canonical = (
        f"{method.upper()}\n{accept or ''}\n{content_type or ''}\n"
        f"{body_len}\n{ts_ms}\n{body_hash}\n{canonical_url}"
    )
    mac = hmac.new(_b64_decode(_V3_SECRET_DEFAULT),
                   canonical.encode("utf-8"), hashlib.md5)
    return f"{ts_ms}|2|{base64.b64encode(mac.digest()).decode()}"


def _signed_headers(method: str, url: str, ua: str, client_info: str,
                    auth_token: str = "", play_mode: bool = False) -> dict:
    ts = int(time.time() * 1000)
    accept = "application/json"
    ctype = "application/json"
    headers = {
        "User-Agent": ua,
        "Accept": accept,
        "Content-Type": ctype,
        "Connection": "keep-alive",
        "X-Client-Token": _client_token(ts),
        "x-tr-signature": _signature(method, accept, ctype, url, None, ts),
        "X-Client-Info": client_info,
        "X-Client-Status": "0",
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    if play_mode:
        headers["X-Play-Mode"] = "2"
    return headers


def _make_device_identity() -> tuple[str, str]:
    """Fixed Android device fingerprint with IN region preset."""
    did = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    gaid = "12345678-1234-1234-1234-123456789abc"
    ua = (
        "com.community.oneroom/50020044 "
        f"(Linux; U; Android 12; {_IN['locale']}; "
        "2201117TG; Build/S1B.220414.015; Cronet/135.0.7012.3)"
    )
    ci = (
        '{"package_name":"com.community.oneroom","version_name":"3.0.03.0529.03",'
        '"version_code":50020044,"os":"android","os_version":"12",'
        '"install_ch":"ps","device_id":"' + did + '","install_store":"ps",'
        '"gaid":"' + gaid + '","brand":"Redmi","model":"2201117TG",'
        f'"system_language":"{_IN["system_language"]}","net":"NETWORK_WIFI",'
        f'"region":"{_IN["region"]}","timezone":"{_IN["timezone"]}",'
        f'"sp_code":"{_IN["sp_code"]}","X-Play-Mode":"2"}}'
    )
    return ua, ci


def _h5_headers(extra: dict | None = None) -> dict:
    h = {
        "User-Agent": (
            f"Mozilla/5.0 (Linux; Android 13; {_IN['locale']}; Redmi Note 12) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Mobile Safari/537.36"
        ),
        "Referer": f"https://netnaija.film{_IN['referer_path']}",
        "Origin": "https://netnaija.film",
        "X-Client-Info": json.dumps({
            "timezone": _IN["timezone"],
            "locale": _IN["locale"],
            "country": _IN["country"],
            "language": _IN["language"],
            "sp_code": _IN["sp_code"],
        }),
        "X-Request-Lang": _IN["language"],
        "Accept-Language": _IN["accept_language"],
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _clean_title(t: str) -> str:
    t = str(t or "")
    t = re.sub(r"\s*\[.*?\]\s*", " ", t)  # strip [Hindi] etc for matching
    t = re.sub(r"\sS\d+.*$", "", t, flags=re.I).strip().lower()
    return re.sub(r"\s+", " ", t)


def _decode_dash_prefix(sign_cookie: str) -> str:
    """Decode the real DASH URL prefix hidden in Edge-Cache-Cookie.

    The signCookie contains e.g.:
      Edge-Cache-Cookie = urlprefix=aHR0cHM6Ly9zYmNkbjMuaGFrdW5heW1hdGF0YS5jb20vZGFzaC8...
    which base64-decodes to:
      https://sbcdnN.hakunaymatata.com/dash/{subjectId}_{se}_{ep}_{res}_{codec}_{x}/
    The DASH manifest lives at {prefix}index.mpd.
    The macdn.aoneroom.com MP4 in play-info is only a tiny preview file.
    """
    if not sign_cookie:
        return ""
    try:
        m = re.search(r"urlprefix=([A-Za-z0-9+/=_-]+)", sign_cookie)
        if not m:
            return ""
        b64 = m.group(1).replace("-", "+").replace("_", "/")
        b64 += "=" * ((4 - len(b64) % 4) % 4)
        prefix = base64.b64decode(b64.encode()).decode("utf-8", errors="ignore")
        if "hakunaymatata.com/dash/" in prefix:
            if not prefix.endswith("/"):
                prefix += "/"
            return prefix
    except Exception:
        pass
    return ""


def _lang_label(code: str, name: str = "") -> str:
    if name:
        return name
    c = (code or "").lower()
    return _LANG_LABELS.get(c, c.upper() if c else "")


class R041Provider(Provider):
    id = "R-041"
    name = "Netnaija"

    def __init__(self):
        self._ua, self._ci = _make_device_identity()
        self._v3_token = ""

    # ── H5 helpers ──

    async def _h5_token(self) -> str:
        try:
            # Use HTTP/1.1 explicitly — some CDN edges (aoneroom) reset
            # HTTP/2 connections from certain datacenter IPs (e.g. Render).
            import httpx
            async with httpx.AsyncClient(
                http2=False,
                follow_redirects=True,
                timeout=15,
                headers={"User-Agent": _h5_headers()["User-Agent"]},
            ) as client:
                r = await client.post(
                    f"{_H5_BASE}/subject/search-suggest",
                    json={"keyword": "a", "perPage": 1},
                    headers=_h5_headers(),
                    timeout=15,
                )
                if r.status_code == 200:
                    xuser = r.headers.get("x-user") or ""
                    if xuser:
                        try:
                            return json.loads(xuser).get("token", "")
                        except Exception:
                            pass
        except Exception:
            pass
        return ""

    async def _h5_search(self, title: str, is_tv: bool) -> list[dict]:
        try:
            import httpx
            jwt = await self._h5_token()
            headers = _h5_headers()
            if jwt:
                headers["Authorization"] = f"Bearer {jwt}"
            async with httpx.AsyncClient(http2=False, follow_redirects=True, timeout=20) as client:
                r = await client.post(
                    f"{_H5_BASE}/subject/search",
                    json={"keyword": title, "page": 1, "perPage": 20,
                          "subjectType": 2 if is_tv else 1},
                    headers=headers,
                    timeout=20,
                )
                if r.status_code != 200:
                    return []
                data = r.json().get("data") or {}
                return data.get("items") or []
        except Exception:
            return []

    async def _h5_dubs(self, detail_path: str, jwt: str) -> list[dict]:
        """Get language-variant dubs from detail (each has own subjectId)."""
        try:
            client = await get_client()
            headers = _h5_headers()
            if jwt:
                headers["Authorization"] = f"Bearer {jwt}"
            r = await client.get(
                f"{_H5_BASE}/detail?detailPath={detail_path}",
                headers=headers,
                timeout=20,
            )
            if r.status_code != 200:
                return []
            inner = (r.json().get("data") or {})
            subject = inner.get("subject") or inner
            return subject.get("dubs") or []
        except Exception:
            return []

    # ── V3 helpers ──

    async def _v3_bootstrap(self) -> str:
        if self._v3_token:
            return self._v3_token
        try:
            client = await get_client()
            for base in _V3_HOSTS:
                try:
                    url = f"{base}{_V3_BOOTSTRAP_PATH}?page=1&tabId=0&version="
                    r = await client.get(
                        url,
                        headers=_signed_headers("GET", url, self._ua, self._ci),
                        timeout=15,
                    )
                    xuser = r.headers.get("x-user") or ""
                    if xuser:
                        try:
                            tok = json.loads(xuser).get("token", "")
                            if tok:
                                self._v3_token = tok
                                return tok
                        except Exception:
                            pass
                except Exception:
                    continue
        except Exception:
            pass
        return ""

    async def _v3_get(self, path: str, params: dict,
                      play_mode: bool = False) -> dict | None:
        token = await self._v3_bootstrap()
        qs = ("?" + urlencode(params)) if params else ""
        try:
            client = await get_client()
            for base in _V3_HOSTS:
                try:
                    url = f"{base}{path}{qs}"
                    r = await client.get(
                        url,
                        headers=_signed_headers("GET", url, self._ua, self._ci,
                                                token, play_mode),
                        timeout=20,
                    )
                    xuser = r.headers.get("x-user") or ""
                    if xuser:
                        try:
                            nt = json.loads(xuser).get("token", "")
                            if nt:
                                self._v3_token = nt
                        except Exception:
                            pass
                    if r.status_code in _V3_RETRY_CODES:
                        continue
                    if r.status_code != 200:
                        continue
                    body = r.json()
                    return body.get("data") if "data" in body else body
                except Exception:
                    continue
        except Exception:
            pass
        return None

    async def _play_info(self, subject_id: str, se: int, ep: int,
                         lan: str = "") -> dict | None:
        params = {"subjectId": subject_id, "se": se, "ep": ep}
        if lan:
            params["lan"] = lan
        data = await self._v3_get(_V3_PLAYINFO_PATH, params, play_mode=True)
        if isinstance(data, dict) and data.get("streams"):
            return data
        # movies / fallback: try se=0, ep=0
        if se <= 1 and ep <= 1:
            params2 = {"subjectId": subject_id, "se": 0, "ep": 0}
            if lan:
                params2["lan"] = lan
            data = await self._v3_get(_V3_PLAYINFO_PATH, params2, play_mode=True)
            if isinstance(data, dict) and data.get("streams"):
                return data
        return None

    async def _captions(self, subject_id: str, resource_id: str) -> list[dict]:
        if not resource_id:
            return []
        try:
            data = await self._v3_get(
                _V3_CAPTIONS_PATH,
                {"subjectId": subject_id, "resourceId": resource_id},
            )
            if isinstance(data, dict):
                return data.get("extCaptions") or data.get("captions") or []
            if isinstance(data, list):
                return data
        except Exception:
            pass
        return []

    # ── main ──

    async def run(self, data: LinkData) -> Result:
        result = Result()
        if not data.title:
            return result

        try:
            is_tv = data.type == "tv"
            se = data.season or 0
            ep = data.episode or 0

            items = await self._h5_search(data.title, is_tv)
            if not items:
                return result

            want = _clean_title(data.title)
            year_str = str(data.year) if data.year else ""

            # Pick best original + best Hindi variant (API-labeled "[Hindi]")
            best_orig = None
            best_hindi = None
            for it in items:
                raw_t = str(it.get("title") or "")
                t = _clean_title(raw_t)
                if not (want and (t == want or want in t or t in want)):
                    continue
                rd = str(it.get("releaseDate") or "")
                year_ok = (not year_str) or (year_str in rd)
                is_hindi = "[hindi]" in raw_t.lower()
                if is_hindi:
                    if year_ok and not best_hindi:
                        best_hindi = it
                    elif not best_hindi:
                        best_hindi = it
                else:
                    if year_ok and not best_orig:
                        best_orig = it
                        break
                    if not best_orig:
                        best_orig = it
            if not best_orig and not best_hindi:
                best_orig = items[0]

            # Collect variants: (subject_id, detail_path, language_label)
            variants: list[tuple[str, str, str]] = []
            seen_ids: set[str] = set()

            def add_variant(sid: str, dpath: str, lang: str):
                sid = str(sid or "")
                if sid and sid not in seen_ids:
                    seen_ids.add(sid)
                    variants.append((sid, dpath, lang))

            if best_hindi:
                add_variant(best_hindi.get("subjectId") or best_hindi.get("id"),
                            best_hindi.get("detailPath") or "", "Hindi")
            if best_orig:
                sid = str(best_orig.get("subjectId") or best_orig.get("id") or "")
                dpath = best_orig.get("detailPath") or ""
                add_variant(sid, dpath, "")  # language resolved via dubs below

            # Enrich via detail dubs (each dub = own subjectId + actual lanName)
            jwt = await self._h5_token()
            for sid, dpath, _ in list(variants):
                if not dpath:
                    continue
                dubs = await self._h5_dubs(dpath, jwt)
                for d in dubs:
                    if not isinstance(d, dict):
                        continue
                    dsid = str(d.get("subjectId") or "")
                    lan_name = d.get("lanName") or ""
                    lan_code = d.get("lanCode") or ""
                    label = _lang_label(lan_code, lan_name)
                    # prioritize Hindi dubs right after the main variants
                    add_variant(dsid, d.get("detailPath") or "", label)
                break  # only enrich from the first variant's detail

            if not variants:
                return result

            # Fetch play-info for variants (Hindi first, then original, max 3)
            import asyncio as _aio
            ordered = sorted(
                variants[:4],
                key=lambda v: 0 if v[2] == "Hindi" else (1 if v[2] == "" else 2),
            )[:3]

            async def fetch_one(sid: str, lang: str):
                # Try new MovieBox API client first (more reliable)
                if _HAS_MB_CLIENT:
                    try:
                        mb = AsyncMovieBoxClient(region="IN", timeout=25.0)
                        try:
                            stream_result = await mb.get_stream(
                                subject_id=str(sid),
                                se=se if se > 0 else 0,
                                ep=ep if ep > 0 else 0,
                                resolution=1080,
                            )
                            if stream_result and stream_result.url:
                                # Convert to dict format expected below
                                info = {
                                    "streams": [{
                                        "url": stream_result.url,
                                        "signCookie": stream_result.sign_cookie or "",
                                        "resolutions": ",".join(
                                            str(q.height) for q in (stream_result.qualities or [])
                                            if hasattr(q, "height") and q.height
                                        ) or "1080",
                                        "id": stream_result.resource_id or "",
                                    }],
                                    "_via_mb_client": True,
                                    "_subtitles": [
                                        {"lang": s.lang, "url": s.url}
                                        for s in (stream_result.subtitles or [])
                                        if hasattr(s, "url") and s.url
                                    ],
                                }
                                return sid, lang, info
                        finally:
                            mb.close()
                    except Exception:
                        pass  # fall through to legacy method
                # Fallback to legacy _play_info
                info = await self._play_info(sid, se, ep)
                return sid, lang, info

            play_results = await _aio.gather(
                *[fetch_one(sid, lang) for sid, _, lang in ordered]
            )

            seen_urls: set[str] = set()
            for sid, lang, info in play_results:
                if not info:
                    continue
                streams = info.get("streams") or []
                # prefer stream with signCookie (authorized CDN URL)
                target = None
                for s in streams:
                    if s.get("signCookie"):
                        target = s
                        break
                if target is None and streams:
                    target = streams[0]
                if not target:
                    continue

                # Real stream: DASH manifest on hakunaymatata CDN.
                # The play-info "url" (macdn.aoneroom.com MP4) is only a tiny
                # preview file — the actual video URL prefix is base64-encoded
                # inside the signCookie's Edge-Cache-Cookie (urlprefix=...).
                sign_cookie = str(target.get("signCookie") or "")
                dash_prefix = _decode_dash_prefix(sign_cookie)
                if dash_prefix:
                    url = dash_prefix + "index.mpd"
                    stype = "mp4"  # DASH via ExoPlayer; mp4 type = direct play
                else:
                    url = str(target.get("url") or "")
                    stype = "m3u8" if ".m3u8" in url else "mp4"
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                # quality: highest from "1080,720,480" style field
                quality = ""
                raw_res = str(target.get("resolutions") or "")
                try:
                    nums = [int(x) for x in raw_res.split(",") if x.strip().isdigit()]
                    if nums:
                        quality = f"{max(nums)}p"
                except Exception:
                    pass

                playback = {"Cookie": sign_cookie} if sign_cookie else {}
                # DASH needs Referer too (CDN checks it)
                if dash_prefix:
                    playback["Referer"] = "https://movie-box.co/"

                # subtitles with actual language labels
                subs: list[Subtitle] = []
                caps = await self._captions(sid, str(target.get("id") or ""))
                seen_langs: set[str] = set()
                for cap in caps:
                    if not isinstance(cap, dict):
                        continue
                    lc = str(cap.get("lan") or cap.get("language") or "").lower()
                    curl = str(cap.get("url") or "")
                    if lc and curl and lc not in seen_langs:
                        seen_langs.add(lc)
                        subs.append(Subtitle(
                            url=curl,
                            language=_lang_label(lc),
                        ))
                        if len(subs) >= 8:
                            break

                result.streams.append(Stream(
                    url=url,
                    type=stype,
                    server="MovieBox IN",
                    language=lang or "Original",
                    quality=quality,
                    headers={},
                    playback_headers=playback,
                ))
                # attach subtitles to the stream via result-level list too
                for sub in subs:
                    if sub not in result.subtitles:
                        result.subtitles.append(sub)

        except Exception:
            pass
        return result
