"""
ENGINE/providers/Subtitle/R_204/R_204.py — YIFY Subtitles

Type: scraper (Cloudflare-protected — uses FlareSolverr)
Flow:
  1. Solve CF challenge on the search page via fetch_soup_cf
  2. Pick the first movie result slug
  3. Solve CF challenge on the movie subtitle page
  4. Return English subtitle download URLs as Subtitle objects

Requires: FLARESOLVERR_URL set in .env
"""
from __future__ import annotations

from ENGINE.providers.base import Provider, LinkData, Result, Subtitle
from ENGINE.tools.scraper import fetch_soup_cf

_BASE = "https://www.yifysubtitles.com"


class R204Provider(Provider):
    id = "R-204"
    name = "YIFY"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            # Step 1 — search (CF-protected)
            search_url = f"{_BASE}/search?q={data.title.replace(' ', '+')}"
            soup = await fetch_soup_cf(search_url)
            if soup is None:
                return result

            # Step 2 — pick first movie match
            movie_href: str | None = None
            for a in soup.select(".movie-item a"):
                href = a.get("href", "")
                if href:
                    movie_href = href
                    break

            if not movie_href:
                return result

            # Step 3 — movie subtitle page (CF-protected)
            if movie_href.startswith("http"):
                movie_url = movie_href
            elif movie_href.startswith("/"):
                movie_url = f"{_BASE}{movie_href}"
            else:
                movie_url = f"{_BASE}/movies/{movie_href.strip('/')}"

            msoup = await fetch_soup_cf(movie_url)
            if msoup is None:
                return result

            # Step 4 — find English rows in subtitle table
            for row in msoup.select(".subtitle-table tbody tr"):
                lang_cell = row.select_one(".language")
                lang = lang_cell.get_text(strip=True).lower() if lang_cell else ""
                if "english" not in lang:
                    continue
                quality_cell = row.select_one(".quality")
                quality = quality_cell.get_text(strip=True) if quality_cell else ""
                dl_cell = row.select_one(".download")
                dl_id = dl_cell.get("data-id", "") if dl_cell else ""
                if dl_id:
                    result.subtitles.append(Subtitle(
                        url=f"{_BASE}/download/{dl_id}",
                        language="en",
                        label=quality or "YIFY",
                        format="srt",
                        playback_headers={},
                    ))
                else:
                    # fallback: any download anchor in the row
                    a = row.select_one('a[href*="/download"]')
                    if a:
                        href = a.get("href", "")
                        result.subtitles.append(Subtitle(
                            url=f"{_BASE}{href}" if href.startswith("/") else href,
                            language="en",
                            label=quality or "YIFY",
                            format="srt",
                            playback_headers={},
                        ))
                if len(result.subtitles) >= 10:
                    break
        except Exception:
            pass
        return result
