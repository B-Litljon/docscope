"""SymbolResolver fast path against the offline fixture inventory."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from docscope.cache import DocCache
from docscope.config import Config
from docscope.models import ExtractedContext, PackageVersion
from docscope.registry import DocRegistry
from docscope.symbol_resolver import SymbolResolver
from docscope.version_resolver import VersionMap


def _versions(exact: bool = True) -> VersionMap:
    return VersionMap(
        [
            PackageVersion(
                package="testpkg", version="1.0", ecosystem="python", source="uv.lock", exact=exact
            )
        ]
    )


def _ctx(symbol: str, incomplete: bool = False) -> ExtractedContext:
    return ExtractedContext(language="python", symbol=symbol, incomplete=incomplete)


async def _resolver(
    cache: DocCache, config: Config, factory: Callable[[], httpx.AsyncClient]
) -> tuple[SymbolResolver, httpx.AsyncClient]:
    client = factory()
    return SymbolResolver(cache, DocRegistry(config.registry), config, client), client


async def test_exact_symbol_resolves_version_pinned(cache, config, mock_client):
    resolver, client = await _resolver(cache, config, mock_client)
    try:
        res = await resolver.resolve(_ctx("testpkg.Thing.do_it"), _versions())
    finally:
        await client.aclose()
    assert res is not None
    assert res.symbol == "testpkg.Thing.do_it"
    assert res.url.endswith("api.html#testpkg.Thing.do_it")
    assert res.role == "method"
    assert res.exact_version is True  # exact install + versioned doc source


async def test_non_exact_version_flag(cache, config, mock_client):
    resolver, client = await _resolver(cache, config, mock_client)
    try:
        res = await resolver.resolve(_ctx("testpkg.Thing.do_it"), _versions(exact=False))
    finally:
        await client.aclose()
    assert res is not None and res.exact_version is False


async def test_warm_inventory_second_lookup(cache, config, mock_client):
    resolver, client = await _resolver(cache, config, mock_client)
    try:
        await resolver.resolve(_ctx("testpkg.Thing.do_it"), _versions())
        assert await cache.inventory_fresh("testpkg", "1.0", 7)
        res2 = await resolver.resolve(_ctx("testpkg.helper"), _versions())
    finally:
        await client.aclose()
    assert res2 is not None and res2.symbol == "testpkg.helper"


async def test_half_typed_symbol_prefix_completes(cache, config, mock_client):
    resolver, client = await _resolver(cache, config, mock_client)
    try:
        res = await resolver.resolve(_ctx("testpkg.Thing.do_i", incomplete=True), _versions())
    finally:
        await client.aclose()
    assert res is not None
    assert res.symbol == "testpkg.Thing.do_it"  # shortest completion wins


async def test_unknown_package_returns_none(cache, config, mock_client):
    resolver, client = await _resolver(cache, config, mock_client)
    try:
        res = await resolver.resolve(_ctx("nonexistent.Thing"), _versions())
    finally:
        await client.aclose()
    assert res is None


async def test_bare_symbol_without_dot_returns_none(cache, config, mock_client):
    resolver, client = await _resolver(cache, config, mock_client)
    try:
        assert await resolver.resolve(_ctx("testpkg"), _versions()) is None
    finally:
        await client.aclose()
