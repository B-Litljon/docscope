"""Shared pytest fixtures.

Real SQLite (tmp_path) is used throughout — the cache layer is never mocked.
HTTP is served offline via ``httpx.MockTransport`` so the resolver/retriever
tests are hermetic and fast; the vendored ``testpkg`` inventory + HTML stand in
for a live Sphinx site.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from docscope.cache import DocCache
from docscope.config import Config

FIXTURES = Path(__file__).parent / "fixtures"
INVENTORY_URL = "https://example.test/testpkg/objects.inv"
API_URL = "https://example.test/testpkg/api.html"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def inventory_bytes() -> bytes:
    return (FIXTURES / "testpkg_objects.inv").read_bytes()


@pytest.fixture
def api_html() -> str:
    return (FIXTURES / "testpkg_api.html").read_text("utf-8")


@pytest_asyncio.fixture
async def cache(tmp_path: Path) -> AsyncIterator[DocCache]:
    db = DocCache(tmp_path / "cache.db")
    await db.open()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
def config(tmp_path: Path) -> Config:
    cfg = Config()
    cfg.cache.dir = str(tmp_path)
    # Point the registry at our offline fixture inventory.
    from docscope.config import RegistryOverride

    cfg.registry = {
        "testpkg": RegistryOverride(inv_url=INVENTORY_URL, versioned=True),
    }
    return cfg


@pytest.fixture
def mock_client(inventory_bytes: bytes, api_html: str) -> Callable[[], httpx.AsyncClient]:
    """Factory for an AsyncClient that serves the fixtures offline."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("objects.inv"):
            return httpx.Response(200, content=inventory_bytes)
        if url.endswith("api.html"):
            return httpx.Response(200, text=api_html)
        return httpx.Response(404, text="not found")

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)

    return factory
