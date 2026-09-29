"""
config.py — All settings in one place. Nothing reads os.environ directly.

Every folder (api, ENGINE, CATALOG, USERS) imports from here.
Change a value here — it changes everywhere.
"""
from __future__ import annotations

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Server ────────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # ── Auth ──────────────────────────────────────────────────────────────────
    app_secret_token: str = ""          # X-Reelz-Token header value
    admin_secret_key: str = ""          # X-Admin-Token header value (admin panel)

    # ── CORS ──────────────────────────────────────────────────────────────────
    cors_origins: str = "*"

    # ── TMDB ─────────────────────────────────────────────────────────────────
    tmdb_api_key: str = ""
    tmdb_base_url: str = "https://api.themoviedb.org/3"
    tmdb_image_base: str = "https://image.tmdb.org/t/p"
    tmdb_poster_size: str = "w500"
    tmdb_backdrop_size: str = "w1280"
    tmdb_still_size: str = "w300"
    tmdb_profile_size: str = "w185"

    # ── Feed ──────────────────────────────────────────────────────────────────
    feed_cache_ttl_seconds: int = 3600          # 1 hour
    feed_section_limit: int = 20

    # ── Cache ─────────────────────────────────────────────────────────────────
    redis_url: str = ""                         # empty = memory cache
    cache_ttl_seconds: int = 480                # 8 min stream cache
    cache_backend: str = "memory"               # memory | redis | cloudflare

    # ── Circuit breaker ───────────────────────────────────────────────────────
    cb_fail_threshold: int = 2
    cb_cooldown_seconds: int = 300
    provider_stats_file: str = "./provider-stats.json"

    # ── Provider tuning ───────────────────────────────────────────────────────
    provider_timeout_ms: int = 45_000

    # ── WARP ──────────────────────────────────────────────────────────────────
    warp_mode: str = "off"              # off | required | fallback | all
    warp_proxy_url: str = ""            # socks5://host:port
    warp_flaresolverr_url: str = ""     # http://host:8191

    # ── FlareSolverr ──────────────────────────────────────────────────────────
    flaresolverr_url: str = ""

    # ── Captcha ───────────────────────────────────────────────────────────────
    anticaptcha_key: str = ""
    twocaptcha_key: str = ""

    # ── Residential IP ────────────────────────────────────────────────────────
    residential_proxy_url: str = ""     # http://user:pass@host:port

    # ── Debrid ────────────────────────────────────────────────────────────────
    realdebrid_key: str = ""
    alldebrid_key: str = ""
    torbox_key: str = ""

    # ── Provider-specific ─────────────────────────────────────────────────────
    castle_suffix: str = ""
    hdrezka_base_url: str = "https://rezka.ag"
    anizone_base_url: str = "https://anizone.to"
    wyzie_key: str = ""
    consumet_url: str = ""

    # ── JWT (issued by this backend) ──────────────────────────────────────────
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_hours: int = 720     # 30 days

    # ── Google OAuth ──────────────────────────────────────────────────────────
    google_client_id: str = ""          # Web client ID for ID-token verification

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./reelz.db"
    # Postgres example: postgresql+asyncpg://user:pass@localhost/reelz

    # ── Paystack ──────────────────────────────────────────────────────────────
    paystack_secret_key: str = ""
    paystack_public_key: str = ""
    paystack_webhook_secret: str = ""
    paystack_base_url: str = "https://api.paystack.co"

    # ── App config (returned by GET /config) ───────────────────────────────────
    app_version: int = 1
    min_app_version: int = 1
    latest_app_version: int = 1
    latest_apk_url: str = ""

    # Feature flags
    shorts_enabled: bool = True
    downloads_enabled: bool = True
    force_maintenance: bool = False
    maintenance_message: str = ""

    # Download resolution caps (0 = no cap / unlimited)
    # These are sent to the app via /config — the app NEVER decides caps itself.
    # The backend also enforces the free cap server-side in api/download.py.

    # Premium pricing (in kobo / minor currency unit)
    premium_enabled: bool = False
    premium_monthly_price: int = 0
    # Yearly price — set in .env; defaults to 10× monthly if absent.
    premium_yearly_price: int = 0
    paystack_monthly_url: str = ""
    paystack_yearly_url: str = ""
    # Optional note shown below subscribe buttons (e.g. "Cancel anytime").
    premium_payment_note: str = ""

    # Ads config — Start.io
    # Only 4 toggles. All SDK keys, ad unit IDs, and frequency logic live in the app.
    ads_enabled: bool = False
    ads_interstitial: bool = True   # show interstitial ads when ads_enabled=true
    ads_banner: bool = True         # show banner ads when ads_enabled=true
    ads_native: bool = True         # show native ads when ads_enabled=true


@lru_cache
def get_settings() -> Settings:
    return Settings()
