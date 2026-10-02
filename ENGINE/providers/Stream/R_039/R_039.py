"""
ENGINE/providers/Stream/R-039/R-039.py — Vidzee (multi-server, Hindi-aware)

Type: m3u8 (HLS)
Flow:
  1. GET https://core.vidzee.wtf/streams/movie/{tmdb_id}?s={server}&e=0
     or https://core.vidzee.wtf/streams/tv/{tmdb_id}/{s}/{e}?s={server}&e=0
     Header: Referer: https://player.vidzee.wtf/
  2. Parse JSON -> direct m3u8 URL
  3. Fallback: dcloud -> tik

  Optional: GET /streams/languages/movie/{tmdb_id} -> {"languages":["Hindi"]}
  Optional: GET /subs/movie/{tmdb_id} -> subtitles

Servers: dcloud (primary), tik (fallback)
Note: v6:Hindi server is retired - use languages endpoint for Hindi detection.

Verified 2026-10-02 via vidbox-stremio research.
"""
from __future__ import annotations

from ENGINE.providers.base import Provider, LinkData, Result, Stream, Subtitle
from ENGINE.tools.http import get_client, UA

_API = "https://core.vidzee.wtf"
_REFERER = "https://player.vidzee.wtf/"
# Working servers in priority order (dcloud primary, tik fallback)
_SERVERS = ["dcloud", "tik"]


class R039Provider(Provider):
    id = "R-039"
    name = "Vidzee"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        if not data.tmdb_id:
            return result
        try:
            client = await get_client()
            headers = {
                "User-Agent": UA,
                "Referer": _REFERER,
            }

            # Check available languages first (for honest Hindi labeling)
            hindi_available = False
            try:
                lang_url = f"{_API}/streams/languages/movie/{data.tmdb_id}"
                if data.season is not None:
                    lang_url = f"{_API}/streams/languages/tv/{data.tmdb_id}"
                lang_resp = await client.get(lang_url, headers=headers, timeout=10)
                if lang_resp.status_code < 400:
                    lang_data = lang_resp.json()
                    langs = lang_data.get("languages", [])
                    hindi_available = any("hindi" in str(l).lower() for l in langs)
            except Exception:
                pass

            # Try each server in order
            for server in _SERVERS:
                try:
                    if data.season is not None:
                        url = f"{_API}/streams/tv/{data.tmdb_id}/{data.season}/{data.episode}?s={server}&e=0"
                    else:
                        url = f"{_API}/streams/movie/{data.tmdb_id}?s={server}&e=0"

                    resp = await client.get(url, headers=headers, timeout=20)
                    if resp.status_code >= 400:
                        continue

                    try:
                        data_json = resp.json()
                    except Exception:
                        continue

                    stream_url = data_json.get("url")
                    if not stream_url:
                        continue

                    # Vidzee returns direct m3u8
                    language = data_json.get("language") or "Auto"
                    # Use languages endpoint for honest labeling
                    if hindi_available and language.lower() in ("auto", ""):
                        language = "Hindi"

                    result.streams.append(Stream(
                        url=stream_url,
                        type="m3u8",
                        server=f"Vidzee ({server})",
                        quality="",  # Quality not specified in response
                        language=language,
                        playback_headers={},  # No special headers needed
                    ))
                    # Got a working stream from this server, don't try others
                    break

                except Exception:
                    continue

            # Subtitles
            try:
                if data.season is not None:
                    sub_url = f"{_API}/subs/tv/{data.tmdb_id}/{data.season}/{data.episode}"
                else:
                    sub_url = f"{_API}/subs/movie/{data.tmdb_id}"
                sub_resp = await client.get(sub_url, headers=headers, timeout=10)
                if sub_resp.status_code < 400:
                    sub_data = sub_resp.json()
                    subs = sub_data.get("subtitles", []) or sub_data.get("subs", [])
                    for sub in subs:
                        sub_file = sub.get("url") or sub.get("file")
                        if sub_file:
                            result.subtitles.append(Subtitle(
                                url=sub_file,
                                language=sub.get("lang") or "en",
                                label=sub.get("label") or "English",
                            ))
            except Exception:
                pass

        except Exception:
            pass

        return result
