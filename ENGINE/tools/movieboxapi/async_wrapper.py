"""
Async wrapper for the MovieBox API sync client.

The vendored movieboxapi client is synchronous (httpx sync).
This wrapper runs it in a thread pool so it can be used from async providers.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from .client import MovieBoxClient


class AsyncMovieBoxClient:
    """Async wrapper around MovieBoxClient."""

    def __init__(self, region: str = "IN", timeout: float = 30.0):
        self._client = MovieBoxClient(region=region, timeout=timeout)

    async def _run(self, func, *args, **kwargs):
        return await asyncio.to_thread(func, *args, **kwargs)

    async def search(self, query: str, page: int = 1, per_page: int = 20):
        return await self._run(self._client.search, query, page, per_page)

    async def get_detail(self, subject_path: str):
        return await self._run(self._client.get_detail, subject_path)

    async def get_stream(
        self,
        subject_id: str,
        se: int = 1,
        ep: int = 1,
        resolution: int = 1080,
        lan: Optional[str] = None,
    ):
        return await self._run(
            self._client.get_stream,
            subject_id=subject_id,
            se=se,
            ep=ep,
            resolution=resolution,
            lan=lan,
        )

    async def get_stream_url(
        self,
        subject_id: str,
        se: int = 1,
        ep: int = 1,
        resolution: int = 1080,
    ) -> str:
        return await self._run(
            self._client.get_stream_url,
            subject_id=subject_id,
            se=se,
            ep=ep,
            resolution=resolution,
        )

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass
