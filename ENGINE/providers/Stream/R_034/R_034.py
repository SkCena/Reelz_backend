"""
ENGINE/providers/Stream/R_034/R_034.py — 4KHDHub Hindi (Indian 4K with Hindi dubbed)

Uses the HubCloud resolver for Hindi+English dual audio content.
/?s=<title> -> div.card-grid > a.movie-card -> detail page
-> div.download-item a -> HubCloud chain -> direct R2/storage URLs

Type: mkv | mp4
Flow:
  1. Search by title -> match movie card by title/year
  2. Fetch detail page -> scrape download links
  3. Decode redirect -> HubCloud URL -> resolve to direct streams

Note: These are typically Hindi dubbed or Hindi+English dual audio releases.
"""
from __future__ import annotations

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.domains import get_domain
from ENGINE.tools.hubcloud_resolver import resolve_4khdhub_movie


class R034Provider(Provider):
    id = "R-034"
    name = "4KHDHub Hindi"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            domain = await get_domain("n4khdhub")
            if not domain:
                return result

            title = (data.title or "").strip()
            if not title:
                return result

            streams = await resolve_4khdhub_movie(domain, title, data.year)

            for s in streams:
                result.streams.append(Stream(
                    url=s["url"],
                    type="mkv" if ".mkv" in s["url"].lower() else "mp4",
                    quality=s.get("quality", "HD"),
                    server=f"R-034 4KHDHub [{s.get('server', 'FSL')}]",
                    playback_headers={},
                ))

        except Exception:
            pass
        return result
