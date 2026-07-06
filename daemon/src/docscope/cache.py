"""On-disk SQLite doc cache (tier T1).

Async access via :mod:`aiosqlite`. The daemon keeps a single long-lived
connection (WAL mode) guarded by an ``asyncio.Lock`` for writes; reads are
concurrent-safe under WAL.

Schema (per the spec) plus one additive table:

* ``inventories(package, version, symbol, url, anchor, fetched_at)`` — resolved
  intersphinx / doc-index entries, keyed by ``(package, version, symbol)``.
* ``doc_pages(url, version, content_md, fetched_at)`` — extracted doc sections.
* ``manifests(workspace_root, package, version, ecosystem, resolved_at)`` — the
  resolved dependency map for a workspace.
* ``manifest_sources(workspace_root, source_path, mtime)`` — *additive*: tracks
  the mtime of each manifest/lock file so the resolver can invalidate cleanly
  when a source file changes (the spec's "invalidated on manifest mtime change"
  requirement, which the bare ``manifests`` schema cannot express on its own).

TTLs are enforced at read time by comparing ``fetched_at`` against ``now``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from .models import PackageVersion, ResolvedSymbol

_SCHEMA = """
CREATE TABLE IF NOT EXISTS inventories (
    package    TEXT NOT NULL,
    version    TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    url        TEXT NOT NULL,
    anchor     TEXT,
    role       TEXT,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY (package, version, symbol)
);

