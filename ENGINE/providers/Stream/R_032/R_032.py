"""
ENGINE/providers/Stream/R-032/R-032.py — MovieBox

Type: m3u8 | mp4
Flow:
  1. GET /wefeed-h5api-bff/app/get-latest-app-pkgs?app_name=moviebox -> x-user token from headers
  2. POST /wefeed-h5api-bff/subject/search {keyword, page, perPage, subjectType} -> subjectId
  3. GET /wefeed-h5-bff/web/post/list/subject?id=<subjectId> -> detailPath
  4. GET /wefeed-h5api-bff/subject/download?subjectId=... -> downloads[]
  5. GET /wefeed-h5api-bff/subject/play?subjectId=... -> streams[]

Ported from Streamline's moviebox.js (CineStream port).
"""
from __future__ import annotations

from ENGINE.providers.base import Provider, LinkData, Result, Stream, Subtitle
from ENGINE.tools.http import get_client, UA

_BASE = "https://h5-api.aoneroom.com"
_WEB_BASE = "https://h5.aoneroom.com"


def _unwrap(obj):
    if not obj or not isinstance(obj, dict):
        return {}
    data = obj.get("data")
    if isinstance(data, dict):
        if isinstance(data.get("data"), dict):
            return data["data"]
        return data
    return obj


def _clean_title(t):
    import re
    t = str(t or "")
    t = re.sub(r"\sS\d+.*$", "", t, flags=re.I).strip().lower()
    return t


class R032Provider(Provider):
    id = "R-032"
    name = "MovieBox"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        if not data.title:
            return result

        try:
            client = await get_client()
            is_tv = data.type == "tv"

            # Step 1: Get x-user token
            token = ""
            try:
                pkg_res = await client.get(
                    f"{_BASE}/wefeed-h5api-bff/app/get-latest-app-pkgs?app_name=moviebox",
                    headers={"User-Agent": UA},
                    timeout=15,
                )
                token = pkg_res.headers.get("x-user", "") or pkg_res.headers.get("X-User", "")
            except Exception:
                pass

            base_headers = {
                "X-Client-Info": '{"timezone":"Africa/Nairobi"}',
                "Accept-Language": "en-US,en;q=0.5",
                "Accept": "application/json",
                "Referer": _BASE,
                "Host": "h5-api.aoneroom.com",
                "Connection": "keep-alive",
                "User-Agent": UA,
            }
            if token:
                base_headers["X-User"] = token

            # Step 2: Search
            search_body = {
                "keyword": data.title,
                "page": 1,
                "perPage": 24,
                "subjectType": 2 if is_tv else 1,
            }
            search_res = await client.post(
                f"{_BASE}/wefeed-h5api-bff/subject/search",
                json=search_body,
                headers=base_headers,
                timeout=20,
            )
            if search_res.status_code >= 400:
                return result

            search_data = _unwrap(search_res.json())
            items = search_data.get("items") or []
            want = _clean_title(data.title)
            subject_id = None
            lang = "Original"

            for it in items:
                t = _clean_title(it.get("title") or it.get("name"))
                if t == want or want in t or t in want:
                    subject_id = it.get("id") or it.get("subjectId")
                    lang = it.get("lanName") or it.get("language") or lang
                    break

            if not subject_id and items:
                subject_id = items[0].get("id") or items[0].get("subjectId")
                lang = items[0].get("lanName") or items[0].get("language") or lang

            if not subject_id:
                return result

            # Step 3: Get detail path
            detail_path = ""
            try:
                detail_res = await client.get(
                    f"{_WEB_BASE}/wefeed-h5-bff/web/post/list/subject?id={subject_id}",
                    timeout=15,
                )
                detail_obj = detail_res.json()
                detail_items = ((detail_obj.get("data") or {}).get("items")) or []
                if detail_items and detail_items[0].get("subject"):
                    detail_path = detail_items[0]["subject"].get("detailPath", "")
            except Exception:
                pass

            # Step 4 & 5: Collect download + play URLs
            params = f"subjectId={subject_id}"
            if is_tv and data.season and data.episode:
                params += f"&se={data.season}&ep={data.episode}"
            if detail_path:
                from urllib.parse import quote
                params += f"&detailPath={quote(detail_path)}"

            referer = f"https://fmoviesunblocked.net/spa/videoPlayPage/movies/{detail_path}?id={subject_id}&type=/movie/detail"
            req_headers = {**base_headers, "Referer": referer, "Origin": "https://fmoviesunblocked.net"}
            play_headers = {"Referer": referer, "Origin": "https://fmoviesunblocked.net", "User-Agent": UA}

            seen = set()

            async def collect(endpoint):
                try:
                    res = await client.get(f"{_BASE}{endpoint}{params}", headers=req_headers, timeout=20)
                    if res.status_code >= 400:
                        return
                    d = _unwrap(res.json())
                    for item in (d.get("downloads") or d.get("streams") or []):
                        if not item or not item.get("url") or item.get("vipLocked"):
                            continue
                        res_name = item.get("resolution") or item.get("resolutions") or "Auto"
                        if res_name in seen:
                            continue
                        seen.add(res_name)
                        url = item["url"]
                        result.streams.append(Stream(
                            url=url,
                            type="m3u8" if ".m3u8" in url else "mp4",
                            server=f"R-032 MovieBox [{lang}]",
                            quality=res_name,
                            playback_headers=play_headers,
                        ))
                    # Captions
                    for cap in (d.get("captions") or []):
                        if not cap or not cap.get("url"):
                            continue
                        sub = Subtitle(
                            url=cap["url"],
                            language=cap.get("lan") or cap.get("lanName") or "en",
                        )
                        for s in result.streams:
                            if not hasattr(s, 'subtitles') or s.subtitles is None:
                                s.subtitles = []
                            if len(s.subtitles) < 8:
                                s.subtitles.append(sub)
                except Exception:
                    pass

            await collect("/wefeed-h5api-bff/subject/download?")
            await collect("/wefeed-h5api-bff/subject/play?")

        except Exception:
            pass
        return result
