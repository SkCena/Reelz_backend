"""
ENGINE/providers/Stream/R_018/R_018.py — VegaMovies (Indian content)

JSON search by IMDB id or title -> permalink -> V-Cloud/G-Direct buttons -> stream.
Requires imdb_id in LinkData for reliable matching.
Uses WARP for Cloudflare-protected pages.

Type: m3u8 | mp4
Flow:
  1. Search /search.php?q=<imdb_id|title> -> JSON hits -> match permalink
  2. Fetch permalink HTML -> find V-Cloud/G-Direct buttons -> hosted pages
  3. cf_get each hosted page -> extract_media_urls -> Stream objects

Ported from Streamplay's VegaMoviesProvider.
"""
from __future__ import annotations

import json
import re

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA
from ENGINE.tools.domains import get_domain
from ENGINE.tools.bypass import bypass_hrefli
from ENGINE.tools.scraper import parse, cf_get, extract_media_urls

_VEGA_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    "cookie": "xla=s4t",
}


class R018Provider(Provider):
    id = "R-018"
    name = "VegaMovies"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            api = await get_domain("vegamovies")
            if not api:
                return result
            # Try IMDB ID first, fall back to title search (don't require imdb_id)

            # 1) Search
            async def fetch_docs(query: str) -> list[dict]:
                html = await cf_get(f"{api}/search.php?q={query}", referer=api, extra_headers=_VEGA_HEADERS)
                if not html:
                    return []
                try:
                    j = json.loads(html)
                    return [(h.get("document") or {}) for h in (j.get("hits") or [])]
                except Exception:
                    return []

            docs = []
            match = None
            if data.imdb_id:
                docs = await fetch_docs(data.imdb_id)
                match = next((d for d in docs if (d.get("imdb_id") or "").lower() == data.imdb_id.lower()), None)
            if not match and data.title:
                docs = await fetch_docs(data.title)
                tl = data.title.lower()
                match = next((d for d in docs if tl in (d.get("post_title") or "").lower()), None) or (docs[0] if docs else None)
            if not match or not match.get("permalink"):
                return result

            main_html = await cf_get(api + match["permalink"], referer=api, extra_headers=_VEGA_HEADERS)
            if not main_html:
                return result

            async def push_hosted(source: str) -> None:
                """Follow a V-Cloud or G-Direct link, extract stream URLs."""
                try:
                    page_html = await cf_get(source, referer=api, extra_headers=_VEGA_HEADERS)
                    if not page_html:
                        return
                    for url in extract_media_urls(page_html):
                        result.streams.append(Stream(
                            url=url,
                            type="m3u8" if ".m3u8" in url else "mp4",
                            server="R-018 VegaMovies",
                        ))
                except Exception:
                    pass

            soup = parse(main_html)

            if data.season is None:
                pages: set[str] = set()
                for btn in soup.select("button.dwd-button"):
                    h = btn.parent.get("href") if btn.parent else None
                    if h:
                        pages.add(h)
                for page in pages:
                    page_html = await cf_get(page, referer=api, extra_headers=_VEGA_HEADERS)
                    if not page_html:
                        continue
                    ps = parse(page_html)
                    for btn in ps.select("button.btn"):
                        if re.search(r"V-Cloud", btn.get_text(), re.I):
                            h = btn.parent.get("href") if btn.parent else None
                            if h:
                                await push_hosted(h)
            else:
                season_re = re.compile(rf"Season {data.season}", re.I)
                link_re = re.compile(r"(V-Cloud|Single|Episode)", re.I)
                pages_tv: set[str] = set()
                for tag in soup.select("h3,h5"):
                    if not season_re.search(tag.get_text()):
                        continue
                    sib = tag.find_next_sibling()
                    while sib and sib.name not in ("h3", "h5", "h4"):
                        for a in sib.select("a"):
                            if link_re.search(a.get_text()):
                                h = a.get("href")
                                if h:
                                    pages_tv.add(h)
                        sib = sib.find_next_sibling()

                ep_re = re.compile(rf"Episodes?\s*:\s*{data.episode}", re.I)
                for page in pages_tv:
                    page_html = await cf_get(page, referer=api, extra_headers=_VEGA_HEADERS)
                    if not page_html:
                        continue
                    ps = parse(page_html)
                    for h4 in ps.select("h4"):
                        if ep_re.search(h4.get_text()):
                            sib = h4.find_next_sibling()
                            while sib and sib.name not in ("h4",):
                                for a in sib.select("a"):
                                    h = a.get("href")
                                    if h:
                                        await push_hosted(h)
                                sib = sib.find_next_sibling()
                            break
        except Exception:
            pass
        return result
