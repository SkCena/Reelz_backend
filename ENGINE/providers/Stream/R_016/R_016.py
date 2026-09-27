"""
ENGINE/providers/Stream/R-016/R-016.py — AnimeNoSub (anime only)

DooPlay WordPress site, plain HTTP.
Flow:
  1. /?s=<title>              -> /anime/<slug>/ cards
  2. /anime/<slug>/           -> episode link matching /-episode-<N>/
  3. Episode page             -> megaplay.buzz iframe -> /stream/getSources?id=<data-id>
                                  -> { sources: { file }, tracks: [{file,.vtt,label}] }
  DUB: swap /sub -> /dub in megaplay URL.

Headers required:
  - Page fetches:    User-Agent: standard browser UA
  - megaplay GET:    Referer: https://animenosub.to/   User-Agent: standard browser UA
  - getSources GET:  Referer: <megaplay_origin>/   X-Requested-With: XMLHttpRequest
  - Playback m3u8:   Referer: <megaplay_origin>/   Origin: <megaplay_origin>

Ported from Streamplay's AnimeNoSubProvider.
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urlparse

from ENGINE.providers.base import Provider, LinkData, Result, Stream, Subtitle
from ENGINE.tools.http import get_client, UA

_BASE = "https://animenosub.to"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _season_score(title: str, want_season: int) -> int:
    t = title.lower()
    m = re.search(r"season\s*(\d+)", t)
    if m:
        return 3 if int(m.group(1)) == want_season else -2
    return 1 if want_season == 1 else 0


async def _resolve_megaplay(embed_url: str) -> tuple[str | None, list[Subtitle], str]:
    """
    Returns (m3u8_url_or_None, subtitles, megaplay_origin).
    megaplay_origin is derived from embed_url and needed for playback headers.
    """
    subs: list[Subtitle] = []
    parsed = urlparse(embed_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    try:
        client = await get_client()
        page = (await client.get(
            embed_url,
            headers={"User-Agent": UA, "Referer": f"{_BASE}/"},
            timeout=15,
        )).text
        id_m = re.search(r'data-id="(\d+)"', page)
        if not id_m:
            return None, subs, origin
        data_id = id_m.group(1)
        r = (await client.get(
            f"{origin}/stream/getSources?id={data_id}",
            headers={
                "User-Agent": UA,
                "Referer": f"{origin}/",
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=15,
        )).json()
        for t in (r.get("tracks") or []):
            if t.get("kind") == "captions" and t.get("file"):
                subs.append(Subtitle(url=t["file"], language=t.get("label") or "Sub"))
        return (r.get("sources") or {}).get("file"), subs, origin
    except Exception:
        return None, subs, origin


class R016Provider(Provider):
    id = "R-016"
    name = "AnimeNoSub"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        if not data.is_anime or not data.title:
            return result

        episode = 1 if data.type == "movie" else (data.episode or 1)
        want_season = None if data.type == "movie" else (data.season or 1)

        try:
            client = await get_client()
            headers = {"User-Agent": UA}

            queries = [data.title]
            if data.org_title and data.org_title != data.title:
                queries.append(data.org_title)

            cards: list[dict] = []
            for q in queries:
                html = (await client.get(f"{_BASE}/?s={q}", headers=headers, timeout=15)).text
                from ENGINE.tools.scraper import parse
                soup = parse(html)
                seen: set[str] = set()
                for a in soup.select('a[href*="/anime/"]'):
                    url = (a.get("href") or "").split("#")[0]
                    m = re.search(r"/anime/([^/]+)/?$", url)
                    if not m:
                        continue
                    slug = m.group(1)
                    if slug in seen:
                        continue
                    title = (a.get("title") or a.find("img", alt=True) and a.find("img")["alt"]  # type: ignore
                             or a.get_text(strip=True) or slug)
                    seen.add(slug)
                    full_url = url if url.startswith("http") else f"{_BASE}{url}"
                    cards.append({"slug": slug, "url": full_url, "title": str(title)})
                if cards:
                    break

            if not cards:
                return result

            want = _norm(data.title)
            want_org = _norm(data.org_title or "")
            extra_re = re.compile(r"\b(movie|film|arc|compilation|ova|special|recap)\b", re.I)

            def _score(c: dict) -> float:
                t = _norm(c["title"])
                s = (4 if t == want or (want_org and t == want_org)
                     else 1 if (want in t or t in want or (want_org and (want_org in t or t in want_org))) else 0)
                if s > 0:
                    if data.type != "movie" and extra_re.search(c["title"]):
                        s -= 2
                    if want_season:
                        s += _season_score(c["title"], want_season)
                return s

            candidates = sorted([c for c in cards if _score(c) > 0], key=_score, reverse=True)
            if not candidates:
                return result

            for pick in candidates[:5]:
                anime_html = (await client.get(
                    pick["url"],
                    headers={"User-Agent": UA, "Referer": f"{_BASE}/"},
                    timeout=15,
                )).text
                from ENGINE.tools.scraper import parse
                asoup = parse(anime_html)
                ep_url = ""
                for a in asoup.select('a[href*="-episode-"]'):
                    href = (a.get("href") or "").split("#")[0]
                    n_m = re.search(r"-episode-(\d+)", href)
                    if n_m and int(n_m.group(1)) == episode and not ep_url:
                        ep_url = href if href.startswith("http") else f"{_BASE}{href}"
                if not ep_url:
                    continue

                ep_html = (await client.get(
                    ep_url,
                    headers={"User-Agent": UA, "Referer": pick["url"]},
                    timeout=15,
                )).text
                iframe_m = re.search(r'<iframe[^>]+(?:data-src|src)="([^"]+)"', ep_html, re.I)
                if not iframe_m:
                    continue
                iframe_src = iframe_m.group(1)
                embed = iframe_src if iframe_src.startswith("http") else f"https:{iframe_src.lstrip('/')}"

                if re.search(r"megaplay\.", embed, re.I):
                    sub_url = re.sub(r"/(sub|dub)/?$", "/sub", embed)
                    dub_url = re.sub(r"/(sub|dub)/?$", "/dub", embed)
                    variants = [{"url": sub_url, "dub": False}]
                    if dub_url != sub_url:
                        variants.append({"url": dub_url, "dub": True})

                    async def resolve_variant(v: dict) -> None:
                        # origin is derived inside _resolve_megaplay and returned
                        # so it is always in scope for the playback_headers below
                        m3u8, subs, mp_origin = await _resolve_megaplay(v["url"])
                        for sub in subs:
                            if not any(s.url == sub.url for s in result.subtitles):
                                result.subtitles.append(sub)
                        if m3u8 and not any(s.url == m3u8 for s in result.streams):
                            result.streams.append(Stream(
                                url=m3u8,
                                type="m3u8",
                                server=f"R-016 AnimeNoSub {'DUB' if v['dub'] else 'SUB'}",
                                # CDN validates Referer + Origin from the megaplay embed domain
                                headers={"Referer": f"{mp_origin}/", "Origin": mp_origin},
                                playback_headers={"Referer": f"{mp_origin}/", "Origin": mp_origin},
                            ))

                    await asyncio.gather(*[resolve_variant(v) for v in variants])

                if result.streams:
                    break
        except Exception:
            pass
        return result
