"""
ENGINE/providers/Stream/R-017/R_017.py — AnimeWorld (watchanimeworld.net)

Anime with Indian dubs (Hindi/Tamil/Telugu) + English + Japanese.
CF-gated → FlareSolverr. Resolves zephyrflick player to master m3u8.
Flow:
  1. FlareSolverr: /?s=<title> -> /series|movies/<slug>
  2. FlareSolverr: /<slug>     -> episode page
  3. zephyrflick iframe        -> master m3u8

Ported from Streamplay's AnimeWorldProvider.
"""
from __future__ import annotations

import re

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA
from ENGINE.tools.scraper import parse, cf_get
from ENGINE.tools.warp import warp_proxy

_BASE = "https://watchanimeworld.net"
_ZEPHYR = "https://play.zephyrflick.top"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _animetitles(data) -> list[str]:
    titles = [data.title]
    if data.org_title and data.org_title != data.title:
        titles.append(data.org_title)
    return [t for t in titles if t]


async def _flare_html(url: str, use_warp: bool = False) -> str | None:
    return await cf_get(url, use_warp=use_warp)


class R017Provider(Provider):
    id = "R-017"
    name = "AnimeWorld"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        if not data.title:
            return result

        season = None if data.type == "movie" else (data.season or 1)
        episode = None if data.type == "movie" else (data.episode or 1)

        try:
            # 1) Search via FlareSolverr+WARP
            results_list: list[dict] = []
            for q in _animetitles(data):
                html = await _flare_html(f"{_BASE}/?s={q}", use_warp=True)
                if not html:
                    continue
                soup = parse(html)
                for a in soup.select("a[href]"):
                    href = a.get("href", "")
                    m = re.search(r"/(series|movies)/([^/]+)/?$", href)
                    if not m:
                        continue
                    slug = m.group(2)
                    title = (a.get("title") or a.get_text(strip=True) or "").strip()
                    existing = next((r for r in results_list if r["slug"] == slug), None)
                    if existing:
                        if title and not existing["title"]:
                            existing["title"] = title
                    else:
                        results_list.append({"slug": slug, "href": href, "title": title})
                if results_list:
                    break

            if not results_list:
                return result

            want = _norm(data.title)

            def name_of(r: dict) -> str:
                return _norm(r.get("title") or r["slug"].replace("-", " "))

            best = (
                next((r for r in results_list if name_of(r) == want), None)
                or next((r for r in results_list if want in name_of(r) or name_of(r) in want), None)
                or results_list[0]
            )

            href = best["href"]
            base_url = href if href.startswith("http") else f"{_BASE}{href}"

            # 2) Content page -> episode
            content_html = await _flare_html(base_url, use_warp=True)
            if not content_html:
                return result

            csoup = parse(content_html)

            if episode is None:
                # Movie: find the first player iframe directly
                iframe_m = re.search(r'<iframe[^>]+(?:src|data-src)="([^"]+)"', content_html, re.I)
                ep_url = None
                iframe_src = iframe_m.group(1) if iframe_m else None
            else:
                # TV: find the episode link
                ep_url = None
                ep_re = re.compile(rf"episode[\s-]*{episode}\b", re.I)
                for a in csoup.select("a[href]"):
                    if ep_re.search(a.get_text() or a.get("href", "")):
                        ep_href = a.get("href", "")
                        ep_url = ep_href if ep_href.startswith("http") else f"{_BASE}{ep_href}"
                        break
                iframe_src = None

            if ep_url:
                ep_html = await _flare_html(ep_url, use_warp=True)
                if not ep_html:
                    return result
                iframe_m = re.search(r'<iframe[^>]+(?:src|data-src)="([^"]+)"', ep_html, re.I)
                iframe_src = iframe_m.group(1) if iframe_m else None

            if not iframe_src:
                return result

            embed = iframe_src if iframe_src.startswith("http") else f"https:{iframe_src.lstrip('/')}"

            # 3) Resolve zephyrflick to master m3u8
            if "zephyrflick" in embed:
                client = await get_client()
                # Try plain HTTP first (clearance may already be in jar)
                try:
                    zr = (await client.get(embed, headers={"User-Agent": UA, "Referer": f"{_BASE}/"}, timeout=15)).text
                    m3u8_m = re.search(r'(https?://[^"\'\\s]+\.m3u8[^"\'\\s]*)', zr, re.I)
                    if m3u8_m:
                        result.streams.append(Stream(
                            url=m3u8_m.group(1),
                            type="m3u8",
                            server="R-017 AnimeWorld",
                            headers={"Referer": f"{_ZEPHYR}/"},
                            playback_headers={"Referer": f"{_ZEPHYR}/"},
                        ))
                except Exception:
                    pass

                if not result.streams:
                    # cf_get WARP fallback
                    zhtml = await cf_get(embed, use_warp=True)
                    if zhtml:
                        m3u8_m = re.search(r'(https?://[^"\'\\s]+\.m3u8[^"\'\\s]*)', zhtml, re.I)
                        if m3u8_m:
                            result.streams.append(Stream(
                                url=m3u8_m.group(1),
                                type="m3u8",
                                server="R-017 AnimeWorld",
                                headers={"Referer": f"{_ZEPHYR}/"},
                                playback_headers={"Referer": f"{_ZEPHYR}/"},
                            ))
            else:
                result.streams.append(Stream(
                    url=embed,
                    type="iframe",
                    server="R-017 AnimeWorld",
                    headers={"Referer": f"{_BASE}/"},
                    playback_headers={"Referer": f"{_ZEPHYR}/"},
                ))
        except Exception:
            pass
        return result
