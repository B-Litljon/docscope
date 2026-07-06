"""Server HTTP surface: /health and POST /lookup (hermetic, mock transport)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from docscope.config import Config, RegistryOverride
from docscope.server import build_app

from .conftest import INVENTORY_URL


@pytest.fixture
def app_client(tmp_path: Path, mock_client):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\ndependencies=["testpkg==1.0"]\n', "utf-8"
    )
    cfg = Config()
    cfg.cache.dir = str(tmp_path)
    cfg.registry = {"testpkg": RegistryOverride(inv_url=INVENTORY_URL, versioned=True)}

    app = build_app(cfg, client=mock_client())
    with TestClient(app) as client:
        yield client, str(ws)


def test_health(app_client):
    client, _ = app_client
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_lookup_returns_card(app_client):
    client, ws = app_client
    payload = {
        "file_path": f"{ws}/main.py",
        "language": "python",
        "text": "import testpkg\n\ntestpkg.Thing.do_it\n",
        "cursor_line": 2,
        "cursor_col": 16,
        "workspace_root": ws,
    }
    resp = client.post("/lookup", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "card"
    assert body["card"]["symbol"] == "testpkg.Thing.do_it"
    assert "do_it" in body["markdown"]


def test_lookup_unresolved_returns_error(app_client):
    client, ws = app_client
    payload = {
        "file_path": f"{ws}/main.py",
        "language": "python",
        "text": "x = 1\ny = x.bit_length\n",
        "cursor_line": 1,
        "cursor_col": 13,
        "workspace_root": ws,
    }
    resp = client.post("/lookup", json=payload)
    assert resp.status_code == 200
    assert resp.json()["type"] == "error"
