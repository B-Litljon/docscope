"""SearchProvider implementations + graceful failure."""

from __future__ import annotations

import httpx

from docscope.config import Config
from docscope.search import (
    BraveSearchProvider,
    NullSearchProvider,
    SearxngSearchProvider,
    build_search_provider,
)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _searxng_cfg() -> Config:
    cfg = Config()
    cfg.search.provider = "searxng"
    cfg.search.endpoint = "http://searx.test/search"
    return cfg


async def test_searxng_parses_results():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["format"] == "json"
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "polars join_asof", "url": "http://d/1", "content": "asof join docs"},
                    {"title": "other", "url": "http://d/2", "content": "..."},
                ]
            },
        )

    client = _client(handler)
    try:
        results = await SearxngSearchProvider(_searxng_cfg(), client).search("polars join_asof")
    finally:
        await client.aclose()
    assert len(results) == 2
    assert results[0].url == "http://d/1"
    assert "asof" in results[0].snippet


async def test_searxng_error_returns_empty():
    client = _client(lambda r: httpx.Response(502))
    try:
        results = await SearxngSearchProvider(_searxng_cfg(), client).search("q")
    finally:
        await client.aclose()
    assert results == []


async def test_brave_parses_results():
    cfg = Config()
    cfg.search.provider = "brave"
    cfg.search.endpoint = "http://brave.test/res/v1/web/search"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"web": {"results": [{"title": "t", "url": "http://b/1", "description": "d"}]}},
        )

    client = _client(handler)
    try:
        results = await BraveSearchProvider(cfg, client).search("q")
    finally:
        await client.aclose()
    assert results[0].url == "http://b/1" and results[0].snippet == "d"


async def test_null_provider():
    client = _client(lambda r: httpx.Response(200, json={}))
    try:
        assert await NullSearchProvider().search("q") == []
    finally:
        await client.aclose()


async def test_build_defaults_to_null_when_unconfigured():
    client = _client(lambda r: httpx.Response(200))
    try:
        assert isinstance(build_search_provider(Config(), client), NullSearchProvider)
    finally:
        await client.aclose()
