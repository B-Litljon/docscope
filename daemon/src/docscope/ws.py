"""WebSocket streaming endpoint with daemon-side debounce.

Editor clients stream cursor/context events; the daemon pushes doc cards. Two
message types come in:

* ``{"type": "context", "context": {...BufferContext...}}`` — a cursor moved.
  The daemon acts only after ``debounce_ms`` of idle **and** only if the symbol
  under the cursor changed since the last card it pushed (so scrolling within
  the same call doesn't re-emit). Ambient failures are silent.
* ``{"type": "lookup", "context": {...}}`` — an explicit request (the editor's
  force-lookup keybinding). Bypasses both the debounce and the change-gate, and
  reports errors back so the user gets feedback.

Outgoing: ``{"type": "card", ...}`` / ``{"type": "error", ...}`` /
``{"type": "status", ...}``.

Each connection owns an independent debounce timer; a new context event cancels
the pending one.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from .config import Config
from .logging_setup import get_logger, log_event
from .models import BufferContext, DocCard
from .pipeline import Pipeline
from .server import _serialize

log = get_logger("ws")


class WSSession:
    """Per-connection debounce + change-gate state machine."""

    def __init__(self, pipeline: Pipeline, config: Config, socket: WebSocket) -> None:
        self._pipeline = pipeline
        self._debounce_s = max(0.0, config.daemon.debounce_ms / 1000.0)
        self._socket = socket
        self._timer: asyncio.Task[None] | None = None
        self._latest: BufferContext | None = None
        self._last_pushed_symbol: str | None = None

    async def handle(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "ping":
            await self._socket.send_json({"type": "pong"})
            return
        try:
            ctx = BufferContext.model_validate(msg.get("context", msg))
        except ValidationError as exc:
            await self._socket.send_json(
                {"type": "error", "error": {"reason": "bad_context", "detail": str(exc)}}
            )
            return

        if kind == "lookup":
            await self._run(ctx, force=True)
        else:  # "context" (default)
            self._latest = ctx
            self._schedule()

    def _schedule(self) -> None:
        if self._timer and not self._timer.done():
            self._timer.cancel()
        self._timer = asyncio.create_task(self._debounced_fire())

    async def _debounced_fire(self) -> None:
        try:
            await asyncio.sleep(self._debounce_s)
        except asyncio.CancelledError:
            return
        ctx = self._latest
        if ctx is None:
            return
        # Change-gate: only push when the resolved symbol actually changed.
        extracted = self._pipeline.extract_context(ctx)
        if not extracted.symbol or extracted.symbol == self._last_pushed_symbol:
            return
        await self._safe_send({"type": "status", "message": "checking…"})
        await self._run(ctx, force=False, symbol=extracted.symbol)

    async def _run(self, ctx: BufferContext, *, force: bool, symbol: str | None = None) -> None:
        try:
            result = await self._pipeline.lookup(ctx)
        except Exception as exc:  # a lookup must never kill the connection
            log_event(log, logging.ERROR, "ws lookup crashed", error=str(exc))
            if force:
                await self._safe_send(
                    {"type": "error", "error": {"reason": "internal", "detail": str(exc)}}
                )
            return

        if isinstance(result, DocCard):
            self._last_pushed_symbol = result.symbol
            await self._safe_send(_serialize(result))
        elif force:
            await self._safe_send(_serialize(result))
        else:
            # Ambient (non-forced) misses never push a card — only a transient
            # status message, so the sidebar itself stays free of noise.
            message = f"no docs for {symbol}" if symbol else "no docs found"
            await self._safe_send({"type": "status", "message": message})

    async def _safe_send(self, payload: dict) -> None:
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            await self._socket.send_json(payload)

    def cancel(self) -> None:
        if self._timer and not self._timer.done():
            self._timer.cancel()


def register_ws(app: FastAPI) -> None:
    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        session = WSSession(websocket.app.state.pipeline, websocket.app.state.config, websocket)
        log_event(log, logging.INFO, "ws connected")
        try:
            while True:
                msg = await websocket.receive_json()
                await session.handle(msg)
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            log_event(log, logging.WARNING, "ws error", error=str(exc))
        finally:
            session.cancel()
            log_event(log, logging.INFO, "ws disconnected")
