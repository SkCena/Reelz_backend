"""
ENGINE/providers/Stream/registry.py — Stream provider registry.

THE ONLY FILE THAT KNOWS STREAM PROVIDERS EXIST.

Add a provider:
    1. Create ENGINE/providers/Stream/R-XXX/ folder
    2. Create R-XXX.py inside with a Provider subclass
    3. Import it below + add one line to ACTIVE

Remove a provider:
    - Delete the folder + remove one line here

Disable temporarily:
    - Move from ACTIVE to DISABLED list

Provider ID range: R-001 to R-099
"""
from __future__ import annotations

from ENGINE.providers.base import Provider

# ── Imports ───────────────────────────────────────────────────────────────────

from ENGINE.providers.Stream.R_001.R_001 import R001Provider   # 2Embed
from ENGINE.providers.Stream.R_002.R_002 import R002Provider   # VidFast
from ENGINE.providers.Stream.R_003.R_003 import R003Provider   # VidRock
from ENGINE.providers.Stream.R_004.R_004 import R004Provider   # HexaSU
from ENGINE.providers.Stream.R_005.R_005 import R005Provider   # AllMovieLand
from ENGINE.providers.Stream.R_006.R_006 import R006Provider   # Xpass
from ENGINE.providers.Stream.R_007.R_007 import R007Provider   # VaplayerV2
from ENGINE.providers.Stream.R_008.R_008 import R008Provider   # DahmerMovies
from ENGINE.providers.Stream.R_009.R_009 import R009Provider   # RiveStream
from ENGINE.providers.Stream.R_010.R_010 import R010Provider   # PrimeVids
from ENGINE.providers.Stream.R_011.R_011 import R011Provider   # KissKh
from ENGINE.providers.Stream.R_012.R_012 import R012Provider   # Castle
from ENGINE.providers.Stream.R_013.R_013 import R013Provider   # HDRezka
from ENGINE.providers.Stream.R_014.R_014 import R014Provider   # AniZone
from ENGINE.providers.Stream.R_015.R_015 import R015Provider   # AniNeko
from ENGINE.providers.Stream.R_016.R_016 import R016Provider   # AnimeNoSub
from ENGINE.providers.Stream.R_017.R_017 import R017Provider   # AnimeWorld
from ENGINE.providers.Stream.R_018.R_018 import R018Provider   # VegaMovies
from ENGINE.providers.Stream.R_019.R_019 import R019Provider   # HdHub4u
from ENGINE.providers.Stream.R_020.R_020 import R020Provider   # 4KHdHub
from ENGINE.providers.Stream.R_021.R_021 import R021Provider   # Movies4u
from ENGINE.providers.Stream.R_022.R_022 import R022Provider   # RogMovies
from ENGINE.providers.Stream.R_023.R_023 import R023Provider   # MultiMovies
from ENGINE.providers.Stream.R_024.R_024 import R024Provider   # UhdMovies
from ENGINE.providers.Stream.R_025.R_025 import R025Provider   # Moviesmod
from ENGINE.providers.Stream.R_026.R_026 import R026Provider   # VidLink
from ENGINE.providers.Stream.R_027.R_027 import R027Provider   # CineMacity
from ENGINE.providers.Stream.R_028.R_028 import R028Provider   # VidEasy
from ENGINE.providers.Stream.R_029.R_029 import R029Provider   # VidZee
from ENGINE.providers.Stream.R_030.R_030 import R030Provider   # Peachify
from ENGINE.providers.Stream.R_031.R_031 import R031Provider   # VidSrcXYZ
from ENGINE.providers.Stream.R_032.R_032 import R032Provider   # MovieBox
from ENGINE.providers.Stream.R_033.R_033 import R033Provider   # VixSrc
from ENGINE.providers.Stream.R_034.R_034 import R034Provider   # 4KHDHub Hindi
from ENGINE.providers.Stream.R_035.R_035 import R035Provider   # NetMirror OTT
from ENGINE.providers.Stream.R_036.R_036 import R036Provider   # Bollyflix (Hindi)
from ENGINE.providers.Stream.R_037.R_037 import R037Provider   # VidZee (Hindi dub)

# ── ACTIVE — priority order (fastest/most reliable first) ────────────────────
#
# Fast, no-scrape, direct-API providers come first so the early-exit
# "first valid m3u8" path in the stream manager fires as quickly as possible.
# Scraper-heavy providers (R-018..R-025) run in parallel but their results
# arrive later and fill quality slots rather than winning the first-stream race.

ACTIVE: list[Provider] = [
    R002Provider(),   # VidFast       — direct JSON API, fast
    R026Provider(),   # VidLink       — enc-dec direct API, fast
    R003Provider(),   # VidRock       — AES-encoded direct API, fast
    R009Provider(),   # RiveStream    — multi-source HLS, fast fan-out
    R004Provider(),   # HexaSU        — enc-dec direct, fast
    R028Provider(),   # VidEasy       — 14-server fan-out, concurrently fast
    R030Provider(),   # Peachify      — 6-server GCM-decrypt, concurrently fast
    R029Provider(),   # VidZee        — 8-server AES-CBC decrypt, concurrently fast
    R001Provider(),   # 2Embed        — iframe (fast URL build, low quality)
    R006Provider(),   # Xpass         — iframe embed
    R007Provider(),   # VaplayerV2    — iframe / direct
    R010Provider(),   # PrimeVids     — direct
    R031Provider(),   # VidSrcXYZ     — 3-step decrypt chain (medium speed)
    R033Provider(),   # VixSrc        — API + signed HLS, fast
    R034Provider(),   # 4KHDHub Hindi — Indian site, Hindi dubbed/dual audio
    R035Provider(),   # NetMirror OTT — Netflix/Prime/Hotstar, Hindi dub fan-out
    R036Provider(),   # Bollyflix     — Hindi/Bollywood movies & series
    R037Provider(),   # VidZee        — TMDB-keyed, Hindi dub support
    R032Provider(),   # MovieBox      — h5-api search/download/play (slow)
    R005Provider(),   # AllMovieLand  — scraper
    R008Provider(),   # DahmerMovies  — scraper
    R011Provider(),   # KissKh        — scraper (Asian content)
    R012Provider(),   # Castle        — AES API (Indian/multi-lang)
    R013Provider(),   # HDRezka       — scraper (Russian)
    R027Provider(),   # CineMacity    — Cloudflare + playerjs scraper
    R018Provider(),   # VegaMovies    — scraper + host extractor
    R019Provider(),   # HdHub4u       — scraper
    R020Provider(),   # 4KHdHub       — scraper
    R021Provider(),   # Movies4u      — scraper
    R022Provider(),   # RogMovies     — scraper
    R023Provider(),   # MultiMovies   — scraper + Cloudflare
    R024Provider(),   # UhdMovies     — scraper + bypass
    R025Provider(),   # Moviesmod     — scraper + bypass
    # Anime-specific
    R014Provider(),   # AniZone
    R015Provider(),   # AniNeko
    R016Provider(),   # AnimeNoSub
    R017Provider(),   # AnimeWorld
]

# ── DISABLED — comment out or move here to pause a provider ──────────────────

DISABLED: list[Provider] = []

# ── Internal registry ─────────────────────────────────────────────────────────

_registry: list[Provider] = []
_ready = False


def init() -> None:
    global _ready
    if _ready:
        return
    _registry.extend(ACTIVE)
    _ready = True


def get_all() -> list[Provider]:
    return list(_registry)
