"""
api/live_tv.py — Live TV channels API (India only).

Fetches and parses M3U playlists for Indian live TV channels.
Caches results for performance.

Sources:
  - iptv-org India: https://iptv-org.github.io/iptv/countries/in.m3u
  - BDXI_KB India: https://raw.githubusercontent.com/Mrbotrx/BDXI_KB/main/playlists/India.m3u8
"""
from __future__ import annotations

import re
import time
from typing import List, Dict

from fastapi import APIRouter, Depends
from api.auth import verify

router = APIRouter(prefix="/api/v1", tags=["Live TV"])

# India M3U sources (in priority order)
_M3U_SOURCES = [
    "https://iptv-org.github.io/iptv/countries/in.m3u",
    "https://raw.githubusercontent.com/Mrbotrx/BDXI_KB/main/playlists/India.m3u8",
]

# Cache
_cache: Dict = {"channels": [], "timestamp": 0}
_CACHE_TTL = 3600  # 1 hour


def _parse_m3u(content: str) -> List[Dict]:
    """Parse M3U playlist into channel list."""
    channels = []
    lines = content.strip().split("\n")

    current_info = {}
    for line in lines:
        line = line.strip()
        if line.startswith("#EXTINF:"):
            # Parse channel info
            # Format: #EXTINF:-1 tvg-id="..." tvg-logo="..." group-title="...",Channel Name
            current_info = {}

            # Extract tvg-logo
            logo_match = re.search(r'tvg-logo="([^"]*)"', line)
            if logo_match:
                current_info["logo"] = logo_match.group(1)

            # Extract group-title
            group_match = re.search(r'group-title="([^"]*)"', line)
            if group_match:
                current_info["group"] = group_match.group(1)

            # Extract channel name (after last comma)
            name_match = re.search(r',([^,]+)$', line)
            if name_match:
                current_info["name"] = name_match.group(1).strip()

        elif line and not line.startswith("#") and current_info:
            # This is the stream URL
            url = line
            # Only include HLS streams
            if ".m3u8" in url:
                channels.append({
                    "name": current_info.get("name", "Unknown"),
                    "url": url,
                    "logo": current_info.get("logo", ""),
                    "group": current_info.get("group", "General"),
                })
            current_info = {}

    return channels


async def _fetch_channels() -> List[Dict]:
    """Fetch channels from M3U sources with caching."""
    now = time.time()
    if _cache["channels"] and (now - _cache["timestamp"]) < _CACHE_TTL:
        return _cache["channels"]

    from ENGINE.tools.http import get_client

    all_channels = []
    seen_urls = set()

    client = await get_client()
    for source_url in _M3U_SOURCES:
        try:
            resp = await client.get(source_url, timeout=30)
            if resp.status_code >= 400:
                continue

            channels = _parse_m3u(resp.text)
            for ch in channels:
                if ch["url"] not in seen_urls:
                    seen_urls.add(ch["url"])
                    all_channels.append(ch)

            # If we got channels from first source, that's enough
            if all_channels:
                break
        except Exception:
            continue

    _cache["channels"] = all_channels
    _cache["timestamp"] = now
    return all_channels


@router.get("/live-tv/channels")
async def get_live_channels(
    user_id: str = Depends(verify),
):
    """Get all India live TV channels."""
    channels = await _fetch_channels()
    return {
        "ok": True,
        "data": {
            "channels": channels,
            "count": len(channels),
        },
        "error": None,
    }


@router.get("/live-tv/categories")
async def get_live_categories(
    user_id: str = Depends(verify),
):
    """Get live TV channels grouped by category."""
    channels = await _fetch_channels()

    categories: Dict[str, List] = {}
    for ch in channels:
        group = ch.get("group", "General")
        if group not in categories:
            categories[group] = []
        categories[group].append(ch)

    return {
        "ok": True,
        "data": {
            "categories": categories,
        },
        "error": None,
    }
