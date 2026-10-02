"""
ENGINE/providers/Stream/R-039/R-039.py — Vidzee Hindi (v6:Hindi server)

Type: m3u8 (HLS)
Flow:
  1. GET https://core.vidzee.wtf/streams/movie/{tmdb_id}?s=v6:Hindi
     or https://core.vidzee.wtf/streams/tv/{tmdb_id}/{s}/{e}?s=v6:Hindi
  2. Parse JSON -> streams with direct m3u8 URLs
  3. No headers, no proxy, no API key required

Hindi-first provider: dedicated v6:Hindi server serves Hindi-dubbed
content (Bollywood, South Hindi-dub, K-drama multi-audio, Hollywood Hindi dubs).

Based on open-source vidbox-stremio (xre000001-ai) research.
"""
from __future__ import annotations

from ENGINE.providers.base import Provider, LinkData, Result, Stream, Subtitle
from ENGINE.tools.http import get_client, UA

_API = "https://core.vidzee.wtf"
_SERVER = "v6:Hindi"


class R039Provider(Provider):
    id = "R-039"
    name = "Vidzee Hindi"

    async def run(self, data: LinkData) -> Result:
        result = Result()
        if not data.tmdb_id:
            return result
        try:
            client = await get_client()
            headers = {"User-Agent": UA}

            # Build URL based on content type
            if data.season is not None:
                url = f"{_API}/streams/tv/{data.tmdb_id}/{data.season}/{data.episode}?s={_SERVER}"
            else:
                url = f"{_API}/streams/movie/{data.tmdb_id}?s={_SERVER}"

            resp = await client.get(url, headers=headers, timeout=20)
            if resp.status_code >= 400:
                return result

            try:
                data_json = resp.json()
            except Exception:
                return result

            streams_list = data_json.get("streams", [])
            if not streams_list:
                # Try alternative response format
                streams_list = data_json.get("data", {}).get("streams", [])

            for s in streams_list:
                stream_url = s.get("url") or s.get("stream_url") or s.get("file")
                if not stream_url:
                    continue

                quality = s.get("quality") or s.get("label") or ""
                # Vidzee Hindi server primarily serves Hindi audio
                language = s.get("language") or "Hindi"

                # Determine type
                if ".m3u8" in stream_url:
                    stream_type = "m3u8"
                elif ".mpd" in stream_url:
                    stream_type = "dash"
                else:
                    stream_type = "mp4"

                result.streams.append(Stream(
                    url=stream_url,
                    type=stream_type,
                    server="Vidzee Hindi",
                    quality=quality,
                    language=language,
                    # No special headers needed for Vidzee
                    playback_headers={},
                ))

            # Subtitles if available
            for sub in data_json.get("subtitles", []):
                sub_url = sub.get("url") or sub.get("file")
                if sub_url:
                    result.subtitles.append(Subtitle(
                        url=sub_url,
                        language=sub.get("lang") or sub.get("language") or "en",
                        label=sub.get("label") or "English",
                    ))

        except Exception:
            # Providers never raise - return what we have
            pass

        return result
