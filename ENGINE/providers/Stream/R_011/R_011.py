"""
ENGINE/providers/Stream/R-011/R_011.py — KissKh (Asian dramas)

Flow:
  1. Search /api/DramaList/Search?q=<title>&type=1|2
  2. /api/DramaList/Drama/<id> -> episode list -> episode id
  3. /api/Setting/alts?id=<epsId>&version=2.8.10  -> video key
  4. /api/Sub/<epsId>?kkey=<key1>                  -> subtitles
  5. /api/DramaList/Episode/<epsId>.png?kkey=<key> -> Video/ThirdParty links

Ported from Streamplay's KissKhProvider.
"""
from __future__ import annotations

import re

from ENGINE.providers.base import Provider, LinkData, Result, Stream, Subtitle
from ENGINE.tools.http import get_client, UA

_API = "https://kisskh.nl"
_TIMEOUT = 12.0


def _slug(title: str) -> str:
    return title.lower().replace(r"[^a-z0-9]+", "-").strip("-")


def _kisskh_title(title: str) -> str:
    return re.sub(r"[^a-zA-Z0-9 ]", "", title).replace(" ", "-")


class R011Provider(Provider):
    id = "R-011"
    name = "KissKh"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        if data.is_anime:
            return result
        try:
            client = await get_client()
            headers = {"User-Agent": UA, "Referer": f"{_API}/"}
            content_type = "2" if data.season is None else "1"
            title = data.title or ""

            # 1) Search
            search_res = (await client.get(
                f"{_API}/api/DramaList/Search?q={title}&type={content_type}",
                headers=headers, timeout=_TIMEOUT,
            )).json()
            if not isinstance(search_res, list) or not search_res:
                return result

            slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            drama_id = drama_title = None
            if len(search_res) == 1:
                drama_id = search_res[0].get("id")
                drama_title = search_res[0].get("title")
            else:
                for item in search_res:
                    item_slug = re.sub(r"[^a-z0-9]+", "-", (item.get("title") or "").lower()).strip("-")
                    if data.season is None:
                        match = item_slug == slug
                    else:
                        match = (slug in item_slug and
                                 f"season {data.season}" in (item.get("title") or "").lower()) or item_slug == slug
                    if match:
                        drama_id = item.get("id")
                        drama_title = item.get("title")
                        break
                if not drama_id:
                    # fallback: exact title
                    for item in search_res:
                        if item.get("title") == title:
                            drama_id = item.get("id")
                            drama_title = item.get("title")
                            break

            if not drama_id or not drama_title:
                return result

            # 2) Drama detail -> episode list
            referer_title = _kisskh_title(drama_title)
            detail = (await client.get(
                f"{_API}/api/DramaList/Drama/{drama_id}?isq=false",
                headers={**headers, "Referer": f"{_API}/Drama/{referer_title}?id={drama_id}"},
                timeout=_TIMEOUT,
            )).json()
            episodes = detail.get("episodes") if isinstance(detail, dict) else None
            if not isinstance(episodes, list):
                return result

            ep_number = data.episode or 1
            if data.season is None:
                episode = episodes[0] if episodes else None
            else:
                episode = next((e for e in episodes if e.get("number") == ep_number), None)
            if not episode:
                return result

            eps_id = episode.get("id")

            # 3) Fetch video key
            kkey = None
            try:
                vk = (await client.get(
                    f"{_API}/api/Setting/alts?id={eps_id}&version=2.8.10",
                    headers=headers, timeout=_TIMEOUT,
                )).json()
                kkey = vk.get("key")
            except Exception:
                pass
            if not kkey:
                return result

            # 4) Subtitle key (best-effort)
            kkey1 = None
            try:
                sk = (await client.get(
                    f"{_API}/api/Setting/alts2?id={eps_id}&version=2.8.10",
                    headers=headers, timeout=_TIMEOUT,
                )).json()
                kkey1 = sk.get("key")
            except Exception:
                pass

            # 5) Video sources
            ep_referer = f"{_API}/Drama/{referer_title}/Episode-{ep_number}?id={drama_id}&ep={eps_id}&page=0&pageSize=100"
            src_data = (await client.get(
                f"{_API}/api/DramaList/Episode/{eps_id}.png?err=false&ts=&time=&kkey={kkey}",
                headers={**headers, "Referer": ep_referer},
                timeout=_TIMEOUT,
            )).json()
            links = [l for l in [src_data.get("Video"), src_data.get("ThirdParty")] if l]
            for link in links:
                if ".m3u8" in link or ".mp4" in link:
                    result.streams.append(Stream(
                        url=link,
                        type="m3u8" if ".m3u8" in link else "mp4",
                        server="R-011 KissKh",
                        quality="720p",
                        headers={"Origin": _API, "Referer": _API},
                        playback_headers={"Referer": f"{_API}/"},
                    ))

            # 6) Subtitles
            if kkey1:
                try:
                    subs = (await client.get(
                        f"{_API}/api/Sub/{eps_id}?kkey={kkey1}",
                        headers=headers, timeout=_TIMEOUT,
                    )).json()
                    if isinstance(subs, list):
                        for sub in subs:
                            if sub.get("src"):
                                result.subtitles.append(Subtitle(
                                    url=sub["src"],
                                    language=sub.get("label", "Unknown"),
                                ))
                except Exception:
                    pass
        except Exception:
            pass
        return result
