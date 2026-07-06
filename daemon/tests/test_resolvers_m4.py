"""RustResolver (docs.rs probing) and MdnResolver."""

from __future__ import annotations

import httpx

from docscope.cache import DocCache
from docscope.config import Config
from docscope.mdn_resolver import MdnResolver
from docscope.models import ExtractedContext, PackageVersion
from docscope.rust_resolver import RustResolver
from docscope.version_resolver import VersionMap


def _rust_versions() -> VersionMap:
    return VersionMap(
        [
            PackageVersion(
                package="serde_json",
                version="1.0.117",
                ecosystem="rust",
                source="Cargo.lock",
                exact=True,
            )
        ]
    )


def _ctx(symbol: str) -> ExtractedContext:
    return ExtractedContext(language="rust", symbol=symbol)


def _client(ok_paths: set[str]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if request.url.path in ok_paths else 404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_docs_rs_enum_probe(cache: DocCache):
    # struct.Value 404s, enum.Value 200s -> resolver picks the enum.
    ok = {"/serde_json/1.0.117/serde_json/enum.Value.html"}
    client = _client(ok)
    try:
        res = await RustResolver(cache, Config(), client).resolve(
            _ctx("serde_json::Value"), _rust_versions()
        )
    finally:
        await client.aclose()
    assert res is not None
    assert res.url.endswith("serde_json/enum.Value.html")
    assert res.role == "enum"
    assert res.resolver == "docs.rs"
    assert res.exact_version is True


async def test_docs_rs_method_anchor_stdlib(cache: DocCache):
    ok = {"/stable/std/collections/struct.HashMap.html"}
    client = _client(ok)
    try:
        res = await RustResolver(cache, Config(), client).resolve(
            _ctx("std::collections::HashMap::new"), VersionMap([])
        )
    finally:
        await client.aclose()
    assert res is not None
    assert res.url.endswith("struct.HashMap.html#method.new")
    assert res.anchor == "method.new"
    assert res.exact_version is False  # stdlib docs aren't version-pinned


async def test_docs_rs_unknown_crate_returns_none(cache: DocCache):
    client = _client(set())
    try:
        res = await RustResolver(cache, Config(), client).resolve(
            _ctx("unknowncrate::Thing"), VersionMap([])
        )
    finally:
        await client.aclose()
    assert res is None


async def test_docs_rs_result_is_cached(cache: DocCache):
    ok = {"/serde_json/1.0.117/serde_json/enum.Value.html"}
    client = _client(ok)
    try:
        r = RustResolver(cache, Config(), client)
        await r.resolve(_ctx("serde_json::Value"), _rust_versions())
        assert await cache.inventory_fresh("serde_json", "1.0.117", 7)
        again = await r.resolve(_ctx("serde_json::Value"), _rust_versions())
    finally:
        await client.aclose()
    assert again is not None and again.role == "enum"


# ---- MDN -----------------------------------------------------------------


async def test_mdn_prototype_method():
    res = await MdnResolver(Config()).resolve(
        ExtractedContext(language="javascript", symbol="Array.prototype.map"), VersionMap([])
    )
    assert res is not None
    assert res.url.endswith("/Global_Objects/Array/map")
    assert res.resolver == "mdn"


async def test_mdn_static_method():
    res = await MdnResolver(Config()).resolve(
        ExtractedContext(language="javascript", symbol="JSON.parse"), VersionMap([])
    )
    assert res is not None and res.url.endswith("/Global_Objects/JSON/parse")


async def test_mdn_non_builtin_returns_none():
    res = await MdnResolver(Config()).resolve(
        ExtractedContext(language="javascript", symbol="lodash.debounce"), VersionMap([])
    )
    assert res is None
