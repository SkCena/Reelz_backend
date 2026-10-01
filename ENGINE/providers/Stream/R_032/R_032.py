"""
ENGINE/providers/Stream/R-032/R-032.py — MovieBox (fast API)

Type: mp4
Flow (simplified, from phoenix project):
  1. POST /subject/search-suggest -> JWT token from x-user header
  2. POST /subject/search {keyword, page, perPage, subjectType} -> subjectId
  3. GET /subject/play?subjectId=...&se=...&ep=... -> direct MP4 streams

Skips the slow get-latest-app-pkgs and post/list/subject steps.
"""
from __future__ import annotations

import json

from ENGINE.providers.base import Provider, LinkData, Result, Stream, Subtitle
from ENGINE.tools.http import get_client, UA

_BASE = "https://h5-api.aoneroom.com/wefeed-h5api-bff"
_SITE = "https://movie-box.co"


def _unwrap(obj):
    if not obj or not isinstance(obj, dict):
        return {}
    data = obj.get("data")
    if isinstance(data, dict):
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

            # Step 1: Get JWT via search-suggest (fast)
            jwt = ""
            try:
                r = await client.post(
                    f"{_BASE}/subject/search-suggest",
                    json={"keyword": "a", "perPage": 1},
                    headers={
                        "User-Agent": UA,
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    timeout=15,
                )
                if r.status_code == 200:
                    xuser = r.headers.get("x-user") or r.headers.get("X-User") or ""
                    if xuser:
                        try:
                            jwt = json.loads(xuser).get("token", "")
                        except Exception:
                            pass
            except Exception:
                pass
            if not jwt:
                return result

            headers = {
                "User-Agent": UA,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {jwt}",
                "X-Client-Info": json.dumps({"timezone": "UTC"}),
                "X-Request-Lang": "en",
            }

            # Step 2: Search
            search_res = await client.post(
                f"{_BASE}/subject/search",
                json={
                    "keyword": data.title,
                    "page": 1,
                    "perPage": 20,
                    "subjectType": 2 if is_tv else 1,
                },
                headers=headers,
                timeout=20,
            )
            if search_res.status_code != 200:
                return result

            items = _unwrap(search_res.json()).get("items") or []
            want = _clean_title(data.title)
            year_str = str(data.year) if data.year else ""

            best = None
            for it in items:
                t = _clean_title(it.get("title") or it.get("name"))
                if want and (t == want or want in t or t in want):
                    # Prefer year match
                    rd = str(it.get("releaseDate") or "")
                    if year_str and year_str in rd:
                        best = it
                        break
                    if not best:
                        best = it
            if not best and items:
                best = items[0]
            if not best:
                return result

            subject_id = best.get("id") or best.get("subjectId")
            detail_path = best.get("detailPath") or ""
            lang = best.get("lanName") or best.get("language") or ""
            if not subject_id:
                return result

            # Step 3: Get play URLs
            se = data.season or 0
            ep = data.episode or 0
            play_url = (
                f"{_BASE}/subject/play?subjectId={subject_id}"
                f"&se={se}&ep={ep}&detailPath={detail_path}&streamSignType=1"
            )
            play_headers = {
                **headers,
                "Referer": f"{_SITE}/movies/{detail_path}",
            }
            play_res = await client.get(play_url, headers=play_headers, timeout=20)
            if play_res.status_code != 200:
                return result

            play_data = _unwrap(play_res.json())
            streams = play_data.get("streams") or []
            seen = set()

            for s in streams:
                url = s.get("url") or ""
                if not url or s.get("vipLocked"):
                    continue
                if url in seen:
                    continue
                seen.add(url)

                res_name = str(s.get("resolutions") or s.get("resolution") or "")
                if res_name and not res_name.endswith("p"):
                    res_name = f"{res_name}p"

                # hakunaymatata CDN needs Referer
                stream_headers = None
                if "hakunaymatata.com" in url:
                    stream_headers = {"Referer": "https://movie-box.co/"}

                result.streams.append(Stream(
                    url=url,
                    type="m3u8" if ".m3u8" in url else "mp4",
                    language=lang or "",
                    quality=res_name or "",
                    headers=stream_headers or {},
                ))

            # Captions
            for cap in (play_data.get("captions") or []):
                curl = cap.get("url") or ""
                if not curl:
                    continue
                sub = Subtitle(
                    url=curl,
                    language=cap.get("lan") or cap.get("lanName") or "English",
                )
                for st in result.streams:
                    if not getattr(st, "subtitles", None):
                        st.subtitles = []
                    if len(st.subtitles) < 8:
                        st.subtitles.append(sub)

        except Exception:
            pass
        return result
