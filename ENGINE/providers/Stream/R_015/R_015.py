"""
ENGINE/providers/Stream/R-015/R_015.py — AniNeko (anime only)

SUB / DUB via packed-JS embed players (playmogo/vivibebe/otakuvid).
Uses FlareSolverr (CF-gated site).
Flow:
  1. /browser?keyword=<title> -> .nv-anime-card -> slug
  2. /watch/<slug>/ep-<N>     -> [data-video] embeds (sub|dub)
  3. Resolve each embed -> Dean-Edwards packed m3u8

Ported from Streamplay's AniNekoProvider.
"""
from __future__ import annotations

import asyncio
import re

from ENGINE.providers.base import Provider, LinkData, Result, Stream, Subtitle
from ENGINE.tools.http import get_client, UA
from ENGINE.tools.scraper import parse, fetch_soup_cf

_BASE = "https://anineko.to"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _season_score(title: str, want_season: int) -> int:
    t = title.lower()
    m = re.search(r"season\s*(\d+)", t)
    if m:
        return 3 if int(m.group(1)) == want_season else -2
    return 1 if want_season == 1 else 0


def _get_and_unpack(js: str) -> str:
    """Minimal Dean-Edwards p,a,c,k,e,d unpacker."""
    import base64
    m = re.search(r"eval\(function\(p,a,c,k,e,[rd]\)", js)
    if not m:
        return js
    try:
        packed = js[m.start():]
        # Extract the packed string via a simple regex approach
        parts_m = re.search(r"'([^']+)'\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*'([^']+)'", packed)
        if not parts_m:
            return js
        encoded, base, count, keys_str = parts_m.groups()
        base = int(base)
        keys = keys_str.split("|")

        def decode_word(word: str) -> str:
            if not word:
                return word
            idx = 0
            for ch in word:
                idx = idx * base + (int(ch) if ch.isdigit() else ord(ch) - ord("a") + 10)
            return keys[idx] if idx < len(keys) and keys[idx] else word

        result = re.sub(r"\b\w+\b", lambda mo: decode_word(mo.group(0)), encoded)
        return result
    except Exception:
        return js


# fetch_soup_cf from scraper wraps FlareSolverr; _fetch_flare_html is a thin wrapper below
async def _fetch_flare_html(url: str) -> str | None:
    soup = await fetch_soup_cf(url)
    if soup is None:
        return None
    return str(soup)


async def _resolve_embed(embed_url: str) -> str | None:
    try:
        client = await get_client()
        r = await client.get(embed_url, headers={"User-Agent": UA, "Referer": f"{_BASE}/"}, timeout=15)
        body = _get_and_unpack(r.text)
        for pattern in [
            r'(?:file|source|src)\s*[=:]\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
            r'(https?://[^"\'\\s\\\\]+\.m3u8[^"\'\\s\\\\]*)',
        ]:
            m = re.search(pattern, body, re.I)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


class R015Provider(Provider):
    id = "R-015"
    name = "AniNeko"

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

            cards: list[dict] = []
            for q in queries:
                html = await _fetch_flare_html(f"{_BASE}/browser?keyword={q}")
                if not html:
                    continue
                soup = parse(html)
                seen: set[str] = set()
                for a in soup.select('a[href*="/watch/"]'):
                    href = a.get("href", "")
                    slug = (href.split("/watch/")[1] or "").split("?")[0].split("/")[0]
                    if not slug or slug in seen:
                        continue
                    card = a.find_parent(class_=re.compile(r"nv-anime-card|article|item")) or a
                    title = (a.get("title") or card.find("img", alt=True) and card.find("img")["alt"]  # type: ignore
                             or card.get_text(strip=True) or slug)
                    seen.add(slug)
                    cards.append({"slug": slug, "title": str(title)})
                if cards:
                    break

            if not cards:
                return result

            want = _norm(data.title)
            scored = sorted(cards, key=lambda c: (
                (3 if _norm(c["title"]) == want else 1 if want in _norm(c["title"]) or _norm(c["title"]) in want else 0)
                + (_season_score(c["title"], want_season) if want_season else 0)
            ), reverse=True)
            pick = scored[0] if scored else None
            if not pick:
                return result

            ep_html = await _fetch_flare_html(f"{_BASE}/watch/{pick['slug']}/ep-{episode}")
            if not ep_html:
                return result

            ep_soup = parse(ep_html)
            embeds: list[dict] = []
            seen_urls: set[str] = set()
            for el in ep_soup.select("[data-video]"):
                url = el.get("data-video", "")
                if not url.startswith("http") or url in seen_urls:
                    continue
                group = ((el.get("data-id") or "") + " " + el.get_text()).lower()
                seen_urls.add(url)
                embeds.append({"url": url, "dub": "dub" in group})

            if not embeds:
                return result

            # Resolve up to 2 sub + 2 dub
            sub_embeds = [e for e in embeds if not e["dub"]][:2]
            dub_embeds = [e for e in embeds if e["dub"]][:2]
            chosen = sub_embeds + dub_embeds

            async def resolve_one(e: dict) -> None:
                m3u8 = await _resolve_embed(e["url"])
                if m3u8 and not any(s.url == m3u8 for s in result.streams):
                    try:
                        host = re.search(r"https?://([^/]+)", e["url"]).group(1)  # type: ignore
                    except Exception:
                        host = "AniNeko"
                    result.streams.append(Stream(
                        url=m3u8,
                        type="m3u8",
                        server=f"R-015 AniNeko {'DUB' if e['dub'] else 'SUB'} · {host}",
                        headers={"Referer": f"{_BASE}/", "Origin": _BASE},
                        playback_headers={"Referer": f"{_BASE}/", "Origin": _BASE},
                    ))

            await asyncio.gather(*[resolve_one(e) for e in chosen])
        except Exception:
            pass
        return result