CREATE TABLE IF NOT EXISTS doc_pages (
    url        TEXT PRIMARY KEY,
    version    TEXT,
    content_md TEXT NOT NULL,
    fetched_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS manifests (
    workspace_root TEXT NOT NULL,
    package        TEXT NOT NULL,
    version        TEXT NOT NULL,
    ecosystem      TEXT NOT NULL,
    source         TEXT NOT NULL,
    exact          INTEGER NOT NULL,
    resolved_at    INTEGER NOT NULL,
    PRIMARY KEY (workspace_root, package, ecosystem)
);

CREATE TABLE IF NOT EXISTS manifest_sources (
    workspace_root TEXT NOT NULL,
    source_path    TEXT NOT NULL,
    mtime          REAL NOT NULL,
    PRIMARY KEY (workspace_root, source_path)
);
"""

DAY = 86_400


@dataclass(slots=True)
class InventoryEntry:
    symbol: str
    url: str
    anchor: str | None
    role: str | None


class DocCache:
    """Async SQLite-backed cache. Construct, then ``await open()``."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> DocCache:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        return self

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def __aenter__(self) -> DocCache:
        return await self.open()

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    @property
    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("DocCache used before open()")
        return self._db

    # ---- inventories -----------------------------------------------------

    async def inventory_fresh(self, package: str, version: str, ttl_days: int) -> bool:
        """True if we hold a non-expired inventory for ``package@version``."""
        cutoff = int(time.time()) - ttl_days * DAY
        async with self._conn.execute(
            "SELECT MAX(fetched_at) AS m FROM inventories WHERE package=? AND version=?",
            (package, version),
        ) as cur:
            row = await cur.fetchone()
        return bool(row and row["m"] is not None and row["m"] >= cutoff)

    async def get_symbol(
        self, package: str, version: str, symbol: str, ttl_days: int
    ) -> ResolvedSymbol | None:
        cutoff = int(time.time()) - ttl_days * DAY
        async with self._conn.execute(
            "SELECT url, anchor, role, fetched_at FROM inventories "
            "WHERE package=? AND version=? AND symbol=?",
            (package, version, symbol),
        ) as cur:
            row = await cur.fetchone()
        if not row or row["fetched_at"] < cutoff:
            return None
        return ResolvedSymbol(
            package=package,
            version=version,
            symbol=symbol,
            url=row["url"],
            anchor=row["anchor"],
            role=row["role"],
            resolver="objects.inv",
        )

    async def find_symbols_like(
        self, package: str, version: str, prefix: str, ttl_days: int, limit: int = 8
    ) -> list[ResolvedSymbol]:
        """Return inventory symbols in ``package@version`` starting with
        ``prefix`` (used for completing half-typed expressions), shortest first
        so the closest completion ranks highest."""
        cutoff = int(time.time()) - ttl_days * DAY
        like = prefix.replace("%", r"\%").replace("_", r"\_") + "%"
        async with self._conn.execute(
            "SELECT symbol, url, anchor, role FROM inventories "
            "WHERE package=? AND version=? AND fetched_at>=? AND symbol LIKE ? ESCAPE '\\' "
            "ORDER BY LENGTH(symbol) ASC LIMIT ?",
            (package, version, cutoff, like, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [
            ResolvedSymbol(
                package=package,
                version=version,
                symbol=r["symbol"],
                url=r["url"],
                anchor=r["anchor"],
                role=r["role"],
                resolver="objects.inv",
            )
            for r in rows
        ]

    async def bulk_put_inventory(
        self, package: str, version: str, entries: list[InventoryEntry]
    ) -> None:
        now = int(time.time())
        await self._conn.executemany(
            "INSERT OR REPLACE INTO inventories "
            "(package, version, symbol, url, anchor, role, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(package, version, e.symbol, e.url, e.anchor, e.role, now) for e in entries],
        )
        await self._conn.commit()

    # ---- doc pages -------------------------------------------------------

    async def get_page(self, url: str, ttl_days: int) -> str | None:
        cutoff = int(time.time()) - ttl_days * DAY
        async with self._conn.execute(
            "SELECT content_md, fetched_at FROM doc_pages WHERE url=?", (url,)
        ) as cur:
            row = await cur.fetchone()
        if not row or row["fetched_at"] < cutoff:
            return None
        return row["content_md"]

    async def put_page(self, url: str, version: str | None, content_md: str) -> None:
        await self._conn.execute(
            "INSERT OR REPLACE INTO doc_pages (url, version, content_md, fetched_at) "
            "VALUES (?, ?, ?, ?)",
            (url, version, content_md, int(time.time())),
        )
        await self._conn.commit()

    # ---- manifests -------------------------------------------------------

    async def get_manifest(self, workspace_root: str) -> list[PackageVersion]:
        async with self._conn.execute(
            "SELECT package, version, ecosystem, source, exact FROM manifests "
            "WHERE workspace_root=?",
            (workspace_root,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            PackageVersion(
                package=r["package"],
                version=r["version"],
                ecosystem=r["ecosystem"],
                source=r["source"],
                exact=bool(r["exact"]),
            )
            for r in rows
        ]

    async def replace_manifest(
        self,
        workspace_root: str,
        packages: list[PackageVersion],
        sources: dict[str, float],
    ) -> None:
        now = int(time.time())
        await self._conn.execute("DELETE FROM manifests WHERE workspace_root=?", (workspace_root,))
        await self._conn.execute(
            "DELETE FROM manifest_sources WHERE workspace_root=?", (workspace_root,)
        )
        await self._conn.executemany(
            "INSERT OR REPLACE INTO manifests "
            "(workspace_root, package, version, ecosystem, source, exact, resolved_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (workspace_root, p.package, p.version, p.ecosystem, p.source, int(p.exact), now)
                for p in packages
            ],
        )
        await self._conn.executemany(
            "INSERT OR REPLACE INTO manifest_sources (workspace_root, source_path, mtime) "
            "VALUES (?, ?, ?)",
            [(workspace_root, path, mtime) for path, mtime in sources.items()],
        )
        await self._conn.commit()

    async def get_manifest_sources(self, workspace_root: str) -> dict[str, float]:
        async with self._conn.execute(
            "SELECT source_path, mtime FROM manifest_sources WHERE workspace_root=?",
            (workspace_root,),
        ) as cur:
            rows = await cur.fetchall()
        return {r["source_path"]: r["mtime"] for r in rows}
