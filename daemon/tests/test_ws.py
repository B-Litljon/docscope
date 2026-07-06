"""WebSocket endpoint: forced lookup, debounce, and symbol-change gating."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from docscope.config import Config, RegistryOverride
from docscope.server import build_app

from .conftest import INVENTORY_URL


@pytest.fixture
def ws_client(tmp_path: Path, mock_client):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0"\ndependencies=["testpkg==1.0"]\n', "utf-8"
    )
    cfg = Config()
    cfg.cache.dir = str(tmp_path)
    cfg.daemon.debounce_ms = 30  # keep the test fast
    cfg.registry = {"testpkg": RegistryOverride(inv_url=INVENTORY_URL, versioned=True)}
    app = build_app(cfg, client=mock_client())
    with TestClient(app) as client:
        yield client, str(ws)


def _ctx(ws: str, text: str, line: int, col: int) -> dict:
    return {
        "file_path": f"{ws}/main.py",
        "language": "python",
        "text": text,
        "cursor_line": line,
        "cursor_col": col,
        "workspace_root": ws,
    }


TEXT = "import testpkg\n\ntestpkg.Thing.do_it\ntestpkg.helper\n"


def test_forced_lookup_returns_card(ws_client):
    client, ws = ws_client
    with client.websocket_connect("/ws") as sock:
        sock.send_json({"type": "lookup", "context": _ctx(ws, TEXT, 2, 16)})
        msg = sock.receive_json()
    assert msg["type"] == "card"
    assert msg["card"]["symbol"] == "testpkg.Thing.do_it"


def test_debounced_context_pushes_card(ws_client):
    client, ws = ws_client
    with client.websocket_connect("/ws") as sock:
        sock.send_json({"type": "context", "context": _ctx(ws, TEXT, 2, 16)})
        msg = sock.receive_json()
    assert msg["type"] == "card"
    assert msg["card"]["symbol"] == "testpkg.Thing.do_it"


def test_unchanged_symbol_is_gated(ws_client):
    client, ws = ws_client
    with client.websocket_connect("/ws") as sock:
        # First forced lookup sets the last-pushed symbol.
        sock.send_json({"type": "lookup", "context": _ctx(ws, TEXT, 2, 16)})
        assert sock.receive_json()["card"]["symbol"] == "testpkg.Thing.do_it"

        # A context event on the SAME symbol must not push another card.
        sock.send_json({"type": "context", "context": _ctx(ws, TEXT, 2, 17)})
        # A ping round-trips; if a card had been emitted it would arrive first.
        sock.send_json({"type": "ping"})
        assert sock.receive_json() == {"type": "pong"}

        # Moving to a DIFFERENT symbol pushes again.
        sock.send_json({"type": "context", "context": _ctx(ws, TEXT, 3, 10)})
        msg = sock.receive_json()
        assert msg["type"] == "card"
        assert msg["card"]["symbol"] == "testpkg.helper"
