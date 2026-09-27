"""
ENGINE/providers/Stream/R-014/R_014.py — AniZone (anime only)

Flow:
  1. GET /anime?search=<title> -> candidate /anime/<id> links
  2. For top candidates, GET /anime/<id>/<episode> -> <media-player src> + <track> subtitles
  3. Season-score and pick best match

Ported from Streamplay's AniZoneProvider.
"""
from __future__ import annotations

import asyncio
import re

from ENGINE.providers.base import Provider, LinkData, Result, Stream, Subtitle
from ENGINE.tools.http import get_client, UA
from ENGINE.tools.scraper import parse, cf_get

_BASE = "https://anizone.to"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _season_score(title: str, want_season: int, want_year: int | None) -> int:
    t = title.lower()
    score = 0
    m = re.search(r"season\s*(\d+)", t)
    if m:
        found = int(m.group(1))
        score += 3 if found == want_season else -2
    elif want_season == 1:
        score += 1  # no season tag usually means season 1
    return score


# cf_get from scraper handles plain HTTP -> FlareSolverr fallback automatically


def _anime_title_of_ep_page(html: str) -> str:
    m = re.search(r"<title>([^<]*)</title>", html, re.I)
    if not m:
        return ""
    return re.sub(r"—\s*AniZone\s*$", "", re.sub(r"^\s*Episode\s+\d+\s*[-–]\s*", "", m.group(1), flags=re.I), flags=re.I).strip()


def _overlaps(a: str, b: str) -> bool:
    ta = {w for w in _norm(a).split() if len(w) >= 3}
    tb = {w for w in _norm(b).split() if len(w) >= 3}
    return bool(ta & tb)


class R014Provider(Provider):
    id = "R-014"
    name = "AniZone"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        if not data.is_anime or not data.title:
            return result

        episode = 1 if data.type == "movie" else (data.episode or 1)
        want_season = None if data.type == "movie" else (data.season or 1)

        try:
            queries = [data.title]
            if data.org_title and data.org_title != data.title:
                queries.append(data.org_title)

            candidates: list[str] = []
            for q in queries:
                html = await cf_get(f"{_BASE}/anime?search={q}")
                if not html:
                    continue
                soup = parse(html)
                for a in soup.select('a[href*="/anime/"]'):
                    href = a.get("href", "")
                    if not re.search(r"/anime/[a-z0-9]+$", href, re.I):
                        continue
                    u = href if href.startswith("http") else f"{_BASE}{href}"
                    if u not in candidates:
                        candidates.append(u)
                if candidates:
                    break

            if not candidates:
                return result

            want_str = f"{data.title} {data.org_title or ''}"

            async def probe(anime_href: str) -> dict | None:
                watch_html = await cf_get(f"{anime_href}/{episode}")
                if not watch_html:
                    return None
                ws = parse(watch_html)
                mp = ws.find("media-player")
                src = mp.get("src") if mp else None
                if not src:
                    return None
                name = _anime_title_of_ep_page(watch_html)
                s = (_season_score(name, want_season, data.year) if want_season else 0) + (1 if _overlaps(name, want_str) else 0)
                return {"src": src, "html": watch_html, "score": s}

            probed = await asyncio.gather(*[probe(c) for c in candidates[:8]])
            scored = [p for p in probed if p]
            if not scored:
                return result

            chosen = max(scored, key=lambda p: p["score"])
            ws = parse(chosen["html"])
            for track in ws.select('track[kind="subtitles"]'):
                track_url = track.get("src")
                label = track.get("label") or track.get("srclang")
                if track_url and label:
                    abs_url = track_url if track_url.startswith("http") else f"{_BASE}{track_url}"
                    result.subtitles.append(Subtitle(url=abs_url, language=label))

            src = chosen["src"]
            result.streams.append(Stream(
                url=src,
                type="mp4" if src.endswith(".mp4") else "m3u8",
                server="R-014 AniZone",
                headers={"Referer": f"{_BASE}/"},
                playback_headers={"Referer": f"{_BASE}/"},
            ))
        except Exception:
            pass
        return result
