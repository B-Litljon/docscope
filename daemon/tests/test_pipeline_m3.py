"""M3 pipeline: LLM fallback, web-search fallback, graceful degradation."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from docscope.config import Config, RegistryOverride
from docscope.models import DocCard, LookupError
from docscope.pipeline import Pipeline

from .conftest import INVENTORY_URL

LLM_BASE = "http://llm.test/v1"
SEARCH_ENDPOINT = "http://searx.test/search"
WEB_DOC_URL = "http://webdocs.test/testpkg/do_it"

WEB_PAGE = (
    "<html><body><main><h1>testpkg do_it</h1>"
    "<p>Community write-up of testpkg.Thing.do_it and how to call it.</p>"
    "<pre>Thing().do_it(3)</pre></main></body></html>"
)


def _completion(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


@pytest.fixture
def handlers(inventory_bytes: bytes, api_html: str):
    """A configurable MockTransport handler; flags flip behaviour per test."""
    state = {"llm_reachable": True}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        path = request.url.path
        if path.endswith("/chat/completions"):
            if not state["llm_reachable"]:
                raise httpx.ConnectError("refused", request=request)
            return _completion(
                '{"library":"testpkg","symbol":"testpkg.Thing.do_it",'
                '"task_intent":"call do_it","confidence":0.88}'
            )
        if request.url.host == "searx.test":
            return httpx.Response(
                200,
                json={"results": [{"title": "do_it", "url": WEB_DOC_URL, "content": "snippet"}]},
            )
        if url == WEB_DOC_URL:
            return httpx.Response(200, text=WEB_PAGE)
        if url.endswith("objects.inv"):
            return httpx.Response(200, content=inventory_bytes)
        if url.endswith("api.html"):
            return httpx.Response(200, text=api_html)
        return httpx.Response(404, text="not found")

    return handler, state


def _config(tmp_path: Path, *, llm: bool, search: bool) -> Config:
    cfg = Config()
    cfg.cache.dir = str(tmp_path)
    cfg.registry = {"testpkg": RegistryOverride(inv_url=INVENTORY_URL, versioned=True)}
    if llm:
        cfg.llm.enabled = True
        cfg.llm.base_url = LLM_BASE
    if search:
        cfg.search.provider = "searxng"
        cfg.search.endpoint = SEARCH_ENDPOINT
    return cfg


def _ws(tmp_path: Path, body: str) -> tuple[str, dict]:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\ndependencies=["testpkg==1.0"]\n', "utf-8"
    )
    (ws / "main.py").write_text(body, "utf-8")
    lines = body.splitlines()
    return str(ws), {"text": body, "lines": lines}


def _ctx_dict(ws: str, text: str, line: int, col: int) -> dict:
    return {
        "file_path": f"{ws}/main.py",
        "language": "python",
        "text": text,
        "cursor_line": line,
        "cursor_col": col,
        "workspace_root": ws,
    }


async def _lookup(config: Config, client: httpx.AsyncClient, ctx: dict):
    from docscope.models import BufferContext

    pipeline = Pipeline(config)
    await pipeline.start(client=client)
    try:
        return await pipeline.lookup(BufferContext.model_validate(ctx))
    finally:
        await pipeline.close()


async def test_llm_fallback_resolves_local_variable(tmp_path, handlers):
    handler, _ = handlers
    # `t` is a local variable: the fast path cannot map it to a package.
    body = "import testpkg\n\nt = testpkg.Thing()\nt.do_it()\n"
    ws, _ = _ws(tmp_path, body)
    ctx = _ctx_dict(ws, body, 3, 3)  # cursor on do_it
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await _lookup(_config(tmp_path, llm=True, search=False), client, ctx)
    assert isinstance(result, DocCard)
    assert result.symbol == "testpkg.Thing.do_it"
    assert result.resolver == "llm+objects.inv"
    assert result.exact_version is True  # still version-pinned via objects.inv


async def test_incomplete_expression_resolved_deterministically(tmp_path, handlers):
    handler, _ = handlers
    # No LLM configured: half-typed symbol must still resolve via prefix search.
    body = "import testpkg\n\ntestpkg.Thing.do_i\n"
    ws, _ = _ws(tmp_path, body)
    ctx = _ctx_dict(ws, body, 2, 17)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await _lookup(_config(tmp_path, llm=False, search=False), client, ctx)
    assert isinstance(result, DocCard)
    assert result.symbol == "testpkg.Thing.do_it"
    assert result.resolver == "objects.inv"  # deterministic, no model involved


async def test_dead_proxy_does_not_break_fast_path(tmp_path, handlers):
    handler, state = handlers
    state["llm_reachable"] = False  # proxy is down
    body = "import testpkg\n\ntestpkg.Thing.do_it\n"
    ws, _ = _ws(tmp_path, body)
    ctx = _ctx_dict(ws, body, 2, 16)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    # LLM enabled but unreachable; a fast-path-resolvable symbol must still work.
    result = await _lookup(_config(tmp_path, llm=True, search=False), client, ctx)
    assert isinstance(result, DocCard)
    assert result.resolver == "objects.inv"


async def test_web_search_fallback(tmp_path, handlers):
    handler, _ = handlers
    body = "import testpkg\n\nt = testpkg.Thing()\nt.do_it()\n"
    ws, _ = _ws(tmp_path, body)
    ctx = _ctx_dict(ws, body, 3, 3)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    # No LLM; search configured -> falls through to web search.
    result = await _lookup(_config(tmp_path, llm=False, search=True), client, ctx)
    assert isinstance(result, DocCard)
    assert result.resolver == "web-search"
    assert result.source_url == WEB_DOC_URL
    assert "web search" in " ".join(result.warnings).lower()


async def test_all_tiers_exhausted_returns_error(tmp_path, handlers):
    handler, state = handlers
    state["llm_reachable"] = False
    body = "import testpkg\n\nt = testpkg.Thing()\nt.do_it()\n"
    ws, _ = _ws(tmp_path, body)
    ctx = _ctx_dict(ws, body, 3, 3)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await _lookup(_config(tmp_path, llm=True, search=False), client, ctx)
    assert isinstance(result, LookupError)
    assert result.reason == "unresolved"
