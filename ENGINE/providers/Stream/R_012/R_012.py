"""
ENGINE/providers/Stream/R-012/R_012.py — CastleTV (api.hlowb.com)

Indian multi-language HLS. AES-128-CBC encrypted API responses.
Requires CASTLE_SUFFIX env var.

Flow:
  1. GET securityKey
  2. Keyword search -> best title/medium/year match
  3. Episode detail
  4. getVideo2 per language track -> stream URLs

Ported from Streamplay's CastleProvider.
"""
from __future__ import annotations

import os
import re
import time
import json

from ENGINE.providers.base import Provider, LinkData, Result, Stream, Subtitle
from ENGINE.tools.http import get_client, UA

_API = "https://api.hlowb.com"
_HEADERS = {"Referer": _API, "User-Agent": "okhttp/4.9.2", "Content-Type": "application/json"}
_KEY_CACHE: dict = {}
_STOP = {"the","a","an","of","in","on","to","and","or","my","with","season","part","world",
         "another","life","story","movie","film","tv","series","saga","arc","chapter","no","wo",
         "ga","isekai","de"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _distinctive(s: str) -> set[str]:
    return {w for w in _norm(s).split() if w and w not in _STOP}


def _title_match(want: str, candidate: str) -> int:
    nw, nc = _norm(want), _norm(candidate)
    if nw == nc:
        return 3
    a, b = _distinctive(want), _distinctive(candidate)
    if not a or not b:
        return 0
    inter = len(a & b) / min(len(a), len(b))
    if inter >= 0.999:
        return 2
    if inter >= 0.6:
        return 1
    return 0


def _medium(row: dict) -> str:
    n = (row.get("movieTypeName") or "").lower()
    if "anime" in n:
        return "anime"
    if re.search(r"movie|film", n):
        return "movie"
    if re.search(r"tv|show|series|drama|reality|season", n):
        return "tv"
    return ""


def _derive_key(b64: str) -> bytes:
    import base64
    suffix = os.getenv("CASTLE_SUFFIX", "")
    m = list(base64.b64decode(b64 + "==")) + list(suffix.encode())
    m = m[:16] if len(m) >= 16 else m + [0] * (16 - len(m))
    return bytes(m)


def _decrypt(enc_b64: str, key_b64: str) -> str | None:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding
        import base64
        k = _derive_key(key_b64)
        ct = base64.b64decode(enc_b64 + "==")
        cipher = Cipher(algorithms.AES(k), modes.CBC(k))
        dec = cipher.decryptor()
        unpadder = padding.PKCS7(128).unpadder()
        raw = dec.update(ct) + dec.finalize()
        return (unpadder.update(raw) + unpadder.finalize()).decode("utf-8")
    except Exception:
        return None


def _unwrap(raw: str, key: str):
    enc = raw
    try:
        j = json.loads(raw)
        if "data" in j:
            enc = j["data"]
    except Exception:
        pass
    dec = _decrypt(enc, key)
    if dec is None:
        _KEY_CACHE.clear()
        return None
    # Quote large integers before parsing
    fixed = re.sub(r"([:\[,]\s*)(-?\d{15,})(?=\s*[,}\]])", r'\1"\2"', dec)
    try:
        parsed = json.loads(fixed)
        return parsed.get("data") if "data" in parsed else parsed
    except Exception:
        return None


async def _get_security_key() -> str | None:
    global _KEY_CACHE
    if _KEY_CACHE.get("exp", 0) > time.time():
        return _KEY_CACHE.get("value")
    try:
        client = await get_client()
        r = (await client.get(
            f"{_API}/v0.1/system/getSecurityKey/1?channel=IndiaA&clientType=1&lang=en-US",
            headers={"Referer": _API, "User-Agent": "okhttp/4.9.2"},
            timeout=12,
        )).json()
        if r.get("code") == 200 and r.get("data"):
            _KEY_CACHE = {"value": r["data"], "exp": time.time() + 3600}
            return r["data"]
    except Exception:
        pass
    return None


async def _enc_get(url: str, key: str):
    client = await get_client()
    res = await client.get(url, headers=_HEADERS, timeout=15)
    return _unwrap(res.text, key)


class R012Provider(Provider):
    id = "R-012"
    name = "Castle"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        if not os.getenv("CASTLE_SUFFIX") or not data.title:
            return result
        try:
            key = await _get_security_key()
            if not key:
                return result

            # Search
            sr = await _enc_get(
                f"{_API}/film-api/v1.1.0/movie/searchByKeyword?channel=IndiaA&clientType=1"
                f"&keyword={data.title.replace(' ', '%20')}&lang=en-US&mode=1"
                f"&packageName=com.external.castle&page=1&size=20",
                key,
            )
            rows: list = (sr or {}).get("rows") or (sr or {}).get("list") or []
            if not rows:
                return result

            want = _norm(data.title)
            want_org = _norm(data.org_title or "")
            want_medium = ("anime" if data.is_anime and data.type != "movie" else
                           "movie" if data.type == "movie" else "tv")
            target_year = data.year

            def score_row(m: dict) -> float:
                s = max(_title_match(want, m.get("title") or ""),
                        _title_match(want_org, m.get("title") or "") if want_org else 0)
                if s <= 0:
                    return 0
                med = _medium(m)
                if med:
                    s += 2 if med == want_medium else -3
                if target_year and m.get("publishTime"):
                    try:
                        pt = m["publishTime"]
                        if isinstance(pt, (int, float)) and pt < 1e12:
                            pt *= 1000
                        y = int(str(pt)[:4]) if isinstance(pt, str) else int(pt / 1000 // 31536000 + 1970)
                        import datetime
                        y = datetime.datetime.fromtimestamp(pt / 1000).year if isinstance(pt, (int, float)) else int(str(pt)[:4])
                        if y > 1970:
                            s += 3 if y == target_year else -min(2, abs(y - target_year) * 0.5)
                    except Exception:
                        pass
                return s

            best = max(rows, key=score_row)
            if score_row(best) <= 0:
                return result

            # Detail
            det = await _enc_get(
                f"{_API}/film-api/v1.9.9/movie?channel=IndiaA&clientType=1&lang=en-US"
                f"&movieId={best['id']}&packageName=com.external.castle",
                key,
            )
            eps: list = (det or {}).get("episodes") or []
            if not eps:
                return result

            ep = eps[0]
            if data.type == "tv" and data.episode:
                found = next((e for e in eps if int(e.get("number", 0)) == data.episode), None)
                if not found:
                    return result
                ep = found

            tracks = ep.get("tracks") or []
            individual = [t for t in tracks if t.get("existIndividualVideo") is True]
            reqs = [{"name": "Original"}] + [{"languageId": int(t["languageId"]), "name": t.get("languageName", "Original")} for t in individual]

            client = await get_client()
            seen_files: set[str] = set()

            for rq in reqs:
                for res_val in [3, 2, 1]:
                    body: dict = {
                        "mode": "1", "appMarket": "GuanWang", "clientType": "1",
                        "woolUser": "false",
                        "apkSignKey": "ED0955EB04E67A1D9F3305B95454FED485261475",
                        "androidVersion": "13",
                        "movieId": str(best["id"]), "episodeId": str(ep["id"]),
                        "isNewUser": "true", "resolution": str(res_val),
                        "packageName": "com.external.castle",
                    }
                    if rq.get("languageId") is not None:
                        body["languageId"] = rq["languageId"]

                    try:
                        raw = (await client.post(
                            f"{_API}/film-api/v2.0.1/movie/getVideo2?clientType=1"
                            "&packageName=com.external.castle&channel=IndiaA&lang=en-US",
                            content=json.dumps(body),
                            headers=_HEADERS,
                            timeout=15,
                        )).text
                        vd = _unwrap(raw, key)
                        if vd and vd.get("videoUrl") and not vd.get("permissionDenied"):
                            url = vd["videoUrl"]
                            file_key = url.split("?")[0]
                            if file_key in seen_files:
                                break
                            seen_files.add(file_key)
                            result.streams.append(Stream(
                                url=url,
                                type="m3u8" if ".m3u8" in url else "mp4",
                                server=f"R-012 Castle · {rq['name']}",
                                headers={"Referer": f"{_API}/"},
                                playback_headers={"Referer": _API, "Origin": "https://www.hlowb.com", "User-Agent": "okhttp/4.9.2"},
                            ))
                            for sub in (vd.get("subtitles") or []):
                                if sub.get("url"):
                                    result.subtitles.append(Subtitle(
                                        url=sub["url"],
                                        language=sub.get("title") or sub.get("abbreviate") or "Sub",
                                    ))
                            break
                    except Exception:
                        continue
        except Exception:
            pass
        return result
