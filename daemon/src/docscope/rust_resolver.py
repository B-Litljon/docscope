"""Rust symbol resolution via the docs.rs / doc.rust-lang.org URL scheme.

rustdoc sites don't publish a Sphinx ``objects.inv``, but their URLs are a
predictable, versioned static layout:

    https://docs.rs/{crate}/{version}/{crate}/{module}/{kind}.{Item}.html

The item *kind* (``struct``/``enum``/``trait``/``fn``/…) isn't derivable from
the path alone, so we probe the small set of candidate kinds and take the first
that returns 200 — deterministic, no LLM, and version-pinned to the crate
version resolved from Cargo. ``std``/``core``/``alloc`` map to the (unversioned)
stdlib docs on doc.rust-lang.org.

Resolved URLs are memoised in the T1 inventory cache keyed by (crate, version,
symbol). Every failure falls through to ``None``.
"""

from __future__ import annotations

import logging

import httpx

from .cache import DocCache, InventoryEntry
from .config import Config
from .logging_setup import get_logger, log_event
from .models import ExtractedContext, ResolvedSymbol
from .version_resolver import VersionMap

log = get_logger("rust_resolver")

_STDLIB = {"std", "core", "alloc"}
# Candidate rustdoc filename kinds for an Uppercase item, in likelihood order.
_TYPE_KINDS = ["struct", "enum", "trait", "type", "union", "primitive"]
# ...and for a lowercase item (free function / macro / module).
_FN_KINDS = ["fn", "macro"]


class RustResolver:
    def __init__(self, cache: DocCache, config: Config, client: httpx.AsyncClient) -> None:
        self._cache = cache
        self._config = config
        self._client = client
        self._ttl = config.cache.inventory_ttl_days

    async def resolve(
        self, extracted: ExtractedContext, versions: VersionMap
    ) -> ResolvedSymbol | None:
        symbol = extracted.symbol
        if not symbol or "::" not in symbol:
            return None
        segs = [s for s in symbol.split("::") if s]
        crate = segs[0]

        if crate in _STDLIB:
            base = "https://doc.rust-lang.org/stable/"
            version, exact = "stable", False
        else:
            pv = versions.lookup(crate, "rust")
            if pv is None:
                return None
            version, exact = pv.version, pv.exact
            base = f"https://docs.rs/{crate}/{version}/"

        cached = await self._cache.get_symbol(crate, version, symbol, self._ttl)
        if cached is not None:
            cached.resolver = "docs.rs"
            cached.exact_version = exact
            return cached

        resolved = await self._probe(base, crate, version, segs, symbol, exact)
        if resolved is None:
            return None
        await self._cache.bulk_put_inventory(
            crate,
            version,
            [InventoryEntry(symbol, resolved.url, resolved.anchor, resolved.role)],
        )
        return resolved

    async def _probe(
        self, base: str, crate: str, version: str, segs: list[str], symbol: str, exact: bool
    ) -> ResolvedSymbol | None:
        item, method, module = self._split(segs)
        module_path = "/".join([crate, *module])
        anchor = f"method.{method}" if method else None

        if item[:1].isupper():
            kinds = _TYPE_KINDS
        elif item.isupper():
            kinds = ["constant", "static"]
        else:
            kinds = _FN_KINDS

        for kind in kinds:
            url = f"{base}{module_path}/{kind}.{item}.html"
            if await self._exists(url):
                full = f"{url}#{anchor}" if anchor else url
                log_event(log, logging.INFO, "docs.rs resolved", symbol=symbol, url=full)
                return ResolvedSymbol(
                    package=crate,
                    version=version,
                    symbol=symbol,
                    url=full,
                    anchor=anchor,
                    role=kind,
                    resolver="docs.rs",
                    exact_version=exact,
                )
        # Last resort: the module index page (covers modules themselves).
        idx = f"{base}{module_path}/{item}/index.html"
        if await self._exists(idx):
            return ResolvedSymbol(
                package=crate,
                version=version,
                symbol=symbol,
                url=idx,
                anchor=None,
                role="mod",
                resolver="docs.rs",
                exact_version=exact,
            )
        return None

    def _split(self, segs: list[str]) -> tuple[str, str | None, list[str]]:
        """Return (item, method, module_segments) from a path like
        ``std::collections::HashMap::new`` -> ("HashMap", "new", ["collections"])."""
        inner = segs[1:]  # drop the crate name
        if not inner:
            return segs[0], None, []
        last = inner[-1]
        if len(inner) >= 2 and last[:1].islower() and inner[-2][:1].isupper():
            return inner[-2], last, inner[:-2]
        return last, None, inner[:-1]

    async def _exists(self, url: str) -> bool:
        try:
            resp = await self._client.get(url, timeout=self._config.network.fetch_timeout_s)
        except httpx.HTTPError:
            return False
        return resp.status_code == 200
