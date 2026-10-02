"""
main.py — Reelz application entry point.

Mounts all routers and initialises all subsystems at startup.
Run: uvicorn main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from config import get_settings

_s = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Boot ENGINE provider registries ───────────────────────────────────────
    from ENGINE.providers.Stream.registry import init as init_stream
    from ENGINE.providers.Download.registry import init as init_download
    from ENGINE.providers.Subtitle.registry import init as init_subtitle
    from ENGINE.providers.Shorts.registry import init as init_shorts
    init_stream()
    init_download()
    init_subtitle()
    init_shorts()

    # ── Boot database (create tables if not exist) ────────────────────────────
    from USERS.db import init_db
    await init_db()

    yield

    # ── Teardown: close shared HTTP client pool ───────────────────────────────
    from ENGINE.tools.http import close_client
    await close_client()


app = FastAPI(
    title="Reelz",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if _s.debug else None,
    redoc_url=None,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
origins = [o.strip() for o in _s.cors_origins.split(",")] if _s.cors_origins != "*" else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Exception handlers — every error response, of every kind, uses the same
#    envelope as success responses. Three layers, most specific first:
#
#    1. HTTPException  — routes/dependencies that `raise HTTPException(...)`
#                         (auth failures, bad ids, disabled features, etc.)
#                         fastapi.HTTPException subclasses this, so one
#                         handler here covers both import paths.
#    2. RequestValidationError — pydantic request body/query validation
#                         failures (missing/invalid fields), normally a
#                         raw 422 `{"detail": [...]}` from FastAPI.
#    3. Exception       — catch-all for anything unhandled; never leak
#                         stack traces or internal error text to clients.
# ────────────────────────────────────────────────────────────────────────────

@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "ok":           False,
            "data":         None,
            "error":        exc.detail,
            "cache_ttl_ms": None,
        },
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "ok":           False,
            "data":         None,
            "error":        "Invalid request",
            "cache_ttl_ms": None,
        },
    )


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "ok":           False,
            "data":         None,
            "error":        "Internal server error",
            "cache_ttl_ms": None,
        },
    )


# ── Routers ───────────────────────────────────────────────────────────────────

# Infrastructure
from api.health import router as health_router
from api.config_route import router as config_router

# Catalog (no ENGINE calls)
from api.feed import router as feed_router
from api.discover import router as discover_router
from api.search import router as search_router
from api.media import router as media_router
from api.debug_providers import router as debug_router

# ENGINE (scraping layer)
from api.stream import router as stream_router
from api.download import router as download_router
from api.subtitle import router as subtitle_router
from api.shorts import router as shorts_router

# AI Insights
from api.insights import router as insights_router

# Auth + Users
from api.user_auth import router as user_auth_router

# Payments
from api.payment import router as payment_router

# Admin — full remote control (X-Admin-Token protected)
from api.admin import router as admin_router

app.include_router(health_router)
app.include_router(config_router)
app.include_router(feed_router)
app.include_router(discover_router)
app.include_router(search_router)
app.include_router(media_router)
app.include_router(debug_router)
app.include_router(stream_router)
app.include_router(download_router)
app.include_router(subtitle_router)
app.include_router(shorts_router)
app.include_router(insights_router)
app.include_router(user_auth_router)
app.include_router(payment_router)
app.include_router(admin_router)


@app.get("/", include_in_schema=False)
async def root():
    return {"name": "Reelz", "version": "1.0.0", "ts": int(time.time())}
