"""
ENGINE/providers/Stream/R_020/R_020.py — 4KHdHub (Indian 4K)

/?s=<title> -> div.card-grid > a.movie-card -> content page
Movie: div.download-item a -> getRedirectLinks -> stream
TV:    div.episode-download-item matching S##E## -> div.episode-links > a

Type: m3u8 | mp4
Flow:
  1. cf_get search page -> match movie card by title/year
  2. cf_get detail page -> scrape download or episode links
  3. get_redirect_link / bypass_hrefli -> cf_get -> extract_media_urls -> Stream objects

Ported from Streamplay's FourKHdHubProvider.
"""
from __future__ import annotations

import re

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.domains import get_domain
from ENGINE.tools.bypass import get_redirect_link, bypass_hrefli
from ENGINE.tools.scraper import parse, cf_get, extract_media_urls


def _pad2(n: int | None) -> str:
    if n is None:
        return "0"
    return f"0{n}" if n < 10 else str(n)


class R020Provider(Provider):
    id = "R-020"
    name = "4KHdHub"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            domain = await get_domain("n4khdhub")
            if not domain:
                return result
            query = (data.title or "").strip()
            if not query:
                return result

            search_html = await cf_get(f"{domain}/?s={query}")
            if not search_html:
                return result

            soup = parse(search_html)
            norm_title = query.lower().strip()
            year_str = str(data.year) if data.year else None

            cards = soup.select("div.card-grid > a.movie-card")
            matched = None
            for card in cards:
                c = card.get_text("", strip=True).lower()
                if norm_title in c and (year_str is None or year_str in c):
                    matched = card
                    break
            if not matched:
                for card in cards:
                    if norm_title in card.get_text("", strip=True).lower():
                        matched = card
                        break
            if not matched:
                return result

            link = matched.get("href") or ""
            url = link if link.startswith("http") else f"{domain}{link}"
            detail_html = await cf_get(url)
            if not detail_html:
                return result

            dsoup = parse(detail_html)
            hrefs: set[str] = set()

            if data.season is None:
                for a in dsoup.select("div.download-item a"):
                    h = a.get("href") or ""
                    if h:
                        hrefs.add(h)
            else:
                s_text = f"S{_pad2(data.season)}"
                e_text = f"E{_pad2(data.episode)}" if data.episode is not None else None
                for el in dsoup.select("div.episode-download-item"):
                    text = el.get_text()
                    if s_text.lower() in text.lower() and (e_text is None or e_text.lower() in text.lower()):
                        for a in el.select("div.episode-links > a"):
                            h = a.get("href") or ""
                            if h:
                                hrefs.add(h)

            for href in hrefs:
                source = (await get_redirect_link(href)) or (await bypass_hrefli(href)) or href
                source_html = await cf_get(source)
                if source_html:
                    for media_url in extract_media_urls(source_html):
                        result.streams.append(Stream(
                            url=media_url,
                            type="m3u8" if ".m3u8" in media_url else "mp4",
                            server="R-020 4KHdHub",
                            playback_headers={},
                        ))
        except Exception:
            pass
        return result
