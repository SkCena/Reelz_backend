"""
ENGINE/providers/Stream/R_036/R_036.py — Bollyflix (Indian/Hindi content)

Search bollyflix for movies/series -> download page -> resolve links -> streams.
Ported from Megix's CloudStream BollyflixProvider (Kotlin).

Type: mp4 | m3u8
Flow:
  1. GET {base}/search/{query}/page/1/ -> parse div.post-cards > article
  2. Match by title/imdb -> movie page URL
  3. Find a.dl download buttons -> resolve ?id= via bypass
  4. Extract media URLs from file host pages

Note: Bollyflix uses Cloudflare; uses cf_get for bypass.
"""
from __future__ import annotations

import base64
import re

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA
from ENGINE.tools.scraper import parse, cf_get, extract_media_urls

# Dynamic domain (same source as the CloudStream plugin)
_URLS_JSON = "https://raw.githubusercontent.com/SaurabhKaperwan/Utils/refs/heads/main/urls.json"
_FALLBACK_BASE = "https://bollyflix.frl"

_BYPASS_API = "https://web.sidexfee.com/?id="

_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": UA,
}


async def _get_base() -> str:
    """Fetch current Bollyflix domain, fallback to hardcoded."""
    try:
        client = await get_client()
        r = await client.get(_URLS_JSON, timeout=10)
        if r.status_code == 200:
            j = r.json()
            url = (j.get("bollyflix") or "").strip()
            if url:
                return url.rstrip("/")
    except Exception:
        pass
    return _FALLBACK_BASE


async def _bypass(link: str) -> str:
    """Resolve ?id= links via sidexfee bypass API."""
    try:
        if "?id=" not in link or "fastdlserver" in link:
            return link
        _id = link.split("id=")[-1].split("&")[0]
        client = await get_client()
        r = await client.get(f"{_BYPASS_API}{_id}", timeout=15)
        if r.status_code == 200:
            m = re.search(r'"link"\s*:\s*"([^"]+)"', r.text)
            if m:
                encoded = m.group(1).replace("\\/", "/")
                try:
                    return base64.b64decode(encoded).decode("utf-8", "ignore")
                except Exception:
                    pass
    except Exception:
        pass
    return link


class R036Provider(Provider):
    id = "R-036"
    name = "Bollyflix"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            base = await _get_base()
            if not data.title:
                return result

            # 1) Search
            query = data.title.strip().replace(" ", "+")
            search_url = f"{base}/search/{query}/page/1/"
            html = await cf_get(search_url, referer=base, extra_headers=_HEADERS)
            if not html:
                return result

            doc = parse(html)
            articles = doc.select("div.post-cards > article")
            if not articles:
                return result

            # 2) Match best result
            title_lower = data.title.lower()
            year_str = str(data.year) if data.year else ""
            best_href = None
            for art in articles:
                a = art.select_one("a")
                if not a:
                    continue
                art_title = (a.get("title") or "").replace("Download ", "").lower()
                href = a.get("href") or ""
                if not href:
                    continue
                # Prefer title match
                if title_lower in art_title or art_title in title_lower:
                    if year_str and year_str in art_title:
                        best_href = href
                        break
                    if not best_href:
                        best_href = href
            if not best_href:
                # Fallback to first result
                a = articles[0].select_one("a")
                best_href = a.get("href") if a else None
            if not best_href:
                return result

            # 3) Load movie page, find download buttons
            page_html = await cf_get(best_href, referer=base, extra_headers=_HEADERS)
            if not page_html:
                return result

            page_doc = parse(page_html)
            dl_buttons = page_doc.select("a.dl")
            if not dl_buttons:
                # Try alternative selectors
                dl_buttons = page_doc.select("a.maxbutton-download-links, a.btnn")

            for btn in dl_buttons[:6]:  # Limit to 6 to avoid overload
                try:
                    link = btn.get("href") or ""
                    if not link:
                        continue
                    resolved = await _bypass(link)
                    if not resolved or resolved == link and "?id=" in link:
                        continue

                    # 4) Extract streams from the file host page
                    host_html = await cf_get(resolved, referer=base, extra_headers=_HEADERS)
                    if not host_html:
                        # Try direct as stream URL
                        if any(x in resolved for x in (".mp4", ".m3u8", ".mkv")):
                            result.streams.append(Stream(
                                url=resolved,
                                type="m3u8" if ".m3u8" in resolved else "mp4",
                                language="Hindi",
                                quality=self._guess_quality(btn.text()),
                            ))
                        continue

                    for url in extract_media_urls(host_html):
                        result.streams.append(Stream(
                            url=url,
                            type="m3u8" if ".m3u8" in url else "mp4",
                            language="Hindi",
                            quality=self._guess_quality(btn.text()),
                        ))
                except Exception:
                    continue

        except Exception:
            pass
        return result

    def _guess_quality(self, text: str) -> str:
        """Extract quality label from button text."""
        t = (text or "").upper()
        for q in ("2160P", "1080P", "720P", "480P", "360P", "4K"):
            if q in t:
                return q.replace("P", "p") if q != "4K" else "4K"
        if "HD" in t:
            return "HD"
        if "CAM" in t:
            return "CAM"
        return ""
