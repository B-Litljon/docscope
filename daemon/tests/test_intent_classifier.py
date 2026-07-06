"""IntentClassifier: LiteLLM proxy call + graceful degradation."""

from __future__ import annotations

import json

import httpx
import pytest

from docscope.config import Config
from docscope.intent_classifier import IntentClassifier
from docscope.models import BufferContext, ExtractedContext, ImportBinding

LLM_BASE = "http://llm.test/v1"


def _llm_config(enabled: bool = True) -> Config:
    cfg = Config()
    cfg.llm.enabled = enabled
    cfg.llm.base_url = LLM_BASE
    cfg.llm.model_tier_fast = "local-fast"
    return cfg


def _completion(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"role": "assistant", "content": content}}]},
    )


def _extracted() -> ExtractedContext:
    return ExtractedContext(
        language="python",
        symbol="df.join_asof",
        raw_token="join_asof",
        imports=[ImportBinding(local_name="pl", qualified_name="polars", package="polars")],
    )


def _buffer() -> BufferContext:
    return BufferContext(
        file_path="/x/f.py",
        language="python",
        text="import polars as pl\n\ndf = pl.DataFrame()\ndf.join_asof(other)\n",
        cursor_line=3,
        cursor_col=6,
    )


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_classify_returns_intent():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        body = json.loads(request.content)
        assert body["temperature"] == 0
        return _completion(
            '{"library":"polars","symbol":"polars.DataFrame.join_asof",'
            '"task_intent":"asof join","confidence":0.9}'
        )

    client = _client(handler)
    try:
        result = await IntentClassifier(_llm_config(), client).classify(_extracted(), _buffer())
    finally:
        await client.aclose()
    assert result is not None
    assert result.symbol == "polars.DataFrame.join_asof"
    assert result.library == "polars"
    assert result.confidence == pytest.approx(0.9)


async def test_disabled_returns_none():
    client = _client(lambda r: httpx.Response(500))
    try:
        assert (
            await IntentClassifier(_llm_config(enabled=False), client).classify(
                _extracted(), _buffer()
            )
            is None
        )
    finally:
        await client.aclose()


async def test_proxy_error_degrades_to_none():
    client = _client(lambda r: httpx.Response(500, text="boom"))
    try:
        assert (
            await IntentClassifier(_llm_config(), client).classify(_extracted(), _buffer()) is None
        )
    finally:
        await client.aclose()


async def test_unreachable_proxy_degrades_to_none():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = _client(handler)
    try:
        assert (
            await IntentClassifier(_llm_config(), client).classify(_extracted(), _buffer()) is None
        )
    finally:
        await client.aclose()


async def test_content_wrapped_in_prose_is_parsed():
    client = _client(
        lambda r: _completion(
            'Sure! {"library":"polars","symbol":"polars.DataFrame",'
            '"task_intent":"x","confidence":0.5} hope that helps'
        )
    )
    try:
        result = await IntentClassifier(_llm_config(), client).classify(_extracted(), _buffer())
    finally:
        await client.aclose()
    assert result is not None and result.symbol == "polars.DataFrame"


async def test_garbage_content_returns_none():
    client = _client(lambda r: _completion("I don't know"))
    try:
        assert (
            await IntentClassifier(_llm_config(), client).classify(_extracted(), _buffer()) is None
        )
    finally:
        await client.aclose()
