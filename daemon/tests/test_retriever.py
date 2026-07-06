"""Retriever: anchor-precise Sphinx extraction + T1 caching."""

from __future__ import annotations

from docscope.cache import DocCache
from docscope.config import Config
from docscope.models import ResolvedSymbol
from docscope.retriever import Retriever

from .conftest import API_URL


def _resolved() -> ResolvedSymbol:
    return ResolvedSymbol(
        package="testpkg",
        version="1.0",
        symbol="testpkg.Thing.do_it",
        url=API_URL,
        anchor="testpkg.Thing.do_it",
        role="method",
        resolver="objects.inv",
    )


async def test_extracts_signature_body_example(cache: DocCache, config: Config, mock_client):
    client = mock_client()
    try:
        result = await Retriever(cache, config, client).retrieve(_resolved())
    finally:
        await client.aclose()
    assert result is not None and result.tier == "T2"
    sec = result.section
    assert sec.signature is not None
    assert "do_it(" in sec.signature.replace(" ", "")
    assert "[source]" not in sec.signature and "¶" not in sec.signature
    assert "Do the thing" in sec.body_md
    assert sec.example_md is not None
    assert ">>> Thing().do_it(3)" in sec.example_md


async def test_second_retrieve_is_t1(cache: DocCache, config: Config, mock_client):
    client = mock_client()
    try:
        r = Retriever(cache, config, client)
        await r.retrieve(_resolved())
        second = await r.retrieve(_resolved())
    finally:
        await client.aclose()
    assert second is not None and second.tier == "T1"
    assert second.section.signature is not None


async def test_missing_anchor_falls_back_to_main(cache: DocCache, config: Config, mock_client):
    client = mock_client()
    resolved = _resolved()
    resolved.anchor = "does-not-exist"
    try:
        result = await Retriever(cache, config, client).retrieve(resolved)
    finally:
        await client.aclose()
    # Falls back to main-content extraction rather than failing.
    assert result is not None
    assert "thing" in result.section.body_md.lower()
