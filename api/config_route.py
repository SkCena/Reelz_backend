"""
api/config_route.py — GET /config
cache_ttl_ms: 3_600_000 (1 hour) — Cloudflare + app both cache for 1 hour
"""
from __future__ import annotations
from fastapi import APIRouter, Response
from config import get_settings
from api.envelope import ok
from api.cache_headers import set_cache

router = APIRouter(tags=["Config"])
_s = get_settings()
_TTL = 3_600_000  # 1 hour


@router.get("/config")
async def get_config(response: Response):
    set_cache(response, _TTL)
    return ok(
        data={
            "version":                 _s.app_version,
            "min_app_version":         _s.min_app_version,
            "latest_app_version":      _s.latest_app_version,
            "latest_apk_url":          _s.latest_apk_url,
            "force_maintenance":       _s.force_maintenance,
            "maintenance_message":     _s.maintenance_message,
            "shorts_enabled":          _s.shorts_enabled,
            "downloads_enabled":       _s.downloads_enabled,
            "search_min_chars":        2,
            "guest_streaming_enabled": True,
            "premium": {
                "enabled":              _s.premium_enabled,
                "monthly_price":        _s.premium_monthly_price,
                # Yearly price — set PREMIUM_YEARLY_PRICE in .env; falls back to 10× monthly.
                "yearly_price":         _s.premium_yearly_price if _s.premium_yearly_price > 0 else _s.premium_monthly_price * 10,
                "paystack_monthly_url": _s.paystack_monthly_url,
                "paystack_yearly_url":  _s.paystack_yearly_url,
                # Optional note below subscribe buttons (e.g. "Cancel anytime").
                "payment_note":         _s.premium_payment_note,
            },
            "ads": {
                # Master switch — set ADS_ENABLED=true in .env to serve ads.
                # All SDK keys, ad unit IDs, and frequency settings live in the app.
                "enabled":       _s.ads_enabled,
                "interstitial":  _s.ads_interstitial,
                "banner":        _s.ads_banner,
                "native":        _s.ads_native,
            },
        },
        cache_ttl_ms=_TTL,
    )
