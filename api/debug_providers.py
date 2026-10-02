"""
api/debug_providers.py — Debug endpoint for provider diagnostics.
Tests R-035 (NetMirror) and R-038 (MovieBox) directly.
"""
from __future__ import annotations
import asyncio
import time
import traceback
from fastapi import APIRouter, Query
from api.auth import verify
from fastapi import Depends

router = APIRouter(prefix="/api/v1", tags=["Debug"])

@router.get("/debug/provider")
async def debug_provider(
    provider: str = Query("R-035"),
    tmdb_id: str = Query("550"),
    type: str = Query("movie"),
    user_id: str = Depends(verify),
):
    """Test a provider directly and return diagnostics."""
    from ENGINE.providers.base import LinkData
    
    result_data = {
        "provider": provider,
        "tmdb_id": tmdb_id,
        "type": type,
        "status": "unknown",
        "streams": 0,
        "error": None,
        "traceback": None,
        "duration_ms": 0,
        "steps": {},
    }
    
    try:
        # Import the provider
        if provider == "R-035":
            from ENGINE.providers.Stream.R_035.R_035 import R035Provider
            p = R035Provider()
        elif provider == "R-038":
            from ENGINE.providers.Stream.R_038.R_038 import R038Provider
            p = R038Provider()
            # For R-038, test search step separately
            try:
                # Test token fetch first
                token_start = time.time()
                token = await p._h5_token()
                result_data["steps"]["h5_token"] = {
                    "duration_ms": int((time.time() - token_start) * 1000),
                    "got_token": bool(token),
                    "token_len": len(token) if token else 0,
                }
                # Then test search
                search_start = time.time()
                items = await p._h5_search("Fight Club" if tmdb_id == "550" else "", False)
                result_data["steps"]["h5_search"] = {
                    "duration_ms": int((time.time() - search_start) * 1000),
                    "items_found": len(items),
                    "sample_titles": [it.get("title", "")[:50] for it in items[:3]],
                }
            except Exception as e:
                result_data["steps"]["h5_search"] = {"error": f"{type(e).__name__}: {e}"}
        elif provider == "R-018":
            from ENGINE.providers.Stream.R_018.R_018 import R018Provider
            p = R018Provider()
            # For R-018, test domain resolution and search separately
            try:
                from ENGINE.tools.domains import get_domain
                dom_start = time.time()
                domain = await get_domain("vegamovies")
                result_data["steps"]["domain"] = {
                    "duration_ms": int((time.time() - dom_start) * 1000),
                    "domain": domain,
                }
                if domain:
                    from ENGINE.tools.scraper import cf_get
                    import json
                    search_start = time.time()
                    html = await cf_get(f"{domain}/search.php?q=Fight+Club", referer=domain)
                    result_data["steps"]["search"] = {
                        "duration_ms": int((time.time() - search_start) * 1000),
                        "html_len": len(html) if html else 0,
                        "is_json": bool(html and html.strip().startswith("{")),
                    }
                    if html and html.strip().startswith("{"):
                        try:
                            j = json.loads(html)
                            hits = j.get("hits", [])
                            result_data["steps"]["search"]["hits"] = len(hits)
                            if hits:
                                doc = hits[0].get("document", {})
                                result_data["steps"]["search"]["first_title"] = doc.get("post_title", "")[:80]
                                result_data["steps"]["search"]["permalink"] = doc.get("permalink", "")
                        except Exception as e:
                            result_data["steps"]["search"]["parse_error"] = str(e)
            except Exception as e:
                result_data["steps"]["domain"] = {"error": f"{type(e).__name__}: {e}"}
        else:
            result_data["status"] = "error"
            result_data["error"] = f"Unknown provider: {provider}"
            return result_data
        
        # Create test data
        data = LinkData(
            tmdb_id=tmdb_id,
            type=type,
            title="Fight Club" if tmdb_id == "550" else "",
            year=1999 if tmdb_id == "550" else 0,
        )
        
        # Run with timing
        start = time.time()
        try:
            result = await asyncio.wait_for(p.run(data), timeout=40)
            duration = (time.time() - start) * 1000
            result_data["duration_ms"] = int(duration)
            
            if result and result.streams:
                result_data["status"] = "success"
                result_data["streams"] = len(result.streams)
                result_data["sample"] = [
                    {
                        "language": s.language,
                        "quality": s.quality,
                        "type": s.type,
                        "url": (s.url or "")[:80],
                    }
                    for s in result.streams[:3]
                ]
            else:
                result_data["status"] = "empty"
                result_data["error"] = "Provider returned no streams"
        except asyncio.TimeoutError:
            result_data["status"] = "timeout"
            result_data["error"] = "Provider timed out after 40s"
            result_data["duration_ms"] = 40000
        except Exception as e:
            result_data["status"] = "crashed"
            result_data["error"] = f"{type(e).__name__}: {e}"
            result_data["traceback"] = traceback.format_exc()[:2000]
            
    except Exception as e:
        result_data["status"] = "import_error"
        result_data["error"] = f"{type(e).__name__}: {e}"
        result_data["traceback"] = traceback.format_exc()[:2000]
    
    return result_data
