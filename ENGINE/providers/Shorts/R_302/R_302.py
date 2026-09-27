"""
ENGINE/providers/Shorts/R_302/R_302.py — Archive.org TikTok Shorts

BUG FIXED: /metadata/{id}/files returns {"result": [...]} (a dict), NOT a raw
list. The old code did `if not isinstance(files, list): continue` which skipped
every single item. Now uses the full /metadata/{id} endpoint and reads
response["files"] — the correct shape archive.org has always returned.

PERF FIXED: metadata calls are now concurrent (asyncio.gather) instead of
sequential. Sequential with RESOLVE_LIMIT=60 × 8s timeout would never finish
inside the 45s safe_run budget; concurrent resolves in one round-trip window.
"""
from __future__ import annotations

import asyncio
import random

from ENGINE.providers.base import Provider, LinkData, Result, Short
from ENGINE.tools.http import get_client, UA

_SEARCH_URL    = "https://archive.org/advancedsearch.php"
_META_URL      = "https://archive.org/metadata/{identifier}"   # full item metadata
_THUMB_URL     = "https://archive.org/services/img/{identifier}"
_COLLECTION    = "tiktoks"
_FETCH_ROWS    = 50    # identifiers fetched from search
_RESOLVE_LIMIT = 20    # concurrent metadata calls (stays well under 45s budget)
_WANT          = 15    # stop after collecting this many valid shorts
_HTTP_TIMEOUT  = 8     # seconds per HTTP call


class R302Provider(Provider):
    id   = "R-302"
    name = "Archive.org TikToks"

    async def run(self, data: LinkData) -> Result:  # noqa: ARG002
        result = Result()
        try:
            client = await get_client()

            # ── Step 1: probe total count ──────────────────────────────────
            probe = await client.get(
                _SEARCH_URL,
                params={
                    "q":      f"collection:{_COLLECTION} mediatype:movies",
                    "rows":   0,
                    "output": "json",
                },
                headers={"User-Agent": UA},
                timeout=_HTTP_TIMEOUT,
            )
            if probe.status_code >= 400:
                return result

            total = probe.json().get("response", {}).get("numFound", 0)
            if not total:
                return result

            # ── Step 2: random page, fetch identifiers ─────────────────────
            max_page = max(1, total // _FETCH_ROWS)
            page     = random.randint(1, max_page)

            search = await client.get(
                _SEARCH_URL,
                params={
                    "q":      f"collection:{_COLLECTION} mediatype:movies",
                    "fl[]":   "identifier,title,description",
                    "rows":   _FETCH_ROWS,
                    "page":   page,
                    "output": "json",
                },
                headers={"User-Agent": UA},
                timeout=_HTTP_TIMEOUT,
            )
            if search.status_code >= 400:
                return result

            docs = search.json().get("response", {}).get("docs", [])
            if not docs:
                return result

            random.shuffle(docs)
            docs = docs[:_RESOLVE_LIMIT]

            # ── Step 3: resolve filenames concurrently ─────────────────────
            async def resolve(doc: dict) -> Short | None:
                iid = doc.get("identifier", "").strip()
                if not iid:
                    return None
                try:
                    meta_resp = await client.get(
                        _META_URL.format(identifier=iid),
                        headers={"User-Agent": UA},
                        timeout=_HTTP_TIMEOUT,
                    )
                    if meta_resp.status_code >= 400:
                        return None

                    # Correct shape: {"metadata": {...}, "files": [...], ...}
                    meta_json = meta_resp.json()
                    files = meta_json.get("files") or []
                    if not isinstance(files, list):
                        return None

                    mp4_name = None
                    # Prefer original (non-derivative) mp4
                    for f in files:
                        name   = f.get("name", "")
                        source = f.get("source", "").lower()
                        if (
                            name.lower().endswith(".mp4")
                            and source != "derivative"
                            and "thumb" not in name.lower()
                        ):
                            mp4_name = name
                            break
                    # Fall back to any mp4
                    if not mp4_name:
                        for f in files:
                            name = f.get("name", "")
                            if name.lower().endswith(".mp4") and "thumb" not in name.lower():
                                mp4_name = name
                                break

                    if not mp4_name:
                        return None

                    title = (
                        doc.get("title") or doc.get("description") or iid
                    ).strip()
                    if len(title) > 120:
                        title = title[:117] + "…"

                    return Short(
                        url       = f"https://archive.org/download/{iid}/{mp4_name}",
                        title     = title,
                        thumbnail = _THUMB_URL.format(identifier=iid),
                    )
                except Exception:
                    return None

            resolved = await asyncio.gather(*[resolve(d) for d in docs])
            for s in resolved:
                if s is not None:
                    result.shorts.append(s)
                    if len(result.shorts) >= _WANT:
                        break

        except Exception:
            pass

        return result
