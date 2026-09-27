"""
ENGINE/providers/Stream/R-013/R_013.py — HDRezka

Large multi-audio catalog. Plain HTTP (no Cloudflare).
Flow:
  1. /search/?do=search&q=<title> -> .b-content__inline_item cards
  2. Film page -> #translators-list + initCDN default
  3. POST /ajax/get_cdn_series/?t=<ts> -> { url: clearTrash(...), subtitle }

Ported from Streamplay's HDRezkaProvider.
"""
from __future__ import annotations

import re
import time
from urllib.parse import urlencode

from ENGINE.providers.base import Provider, LinkData, Result, Stream, Subtitle
from ENGINE.tools.http import get_client, UA

_BASE = "https://rezka.ag"
_HEADERS = {"User-Agent": UA, "Referer": f"{_BASE}/", "Accept-Language": "en-US,en;q=0.9"}

_TRASH_ALPHA = ["@", "#", "!", "^", "$"]


def _build_combos() -> list[str]:
    import itertools
    out = []
    for length in (2, 3):
        for combo in itertools.product(_TRASH_ALPHA, repeat=length):
            out.append("".join(combo))
    return out


_COMBOS = _build_combos()


def _clear_trash(data: str) -> str:
    import base64
    if not data:
        return data
    if data.startswith("["):
        return data
    s = data.replace("#h", "").replace("//_//", "")
    for c in _COMBOS:
        s = s.replace(base64.b64encode(c.encode()).decode(), "")
    try:
        return base64.b64decode(s + "===").decode("utf-8")
    except Exception:
        return ""


def _parse_streams(decoded: str) -> list[dict]:
    out = []
    for part in decoded.split(","):
        m = re.match(r"^\s*\[([^\]]+)](.+)$", part)
        if not m:
            continue
        quality_m = re.search(r"\d{3,4}", m.group(1))
        quality = quality_m.group(0) if quality_m else ""
        mirrors = [u.strip() for u in m.group(2).split(" or ") if re.match(r"^https?://", u.strip())]
        url = next((u for u in mirrors if ".m3u8" in u.lower()), mirrors[0] if mirrors else None)
        if url:
            out.append({"quality": quality, "url": url})
    return out


def _parse_subs(raw) -> list[Subtitle]:
    if not isinstance(raw, str) or not raw:
        return []
    return [
        Subtitle(url=m.group(2).strip(), language=m.group(1).strip())
        for m in re.finditer(r"\[([^\]]+)](https?://[^,]+)", raw)
    ]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


class R013Provider(Provider):
    id = "R-013"
    name = "HDRezka"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        if not data.title:
            return result
        try:
            client = await get_client()

            # 1) Search
            queries = list({data.title, data.org_title} - {None})  # type: ignore[arg-type]
            hits = []
            for q in queries:
                r = await client.get(
                    f"{_BASE}/search/?do=search&subaction=search&q={q}",
                    headers=_HEADERS, timeout=15,
                )
                from ENGINE.tools.scraper import parse
                soup = parse(r.text)
                for item in soup.select(".b-content__inline_item"):
                    a = item.select_one(".b-content__inline_item-link a")
                    if not a:
                        continue
                    href = a.get("href", "")
                    id_m = re.search(r"/(\d+)-", href)
                    if not id_m or not href.endswith(".html"):
                        continue
                    title = a.get_text(strip=True)
                    year_m = re.search(r"-(\d{4})(?:-|\.)", href) or re.search(r"(\d{4})", item.get_text())
                    year = int(year_m.group(1)) if year_m else None
                    hits.append({"id": id_m.group(1), "url": href, "is_series": "/series/" in href, "year": year, "title": title})
                if hits:
                    break

            if not hits:
                return result

            want_series = data.type == "tv"
            want = _norm(data.title)
            want_org = _norm(data.org_title or "")
            want_year = data.year

            def score(h: dict) -> float:
                s = 2 if h["is_series"] == want_series else -3
                t = _norm(h["title"])
                if t == want or (want_org and t == want_org):
                    s += 2
                elif want in t or t in want:
                    s += 1
                if want_year and h.get("year"):
                    diff = abs(h["year"] - want_year)
                    s += 2 if diff == 0 else -min(2, diff * 0.5)
                return s

            best = max(hits, key=score)
            if score(best) <= 0:
                return result

            # 2) Film page -> translators
            page_r = (await client.get(best["url"], headers=_HEADERS, timeout=15)).text
            from ENGINE.tools.scraper import parse
            page_soup = parse(page_r)
            translators = [
                {"id": el.get("data-translator_id", ""), "name": el.get_text(strip=True) or "HDRezka"}
                for el in page_soup.select("#translators-list li[data-translator_id]")
            ]
            init_default_m = re.search(r"initCDN(?:Movies|Series)Events\(\d+,\s*(\d+)", page_r)
            if init_default_m:
                init_id = init_default_m.group(1)
                if not any(t["id"] == init_id for t in translators):
                    translators.insert(0, {"id": init_id, "name": "Original"})
            if not translators:
                return result

            # 3) CDN ajax per translator (up to 3)
            for tr in translators[:3]:
                try:
                    body: dict = (
                        {"id": best["id"], "translator_id": tr["id"],
                         "season": str(data.season or 1), "episode": str(data.episode or 1), "action": "get_stream"}
                        if data.type == "tv"
                        else {"id": best["id"], "translator_id": tr["id"], "action": "get_movie"}
                    )
                    body.update({"is_camrip": "0", "is_ads": "0", "is_director": "0"})
                    form = urlencode(body)
                    cdn_r = (await client.post(
                        f"{_BASE}/ajax/get_cdn_series/?t={int(time.time() * 1000)}",
                        content=form.encode(),
                        headers={**_HEADERS, "X-Requested-With": "XMLHttpRequest",
                                 "Content-Type": "application/x-www-form-urlencoded"},
                        timeout=15,
                    )).json()
                    if not cdn_r.get("success") or not cdn_r.get("url"):
                        continue
                    decoded = _clear_trash(str(cdn_r["url"]))
                    for sub in _parse_subs(cdn_r.get("subtitle")):
                        if not any(s.url == sub.url for s in result.subtitles):
                            result.subtitles.append(sub)
                    seen_urls = {s.url for s in result.streams}
                    for st in _parse_streams(decoded):
                        if st["url"] in seen_urls:
                            continue
                        seen_urls.add(st["url"])
                        result.streams.append(Stream(
                            url=st["url"],
                            type="m3u8" if ".m3u8" in st["url"].lower() else "mp4",
                            server=f"R-013 HDRezka · {tr['name']}{' ' + st['quality'] + 'p' if st['quality'] else ''}",
                            quality=st["quality"] or None,
                            headers={"Referer": f"{_BASE}/", "User-Agent": UA},
                            playback_headers={"Referer": f"{_BASE}/"},
                        ))
                except Exception:
                    continue
        except Exception:
            pass
        return result
