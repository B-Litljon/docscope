"""Regenerate the vendored tiny objects.inv fixture.

Run: ``uv run python tests/fixtures/make_inventory.py``
Kept in-tree so the binary fixture is reproducible and auditable.
"""
# sphobjinv's DataObjStr constructor is dynamically typed; kwargs verified at runtime.
# pyright: reportCallIssue=false

from pathlib import Path

import sphobjinv as soi

OBJECTS = [
    ("testpkg", "py:module", "index.html#$", "-"),
    ("testpkg.Thing", "py:class", "api.html#$", "-"),
    ("testpkg.Thing.do_it", "py:method", "api.html#$", "-"),
    ("testpkg.Thing.do_it_later", "py:method", "api.html#$", "-"),
    ("testpkg.helper", "py:function", "api.html#$", "-"),
]


def build() -> soi.Inventory:
    inv = soi.Inventory()
    inv.project = "testpkg"
    inv.version = "1.0"
    for name, dr, uri, dispname in OBJECTS:
        domain, role = dr.split(":")
        inv.objects.append(
            soi.DataObjStr(
                name=name,
                domain=domain,
                role=role,
                priority="1",
                uri=uri,
                dispname=dispname,
            )
        )
    return inv


def main() -> None:
    inv = build()
    out = Path(__file__).parent / "testpkg_objects.inv"
    ztext = soi.compress(inv.data_file())
    out.write_bytes(ztext)
    print(f"wrote {out} ({len(ztext)} bytes, {len(inv.objects)} objects)")


if __name__ == "__main__":
    main()
