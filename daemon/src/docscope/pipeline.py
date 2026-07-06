"""The docscope request pipeline: buffer context in, doc card out.

Wires together the deterministic fast path (ContextExtractor → VersionResolver →
SymbolResolver → Retriever → Assembler). The slow path (IntentClassifier via
LiteLLM) and T3 web search are attached in M3 behind the same interface; the
pipeline already exposes the seams so those tiers slot in without touching the
fast path.

The pipeline owns the shared async resources (SQLite connection, httpx client)
and is created once per daemon process.
"""

from __future__ import annotations

import logging
import time

import httpx

from .assembler import Assembler
from .cache import DocCache
from .config import Config
from .context_extractor import ContextExtractor
from .logging_setup import get_logger, log_event
from .models import BufferContext, DocCard, ExtractedContext, LookupError
from .registry import DocRegistry
from .retriever import Retriever
from .symbol_resolver import SymbolResolver
from .version_resolver import VersionResolver

log = get_logger("pipeline")


class Pipeline:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._cache = DocCache(config.cache.db_path)
        self._client: httpx.AsyncClient | None = None
        self._extractor = ContextExtractor()
        self._version_resolver = VersionResolver(self._cache, config)
        self._registry = DocRegistry(config.registry)
        self._symbol_resolver: SymbolResolver | None = None
        self._retriever: Retriever | None = None
        self._assembler = Assembler()
        # Populated in M3.
        self._intent_classifier = None
        self._search_provider = None

    async def start(self, client: httpx.AsyncClient | None = None) -> None:
        await self._cache.open()
        self._client = client or httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": self._config.network.user_agent},
        )
        self._symbol_resolver = SymbolResolver(
            self._cache, self._registry, self._config, self._client
        )
        self._retriever = Retriever(self._cache, self._config, self._client)
        log_event(log, logging.INFO, "pipeline started", port=self._config.daemon.port)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        await self._cache.close()

    async def __aenter__(self) -> Pipeline:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    def extract_context(self, ctx: BufferContext) -> ExtractedContext:
        """Run only the (cheap, no-I/O) context extraction. Used by the WS layer
        to gate the debounce on whether the symbol under the cursor changed."""
        return self._extractor.extract(ctx)

    async def lookup(self, ctx: BufferContext) -> DocCard | LookupError:
        if self._symbol_resolver is None or self._retriever is None:
            raise RuntimeError("Pipeline.lookup called before start()")
        t0 = time.perf_counter()

        extracted = self._extractor.extract(ctx)
        versions = await self._version_resolver.resolve(ctx.workspace_root)

        resolved = await self._symbol_resolver.resolve(extracted, versions)
        # M3 attaches the LLM/T3 fallback here when `resolved is None`.

        if resolved is None:
            elapsed = (time.perf_counter() - t0) * 1000
            log_event(
                log,
                logging.INFO,
                "lookup unresolved",
                symbol=extracted.symbol,
                elapsed_ms=round(elapsed, 1),
            )
            return LookupError(
                reason="unresolved",
                detail=(
                    f"No documentation source for '{extracted.symbol or ctx.file_path}'. "
                    "The symbol may be a local variable, an un-imported name, or a library "
                    "without a published Sphinx inventory."
                ),
                elapsed_ms=elapsed,
            )

        retrieval = await self._retriever.retrieve(resolved)
        section = retrieval.section if retrieval else None
        tier = retrieval.tier if retrieval else "none"

        elapsed = (time.perf_counter() - t0) * 1000
        card = self._assembler.assemble(resolved, section, elapsed_ms=elapsed, cache_tier=tier)
        log_event(
            log,
            logging.INFO,
            "lookup ok",
            symbol=resolved.symbol,
            package=resolved.package,
            version=resolved.version,
            tier=tier,
            exact=resolved.exact_version,
            elapsed_ms=round(elapsed, 1),
        )
        return card
