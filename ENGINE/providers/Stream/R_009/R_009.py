"""
ENGINE/providers/Stream/R-009/R_009.py — RiveStream

Multi-server HLS provider.
Flow:
  1. GET /api/backendfetch?requestID=VideoProviderServices&secretKey=rive  -> source list
  2. Scrape _app script from homepage, extract let c=[...] key array
  3. Derive secretKey via Cloudflare worker
  4. For each source, fetch stream JSON and collect m3u8/mp4 links

Ported from Streamplay's RiveStreamProvider.
"""
from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import quote

from ENGINE.providers.base import Provider, LinkData, Result, Stream
from ENGINE.tools.http import get_client, UA

_API = "https://www.rivestream.app"
_WORKER = "https://rivestream.supe2372.workers.dev/"


class R009Provider(Provider):
    id = "R-009"
    name = "RiveStream"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        try:
            client = await get_client()
            headers = {"User-Agent": UA}

            # 1) Fetch service list
            svc_url = f"{_API}/api/backendfetch?requestID=VideoProviderServices&secretKey=rive"
            svc_res = (await client.get(svc_url, headers=headers, timeout=15)).json()
            services: list[str] = (svc_res or {}).get("data", []) if isinstance(svc_res, dict) else []
            if not services:
                return result

            # 2) Scrape homepage for _app script containing the key list
            home_html = (await client.get(_API, headers=headers, timeout=20)).text
            app_script_src = None
            for m in re.finditer(r'<script[^>]+src="([^"]+_app[^"]*)"', home_html):
                app_script_src = m.group(1)
                break
            if not app_script_src:
                return result

            js = (await client.get(f"{_API}{app_script_src}", headers=headers, timeout=15)).text
            key_list: list[str] = []
            for m in re.finditer(r'let\s+c\s*=\s*(\[[^\]]*\])', js):
                if len(m.group(1)) > 2:
                    key_list = re.findall(r'"([^"]+)"', m.group(1))
                    break
            if not key_list:
                return result

            # 3) Derive secret key
            c_list = ",".join(quote(k, safe="") for k in key_list)
            secret_key = (await client.get(
                f"{_WORKER}?input={quote(str(data.tmdb_id))}&cList={c_list}",
                headers=headers,
                timeout=15,
            )).text.strip()
            if not secret_key:
                return result

            # 4) Fetch each service stream
            async def fetch_service(source: str) -> None:
                try:
                    if data.season is None:
                        stream_url = (
                            f"{_API}/api/backendfetch?requestID=movieVideoProvider"
                            f"&id={data.tmdb_id}&service={source}&secretKey={secret_key}"
                        )
                    else:
                        stream_url = (
                            f"{_API}/api/backendfetch?requestID=tvVideoProvider"
                            f"&id={data.tmdb_id}&season={data.season}&episode={data.episode}"
                            f"&service={source}&secretKey={secret_key}"
                        )

                    text = (await client.get(stream_url, headers=headers, timeout=10)).text
                    j = json.loads(text)
                    sources_arr = (j.get("data") or {}).get("sources", []) if isinstance(j, dict) else []

                    for src in (sources_arr if isinstance(sources_arr, list) else []):
                        src_name: str = src.get("source", "") if isinstance(src, dict) else ""
                        url: str = src.get("url", "") if isinstance(src, dict) else ""
                        if not url:
                            continue

                        label = (
                            f"RiveStream {src_name}[{src.get('quality', '')}]"
                            if "asiacloud" in src_name.lower()
                            else f"RiveStream {src_name}"
                        )

                        if "proxy?url=" in url:
                            try:
                                decoded = re.sub(r"%(?![0-9A-Fa-f]{2})", "%25", url)
                                from urllib.parse import unquote
                                fully = unquote(decoded)
                                inner = fully.split("proxy?url=")[1].split("&headers=")[0]
                                real_url = unquote(inner)
                                headers_part = fully.split("&headers=")[1] if "&headers=" in fully else "{}"
                                hmap: dict = {}
                                try:
                                    hmap = json.loads(unquote(headers_part))
                                except Exception:
                                    pass
                                stream_type = "m3u8" if ".m3u8" in real_url.lower() else "mp4"
                                result.streams.append(Stream(
                                    url=real_url,
                                    type=stream_type,
                                    server=f"R-009 {label}",
                                    quality="1080p",
                                    headers={
                                        "Referer": hmap.get("Referer", ""),
                                        "Origin": hmap.get("Origin", ""),
                                    },
                                    playback_headers={"Referer": hmap.get("Referer")},
                                ))
                            except Exception:
                                pass
                        else:
                            stream_type = "m3u8" if ".m3u8" in url.lower() else "mp4"
                            result.streams.append(Stream(
                                url=url,
                                type=stream_type,
                                server=f"R-009 {label} (VLC)",
                                quality="1080p",
                                playback_headers={},
                            ))
                except Exception:
                    pass

            await asyncio.gather(*[fetch_service(s) for s in services])

        except Exception:
            pass
        return result
