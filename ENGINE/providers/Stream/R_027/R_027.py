"""
ENGINE/providers/Stream/R_027/R_027.py — CineMacity

Type: m3u8 | mp4
Flow:
  1. Search cinemacity.cc by IMDB id (requires pre-baked session cookie)
  2. Open first result page
  3. Find script containing atob(...) → base64-decode → Playerjs config JSON
  4. Parse file array → push m3u8/mp4 streams + optional subtitle list
"""
from __future__ import annotations

import base64
import json
import re

from ENGINE.providers.base import Provider, LinkData, Result, Stream, Subtitle
from ENGINE.tools.http import get_client, UA
from ENGINE.tools.scraper import parse

_API = "https://cinemacity.cc"
# Pre-baked session cookie (base64 of "dle_user_id=32729; dle_password=894171c6a8dab18ee594d5c652009a35;")
_COOKIE = base64.b64decode(
    "ZGxlX3VzZXJfaWQ9MzI3Mjk7IGRsZV9wYXNzd29yZD04OTQxNzFjNmE4ZGFiMThlZTU5NGQ1YzY1MjAwOWEzNTs="
).decode()


def _fix_js_obj(s: str) -> str:
    """Quote bare JS object keys so JSON.parse won't reject them."""
    return re.sub(r'([{,]\s*)([A-Za-z_$][\w$]*)\s*:', r'\1"\2":', s)


class R027Provider(Provider):
    id = "R-027"
    name = "CineMacity"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        if not data.imdb_id:
            return result
        try:
            headers = {"Cookie": _COOKIE, "User-Agent": UA}
            client = await get_client()

            search_url = (
                f"{_API}/?do=search&subaction=search"
                f"&search_start=0&full_search=0&story={data.imdb_id}"
            )
            search_res = await client.get(search_url, headers=headers, timeout=15)
            soup = parse(search_res.text)
            page_link = soup.select_one("div.dar-short_item > a")
            if not page_link:
                return result
            page_url = page_link.get("href", "")
            if not page_url:
                return result

            page_res = await client.get(page_url, headers=headers, timeout=15)
            page_soup = parse(page_res.text)

            player_js = ""
            for script in page_soup.find_all("script"):
                content = script.string or ""
                if "atob(" in content:
                    player_js = content
                    break

            if not player_js:
                return result

            b64_m = re.search(r'atob\("([^"]+)"\)', player_js)
            if not b64_m:
                return result

            decoded = base64.b64decode(b64_m.group(1)).decode("utf-8", errors="replace")

            pj_m = re.search(r"new Playerjs\((.*?)\);", decoded, re.S)
            if not pj_m:
                return result

            raw_config = pj_m.group(1)
            config: dict = {}
            try:
                config = json.loads(raw_config)
            except Exception:
                try:
                    config = json.loads(_fix_js_obj(raw_config))
                except Exception:
                    return result

            file_data = config.get("file", "")
            if file_data:
                files: list = []
                try:
                    files = json.loads(file_data)
                except Exception:
                    if isinstance(file_data, str):
                        files = [{"file": file_data}]

                if isinstance(files, list):
                    for f in files:
                        url = f.get("file", "") if isinstance(f, dict) else ""
                        if url:
                            result.streams.append(Stream(
                                url=url,
                                type="m3u8" if ".m3u8" in url else "mp4",
                                server="R-027 CineMacity",
                                quality=f.get("title") if isinstance(f, dict) else None,
                                playback_headers={"Referer": f"{_API}/"},
                            ))

            # Subtitles: "[EN]https://...vtt,[RU]https://..."
            subs_raw = config.get("subtitle", "")
            if subs_raw:
                for part in subs_raw.split(","):
                    m = re.match(r"\[([^\]]+)\](.*)", part.strip())
                    if m:
                        result.subtitles.append(Subtitle(
                            language=m.group(1),
                            url=m.group(2).strip(),
                        ))
        except Exception:
            pass
        return result
