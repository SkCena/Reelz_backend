"""
ENGINE/providers/Subtitle/R_201/R_201.py — Subscene

Type: scraper (Cloudflare-protected — uses FlareSolverr)
Flow:
  1. Solve CF challenge on the search page via fetch_soup_cf
  2. Pick the first matching movie result
  3. Solve CF challenge on the movie subtitle listing page
  4. Return English subtitle download paths as Subtitle objects

Requires: FLARESOLVERR_URL set in .env
"""
from __future__ import annotations

from ENGINE.providers.base import Provider, LinkData, Result, Subtitle
from ENGINE.tools.scraper import fetch_soup_cf

_BASE = "https://subscene.com"


class R201Provider(Provider):
    id = "R-201"
    name = "Subscene"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            # Step 1 — search (CF-protected)
            search_url = f"{_BASE}/subtitles/search?query={data.title.replace(' ', '+')}"
            soup = await fetch_soup_cf(search_url)
            if soup is None:
                return result

            # Step 2 — pick first movie result
            movie_path: str | None = None
            for a in soup.select(".search-result .title a"):
                href = a.get("href", "")
                if href:
                    movie_path = href
                    break

            if not movie_path:
                return result

            # Step 3 — movie subtitle listing (CF-protected)
            movie_url = f"{_BASE}{movie_path}"
            msoup = await fetch_soup_cf(movie_url)
            if msoup is None:
                return result

            # Step 4 — collect English subtitle rows
            for row in msoup.select(".table tbody tr"):
                cols = row.select("td")
                if len(cols) < 2:
                    continue
                lang_span = cols[0].select_one("span")
                if not lang_span or lang_span.get_text(strip=True).lower() != "english":
                    continue
                link = cols[1].select_one("a")
                if not link:
                    continue
                dl_path = link.get("href", "")
                release_name = link.get_text(strip=True)
                if not dl_path:
                    continue
                result.subtitles.append(Subtitle(
                    url=f"{_BASE}{dl_path}",
                    language="en",
                    label=release_name or "Subscene",
                    format="srt",
                    playback_headers={},
                ))
                if len(result.subtitles) >= 10:
                    break
        except Exception:
            pass
        return result
