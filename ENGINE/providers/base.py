"""
ENGINE/providers/base.py — Provider base class.

Every provider (Stream, Download, Subtitle, Shorts) inherits from Provider.
This file knows nothing about registries, managers, cache, or routes.

Rules:
  - Providers NEVER raise exceptions — safe_run() catches everything
  - Providers NEVER call other providers
  - Providers call tools freely from ENGINE/tools/
  - If a provider breaks, only that provider is affected

Playback headers contract (playback_headers):
  - A provider sets playback_headers on Stream / DownloadItem / Subtitle / Short
    ONLY when the CDN/server requires specific headers for the URL to work.
  - Pass an empty dict (the default) if the URL works without any extra headers.
  - Include ANY headers the CDN needs: Referer, Origin, Authorization, X-Token, etc.
  - The manager passes the dict to the API as-is.
  - The app merges these headers into every HTTP request for that URL.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


# ── What providers produce ────────────────────────────────────────────────────

@dataclass
class Stream:
    url: str
    type: str                        # "m3u8" | "mp4" | "iframe"
    server: str = ""
    quality: Optional[str] = None
    language: Optional[str] = None    # e.g. "Hindi", "English", "Original"
    headers: dict = field(default_factory=dict)
    # Optional: set this if the provider's response tells you when the URL expires.
    # Unix timestamp in milliseconds (e.g. int(time.time() * 1000) + 3_600_000).
    # When set, the cache TTL is computed from this rather than the provider default.
    expires_at_ms: Optional[int] = None

    # ── Playback headers — set ONLY when the CDN/server requires them ─────────
    # Include any headers the CDN needs: Referer, Origin, Authorization, X-Token, etc.
    # Leave empty ({}) if the URL works without extra headers.
    # The manager passes this dict to the API; the app applies all entries.
    playback_headers: dict = field(default_factory=dict)


@dataclass
class DownloadItem:
    url: str
    type: str                        # "mp4" | "hls" | "mkv"
    quality: Optional[str] = None
    headers: dict = field(default_factory=dict)
    size_label: Optional[str] = None
    size_bytes: int = 0
    language: str = ""
    # Optional: set this if the provider knows when the URL expires.
    expires_at_ms: Optional[int] = None
    # For HLS: this should be the quality-specific index.m3u8 URL
    # The backend resolves master → quality index before returning.
    # For MP4: direct download URL.

    # ── Download headers — set ONLY when the CDN/server requires them ─────────
    # Include any headers the CDN needs: Referer, Origin, Authorization, X-Token, etc.
    # Leave empty ({}) if the URL works without extra headers.
    playback_headers: dict = field(default_factory=dict)


@dataclass
class Subtitle:
    url: str
    language: str
    label: Optional[str] = None
    format: str = "srt"

    # ── Subtitle fetch headers — set ONLY when required ───────────────────────
    # Include any headers the CDN needs. Leave empty ({}) if none are required.
    playback_headers: dict = field(default_factory=dict)


@dataclass
class Short:
    url: str
    title: str
    thumbnail: Optional[str] = None

    # ── Short playback headers — set ONLY when required ───────────────────────
    # Include any headers the CDN needs. Leave empty ({}) if none are required.
    playback_headers: dict = field(default_factory=dict)


@dataclass
class Result:
    """Unified result envelope. Every provider returns this."""
    streams: list[Stream] = field(default_factory=list)
    downloads: list[DownloadItem] = field(default_factory=list)
    subtitles: list[Subtitle] = field(default_factory=list)
    shorts: list[Short] = field(default_factory=list)
    http_status: int = 0   # last HTTP status code seen; 0 = unknown/timeout


# ── What managers pass to providers ──────────────────────────────────────────

@dataclass
class LinkData:
    """Everything a provider needs. Built by manager. Passed read-only."""
    tmdb_id: int
    type: str                        # "movie" | "tv"
    title: str
    imdb_id: Optional[str] = None
    year: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    is_anime: bool = False
    is_asian: bool = False
    is_bollywood: bool = False
    org_title: Optional[str] = None


# ── Timeout sentinel ─────────────────────────────────────────────────────────

class TimedOut(Result):
    """
    Returned by safe_run() on timeout or crash.
    Manager checks isinstance(result, TimedOut) to record a failure
    in the circuit breaker — not just an empty result.
    """
    pass


# ── Safe runner ───────────────────────────────────────────────────────────────

async def safe_run(provider: "Provider", data: LinkData, timeout_ms: int) -> Result:
    """
    Run provider.run(data) with a hard timeout.
    Never raises. Returns TimedOut on timeout or any exception.
    """
    try:
        result = await asyncio.wait_for(
            provider.run(data),
            timeout=timeout_ms / 1000,
        )
        return result or Result()
    except asyncio.TimeoutError:
        return TimedOut()
    except Exception:
        return TimedOut()


# ── Base class ────────────────────────────────────────────────────────────────

class Provider(ABC):
    id: str = ""        # unique e.g. "R-001"
    name: str = ""      # human name e.g. "VidFast"

    @abstractmethod
    async def run(self, data: LinkData) -> Result:
        """
        Fetch content for `data`. Return a Result.
        NEVER raises. NEVER calls other providers.
        Use tools from ENGINE/tools/ freely.
        """
        ...
