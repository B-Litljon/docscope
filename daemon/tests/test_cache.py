"""DocCache: inventory, doc pages, manifests, TTL, prefix search."""

from __future__ import annotations

import time

from docscope.cache import DAY, DocCache, InventoryEntry
from docscope.models import PackageVersion


async def test_inventory_roundtrip_and_prefix(cache: DocCache):
    entries = [
        InventoryEntry("pkg.Thing", "http://x/a.html#pkg.Thing", "pkg.Thing", "class"),
        InventoryEntry("pkg.Thing.run", "http://x/a.html#pkg.Thing.run", "pkg.Thing.run", "method"),
        InventoryEntry("pkg.Thing.run_all", "http://x/a.html#run_all", "run_all", "method"),
    ]
    await cache.bulk_put_inventory("pkg", "1.0", entries)

    assert await cache.inventory_fresh("pkg", "1.0", 7)
    got = await cache.get_symbol("pkg", "1.0", "pkg.Thing.run", 7)
    assert got and got.role == "method"

    # prefix completion: shortest match first
    like = await cache.find_symbols_like("pkg", "1.0", "pkg.Thing.run", 7)
    assert [r.symbol for r in like] == ["pkg.Thing.run", "pkg.Thing.run_all"]


async def test_inventory_ttl_expiry(cache: DocCache):
    await cache.bulk_put_inventory("pkg", "1.0", [InventoryEntry("pkg.x", "http://x", None, None)])
    # Backdate fetched_at beyond a 7-day TTL.
    old = int(time.time()) - 8 * DAY
    await cache._conn.execute("UPDATE inventories SET fetched_at=?", (old,))
    await cache._conn.commit()
    assert not await cache.inventory_fresh("pkg", "1.0", 7)
    assert await cache.get_symbol("pkg", "1.0", "pkg.x", 7) is None


async def test_doc_page_roundtrip_and_ttl(cache: DocCache):
    await cache.put_page("http://x/p", "1.0", '{"body_md":"hi"}')
    assert await cache.get_page("http://x/p", 30) == '{"body_md":"hi"}'
    old = int(time.time()) - 31 * DAY
    await cache._conn.execute("UPDATE doc_pages SET fetched_at=?", (old,))
    await cache._conn.commit()
    assert await cache.get_page("http://x/p", 30) is None


async def test_manifest_replace_and_sources(cache: DocCache):
    pkgs = [
        PackageVersion(
            package="polars", version="1.2.1", ecosystem="python", source="uv.lock", exact=True
        )
    ]
    await cache.replace_manifest("/ws", pkgs, {"/ws/uv.lock": 123.0})
    assert (await cache.get_manifest("/ws"))[0].version == "1.2.1"
    assert await cache.get_manifest_sources("/ws") == {"/ws/uv.lock": 123.0}

    # Replace wipes prior entries.
    await cache.replace_manifest("/ws", [], {"/ws/uv.lock": 456.0})
    assert await cache.get_manifest("/ws") == []
    assert await cache.get_manifest_sources("/ws") == {"/ws/uv.lock": 456.0}
