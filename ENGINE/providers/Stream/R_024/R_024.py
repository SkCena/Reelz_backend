"""
ENGINE/providers/Stream/R_024/R_024.py — UhdMovies (Indian UHD)

/search/<title year> -> first article -> detail page -> per-quality links
-> driveleech/driveseed JS redirect OR href.li bypass -> stream.

Type: mp4 | m3u8
Flow:
  1. cf_get search page -> first article link
  2. cf_get detail page -> quality headings -> per-quality download links
  3. Follow driveleech/driveseed JS redirect or bypass_hrefli
  4. cf_get final link -> extract_media_urls -> Stream objects

Ported from Streamplay's UhdMoviesProvider.
"""
from __future__ import annotations

import re

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.domains import get_domain
from ENGINE.tools.bypass import bypass_hrefli, get_redirect_link, get_base_url
from ENGINE.tools.scraper import parse, cf_get, extract_media_urls

_JS_REDIRECT_RE = re.compile(r"window\.location\.replace\([\"'](.*?)[\"']\)")


class R024Provider(Provider):
    id = "R-024"
    name = "UhdMovies"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            api = await get_domain("uhdmovies")
            if not api:
                return result
            query = re.sub(r"[-:]", " ", data.title or "").strip()
            if not query:
                return result

            search_html = await cf_get(f"{api}/search/{query} {data.year or ''}".strip())
            if not search_html:
                return result

            ssoup = parse(search_html)
            page_link = ssoup.select_one("article div.entry-image a")
            if not page_link:
                return result
            page_url = page_link.get("href") or ""
            if not page_url.startswith("http"):
                page_url = api + page_url

            detail_html = await cf_get(page_url)
            if not detail_html:
                return result
            dsoup = parse(detail_html)

            season_re = (
                re.compile(rf"(S0?{data.season}|Season 0?{data.season})", re.I)
                if data.season is not None
                else re.compile(str(data.year or ""))
            )
            ep_re = (
                re.compile(rf"Episode {data.episode}", re.I)
                if data.season is not None
                else re.compile(r"Download", re.I)
            )

            links: list[str] = []
            for p in dsoup.select("div.entry-content p"):
                if not season_re.search(p.get_text()):
                    continue
                sib = p.find_next_sibling()
                if not sib:
                    continue
                for a in sib.select("a"):
                    if ep_re.search(a.get_text()):
                        h = a.get("href")
                        if h and h not in links:
                            links.append(h)

            for link in links:
                drive_link: str | None = None
                try:
                    if re.search(r"driveleech|driveseed", link, re.I):
                        text = await cf_get(link)
                        if text:
                            m = _JS_REDIRECT_RE.search(text)
                            if m:
                                drive_link = get_base_url(link) + m.group(1)
                    else:
                        drive_link = await bypass_hrefli(link)
                except Exception:
                    continue
                if not drive_link:
                    continue
                drive_html = await cf_get(drive_link)
                if drive_html:
                    for url in extract_media_urls(drive_html):
                        result.streams.append(Stream(
                            url=url,
                            type="m3u8" if ".m3u8" in url else "mp4",
                            server="R-024 UhdMovies",
                            playback_headers={},
                        ))
        except Exception:
            pass
        return result
