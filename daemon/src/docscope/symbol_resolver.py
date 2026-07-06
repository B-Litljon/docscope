"""Fast-path symbol resolution via Sphinx ``objects.inv`` inventories.

Given a fully-qualified symbol (``polars.DataFrame.join_asof``) and the resolved
version map, this produces a version-pinned documentation URL *without an LLM*:

1. Determine the package (symbol root) and its installed version.
2. Serve from the T1 SQLite inventory cache when fresh.
3. On a cold cache, download the package's ``objects.inv`` (trying registry +
   ReadTheDocs + PyPI-metadata candidates in order), index every entry into T1,
   then resolve.
4. Exact match preferred. For half-typed symbols, fall back to a deterministic
   prefix search over the inventory — so ``pl.DataFrame.join_as`` resolves to
   ``join_asof`` before any model is consulted (LLM-last).

All network I/O has timeouts and never raises out of :meth:`resolve`; a failure
returns ``None`` so the pipeline can fall through to the next tier.
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin

import httpx
import sphobjinv as soi

from .cache import DocCache, InventoryEntry
from .config import Config
from .logging_setup import get_logger, log_event
from .models import ExtractedContext, ResolvedSymbol
from .registry import DocRegistry, DocSource
from .version_resolver import VersionMap

log = get_logger("symbol_resolver")


def _expand_uri(uri: str, name: str) -> str:
    """Expand the intersphinx ``$`` placeholder (``page.html#$`` -> name)."""
    if uri.endswith("$"):
        return uri[:-1] + name
    return uri


def _anchor_of(uri: str) -> str | None:
    return uri.split("#", 1)[1] if "#" in uri else None


class SymbolResolver:
    def __init__(
        self,
        cache: DocCache,
        registry: DocRegistry,
        config: Config,
        client: httpx.AsyncClient,
    ) -> None:
        self._cache = cache
        self._registry = registry
        self._config = config
        self._client = client
        self._inv_ttl = config.cache.inventory_ttl_days

    async def resolve(self, ctx: ExtractedContext, versions: VersionMap) -> ResolvedSymbol | None:
        symbol = ctx.symbol
        if not symbol or "." not in symbol:
            return None
        package = symbol.split(".", 1)[0]

        pv = versions.lookup(package)
        version = pv.version if pv else "latest"
        exact_known = bool(pv and pv.exact)

        # Ensure the inventory for this package@version is indexed in T1.
        source = await self._ensure_inventory(package, version)
        if source is None:
            log_event(
                log, logging.INFO, "no inventory", package=package, version=version, symbol=symbol
            )
            return None

        resolved = await self._lookup(package, version, symbol)
        if resolved is None:
            return None

        # Version pinning is real only when both the installed version is exact
        # AND the doc URL we found actually embeds a version.
        resolved.exact_version = exact_known and source.versioned
        return resolved

    async def _lookup(self, package: str, version: str, symbol: str) -> ResolvedSymbol | None:
        exact = await self._cache.get_symbol(package, version, symbol, self._inv_ttl)
        if exact is not None:
            return exact

        # Exact miss: complete the (possibly half-typed) symbol by prefix search
        # over the inventory, shortest completion first. This is the
        # deterministic answer to `pl.DataFrame.join_as` -> `join_asof`.
        candidates = await self._cache.find_symbols_like(package, version, symbol, self._inv_ttl)
        return candidates[0] if candidates else None

    async def _ensure_inventory(self, package: str, version: str) -> DocSource | None:
        """Guarantee an inventory for ``package@version`` is present & fresh in
        T1, returning the :class:`DocSource` it came from (or a cached marker)."""
        if await self._cache.inventory_fresh(package, version, self._inv_ttl):
            # We don't persist which source produced a cached inventory; assume
            # the best (first) candidate's versioned-ness for the flag. This is
            # only used to decide the "version pinned" badge and is refreshed on
            # the next cold fetch.
            cands = self._registry.candidates(package, version)
            return cands[0] if cands else DocSource("cached://", versioned=False)

        candidates = self._registry.candidates(package, version)
        for source in candidates:
            inv = await self._fetch_inventory(source.inv_url)
            if inv is None:
                continue
            entries = self._index_entries(inv, source)
            if not entries:
                continue
            await self._cache.bulk_put_inventory(package, version, entries)
            log_event(
                log,
                logging.INFO,
                "inventory indexed",
                package=package,
                version=version,
                entries=len(entries),
                source=source.inv_url,
            )
            return source
        return None

    async def _fetch_inventory(self, url: str) -> soi.Inventory | None:
        try:
            resp = await self._client.get(url, timeout=self._config.network.fetch_timeout_s)
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            log_event(log, logging.DEBUG, "inventory fetch error", url=url, error=str(exc))
            return None
        if resp.status_code != 200 or not resp.content:
            return None
        try:
            # sphobjinv's __init__ is dynamically typed (**kwargs); these calls
            # are verified at runtime by the resolver tests.
            return soi.Inventory(zlib=resp.content)  # pyright: ignore[reportCallIssue]
        except Exception:
            # Some inventories (e.g. Django's _objects/) are served uncompressed
            # or in a plaintext-ish form; try the generic constructor.
            try:
                return soi.Inventory(resp.content)  # pyright: ignore[reportCallIssue]
            except Exception as exc:  # not a parseable inventory
                log_event(log, logging.DEBUG, "inventory parse error", url=url, error=str(exc))
                return None

    def _index_entries(self, inv: soi.Inventory, source: DocSource) -> list[InventoryEntry]:
        entries: list[InventoryEntry] = []
        for obj in inv.objects:
            uri = _expand_uri(obj.uri, obj.name)
            full_url = urljoin(source.base_url, uri)
            entries.append(
                InventoryEntry(
                    symbol=obj.name,
                    url=full_url,
                    anchor=_anchor_of(uri),
                    role=obj.role,
                )
            )
        return entries
