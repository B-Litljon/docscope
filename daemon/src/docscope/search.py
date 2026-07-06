"""Pluggable web-search backend for the T3 fallback (unindexed libraries).

A :class:`SearchProvider` turns a query into ranked results. Two concrete
implementations ship: a SearXNG JSON backend and a Brave Search API backend,
both driven by a configurable endpoint. When ``search.provider`` is ``"none"``
the null provider returns nothing and the pipeline simply reports a miss.

Search never raises out of :meth:`search`; on any error it returns ``[]`` so the
pipeline degrades cleanly.
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx
from pydantic import BaseModel

from .config import Config
from .logging_setup import get_logger, log_event

log = get_logger("search")


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str = ""


class SearchProvider(Protocol):
    async def search(self, query: str, limit: int = 5) -> list[SearchResult]: ...


class NullSearchProvider:
    async def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        return []


class SearxngSearchProvider:
    """SearXNG instance with the JSON output format enabled."""

    def __init__(self, config: Config, client: httpx.AsyncClient) -> None:
        self._endpoint = config.search.endpoint.rstrip("/")
        self._timeout = config.search.timeout_s
        self._client = client

    async def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        try:
            resp = await self._client.get(
                self._endpoint,
                params={"q": query, "format": "json"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log_event(log, logging.WARNING, "searxng error (degrading)", error=str(exc))
            return []
        results: list[SearchResult] = []
        for item in data.get("results", [])[:limit]:
            url = item.get("url")
            if not url:
                continue
            results.append(
                SearchResult(
                    title=item.get("title", url),
                    url=url,
                    snippet=item.get("content", ""),
                )
            )
        return results


class BraveSearchProvider:
    """Brave Search API (``/res/v1/web/search``)."""

    def __init__(self, config: Config, client: httpx.AsyncClient) -> None:
        self._endpoint = config.search.endpoint.rstrip("/")
        self._timeout = config.search.timeout_s
        self._api_key = config.search.api_key
        self._client = client

    async def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["X-Subscription-Token"] = self._api_key
        try:
            resp = await self._client.get(
                self._endpoint,
                params={"q": query, "count": limit},
                headers=headers,
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log_event(log, logging.WARNING, "brave error (degrading)", error=str(exc))
            return []
        results: list[SearchResult] = []
        for item in data.get("web", {}).get("results", [])[:limit]:
            url = item.get("url")
            if not url:
                continue
            results.append(
                SearchResult(
                    title=item.get("title", url),
                    url=url,
                    snippet=item.get("description", ""),
                )
            )
        return results


def build_search_provider(config: Config, client: httpx.AsyncClient) -> SearchProvider:
    provider = config.search.provider.lower()
    if provider == "searxng" and config.search.endpoint:
        return SearxngSearchProvider(config, client)
    if provider == "brave" and config.search.endpoint:
        return BraveSearchProvider(config, client)
    return NullSearchProvider()
