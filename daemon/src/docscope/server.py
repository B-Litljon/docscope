"""FastAPI daemon.

M1 exposes the HTTP surface: a health probe and ``POST /lookup`` (the
explicit-request endpoint that bypasses debounce — used both for keybound
manual lookups and by the CLI/tests). The WebSocket streaming endpoint with
daemon-side debounce is added in M2 via :func:`register_ws`.

A single :class:`Pipeline` is created for the process lifetime and shared by all
requests.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI

from . import __version__
from .config import Config
from .logging_setup import configure_logging, get_logger, log_event
from .models import BufferContext, DocCard
from .pipeline import Pipeline

log = get_logger("server")


def _serialize(result: object) -> dict[str, Any]:
    if isinstance(result, DocCard):
        return {"type": "card", "card": result.model_dump(), "markdown": result.to_markdown()}
    # LookupError
    return {"type": "error", "error": result.model_dump()}  # type: ignore[union-attr]


def build_app(config: Config, *, client: httpx.AsyncClient | None = None) -> FastAPI:
    """Build the FastAPI app. ``client`` injects an httpx transport (tests use a
    MockTransport-backed client); the pipeline is always started inside the app
    lifespan so its SQLite connection is bound to the serving event loop."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pipe = Pipeline(config)
        await pipe.start(client=client)
        app.state.pipeline = pipe
        app.state.config = config
        log_event(
            log, logging.INFO, "daemon ready", host=config.daemon.host, port=config.daemon.port
        )
        try:
            yield
        finally:
            await pipe.close()

    app = FastAPI(title="docscope", version=__version__, lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.post("/lookup")
    async def lookup(ctx: BufferContext) -> dict[str, Any]:
        """Explicit lookup; bypasses debounce (M2 WS applies debounce instead)."""
        pipeline: Pipeline = app.state.pipeline
        result = await pipeline.lookup(ctx)
        return _serialize(result)

    # WebSocket streaming endpoint + daemon-side debounce (M2).
    from .ws import register_ws

    register_ws(app)
    return app


async def run_server(config: Config) -> None:
    configure_logging(config.log_dir, level=logging.INFO, to_stderr=True)
    app = build_app(config)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=config.daemon.host,
            port=config.daemon.port,
            log_level="warning",
        )
    )
    await server.serve()
