"""
ENGINE/tools/domains.py — Dynamic domain resolver plugin.

Indian piracy sites rotate domains frequently. This tool stores known
working domains per provider and falls back to a probe if the stored
one stops responding.

Configure in .env (optional overrides):
    DOMAIN_VEGAMOVIES=https://vegamovies.dad
    DOMAIN_HDHUB4U=https://hdhub4u.hair
    DOMAIN_ROGMOVIES=https://rogmovies.dad
    DOMAIN_MULTIMOVIES=https://multimovies.live
    DOMAIN_UHDMOVIES=https://uhdmovies.online
    DOMAIN_MOVIESMOD=https://moviesmod.skin
    DOMAIN_MOVIES4U=https://movies4u.homes
    DOMAIN_4KHDHUB=https://4kmovieshub.in

Usage:
    from ENGINE.tools.domains import get_domain

    api = await get_domain("vegamovies")
    if not api:
        return result
"""
from __future__ import annotations

import os
from typing import Optional

from ENGINE.tools.http import get_client, UA

# Default known domains per provider (kept as fallback)
# Updated 2026-10-01 from SaurabhKaperwan/Utils urls.json
_DEFAULTS: dict[str, list[str]] = {
    "vegamovies":   ["https://vegamovies.gallery", "https://vegamovies.dad", "https://vegamovies.skin"],
    "hdhub4u":      ["https://new1.hdhub4u.free", "https://hdhub4u.hair", "https://hdhub4u.gives"],
    "rogmovies":    ["https://rogmovies.best", "https://rogmovies.dad", "https://rogmovies.skin"],
    "multimovies":  ["https://multimovies.casa", "https://multimovies.live", "https://multimovies.cloud"],
    "uhdmovies":    ["https://uhdmovies.my", "https://uhdmovies.online", "https://uhdmovies.mom"],
    "moviesmod":    ["https://moviesmod.ai.in", "https://moviesmod.skin", "https://moviesmod.dad"],
    "movies4u":     ["https://movies4u.homes", "https://movies4u.art"],
    "n4khdhub":     ["https://4khdhub.one", "https://4kmovieshub.in", "https://4khdhub.com"],
    "bollyflix":    ["https://new.bollyflix.vote"],
    "moviesdrive":  ["https://new5.moviesdrive.christmas"],
}

_ENV_MAP: dict[str, str] = {
    "vegamovies":   "DOMAIN_VEGAMOVIES",
    "hdhub4u":      "DOMAIN_HDHUB4U",
    "rogmovies":    "DOMAIN_ROGMOVIES",
    "multimovies":  "DOMAIN_MULTIMOVIES",
    "uhdmovies":    "DOMAIN_UHDMOVIES",
    "moviesmod":    "DOMAIN_MOVIESMOD",
    "movies4u":     "DOMAIN_MOVIES4U",
    "n4khdhub":     "DOMAIN_4KHDHUB",
    "bollyflix":    "DOMAIN_BOLLYFLIX",
    "moviesdrive":  "DOMAIN_MOVIESDRIVE",
}

# Cache of verified domains
_cache: dict[str, str] = {}


async def get_domain(provider: str) -> Optional[str]:
    """
    Return a working base URL for the given provider name.
    Checks env override first, then cache, then probes known candidates.
    """
    key = provider.lower()

    # 1) Env override — trust it unconditionally
    env_key = _ENV_MAP.get(key)
    if env_key:
        env_val = os.getenv(env_key)
        if env_val:
            return env_val.rstrip("/")

    # 2) Already verified this session
    if key in _cache:
        return _cache[key]

    # 3) Probe candidates
    candidates = _DEFAULTS.get(key, [])
    client = await get_client()
    for domain in candidates:
        try:
            r = await client.get(
                domain,
                headers={"User-Agent": UA},
                timeout=8.0,
            )
            if r.status_code < 500:
                _cache[key] = domain.rstrip("/")
                return _cache[key]
        except Exception:
            continue

    return None
