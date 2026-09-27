"""
ENGINE/providers/Stream/R_025/R_025.py — Moviesmod (Indian content)

/search/<imdbId> -> article -> detail page -> quality headings -> intermediate page
-> maxbutton/episode link -> bypassHrefli -> stream.
Requires imdb_id.

Type: mp4 | m3u8
Flow:
  1. cf_get search by imdb_id -> first article link
  2. cf_get detail page -> heading regex -> download/episode anchor
  3. cf_get intermediate page -> maxbutton or episode-N link
  4. bypass_hrefli -> cf_get -> extract_media_urls -> Stream objects

Ported from Streamplay's MoviesModProvider.
"""
from __future__ import annotations

import re

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.domains import get_domain
from ENGINE.tools.bypass import bypass_hrefli
from ENGINE.tools.scraper import parse, cf_get, extract_media_urls


def _pad2(n: int) -> str:
    return f"0{n}" if n < 10 else str(n)


class R025Provider(Provider):
    id = "R-025"
    name = "Moviesmod"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            api = await get_domain("moviesmod")
            if not api or not data.imdb_id:
                return result

            season = data.season
            episode = data.episode

            search_url = (
                f"{api}/search/{data.imdb_id}"
                if season is None
                else f"{api}/search/{data.imdb_id} {season}"
            )
            search_html = await cf_get(search_url)
            if not search_html:
                return result

            ssoup = parse(search_html)
            article_a = ssoup.select_one("#content_box article > a")
            if not article_a:
                return result
            href = article_a.get("href") or ""

            htag = "h4" if season is None else "h3"
            atag = "Download" if season is None else "Episode"
            stag = "" if season is None else rf"(S{_pad2(season)}|Season {season})"
            heading_re = re.compile(rf"{stag}.*(480p|720p|1080p|2160p)", re.I)

            detail_html = await cf_get(href)
            if not detail_html:
                return result
            dsoup = parse(detail_html)

            for h_el in dsoup.select(f"div.thecontent {htag}"):
                text = h_el.get_text()
                if not heading_re.search(text) or re.search(r"moviesmod", text, re.I):
                    continue
                sib = h_el.find_next_sibling()
                link = ""
                if sib:
                    for a in sib.select("a"):
                        if atag in a.get_text():
                            raw_href = a.get("href") or ""
                            link = raw_href[raw_href.index("=") + 1:] if "=" in raw_href else raw_href
                            break
                if not link:
                    continue

                intermediate_html = await cf_get(link)
                if not intermediate_html:
                    continue
                isoup = parse(intermediate_html)

                source: str | None = None
                if season is None:
                    a_el = isoup.select_one("p a.maxbutton")
                    source = a_el.get("href") if a_el else None
                else:
                    ep_re = re.compile(rf"Episode {episode}", re.I)
                    for a in isoup.select("h3 a"):
                        if ep_re.search(a.get_text()):
                            source = a.get("href")
                            break

                if not source:
                    continue

                bypassed = await bypass_hrefli(source)
                if not bypassed:
                    continue
                bypassed_html = await cf_get(bypassed)
                if bypassed_html:
                    for url in extract_media_urls(bypassed_html):
                        result.streams.append(Stream(
                            url=url,
                            type="m3u8" if ".m3u8" in url else "mp4",
                            server="R-025 Moviesmod",
                            playback_headers={},
                        ))
        except Exception:
            pass
        return result
