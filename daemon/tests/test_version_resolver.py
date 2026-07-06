"""VersionResolver: manifest parsing, lockfile priority, cache invalidation."""

from __future__ import annotations

from pathlib import Path

from docscope.cache import DocCache
from docscope.config import Config
from docscope.models import PackageVersion
from docscope.version_resolver import VersionMap, VersionResolver, canonical


def _write(root: Path, name: str, body: str) -> None:
    (root / name).write_text(body, "utf-8")


def _req(vm: VersionMap, name: str, ecosystem: str | None = None) -> PackageVersion:
    """Look up a package, asserting it was resolved (keeps tests type-safe)."""
    pv = vm.lookup(name, ecosystem)
    assert pv is not None, f"{name!r} not resolved"
    return pv


async def test_pyproject_pep621_exact_and_range(tmp_path: Path, cache: DocCache):
    _write(
        tmp_path,
        "pyproject.toml",
        ('[project]\nname="x"\nversion="0"\ndependencies = ["polars==1.2.1", "numpy>=1.26,<2"]\n'),
    )
    vm = await VersionResolver(cache, Config()).resolve(tmp_path)
    polars = vm.lookup("polars")
    numpy = vm.lookup("numpy")
    assert polars and polars.version == "1.2.1" and polars.exact
    assert numpy and numpy.version == "1.26" and not numpy.exact


async def test_lockfile_beats_manifest(tmp_path: Path, cache: DocCache):
    _write(
        tmp_path,
        "pyproject.toml",
        ('[project]\nname="x"\nversion="0"\ndependencies = ["polars>=1.0"]\n'),
    )
    _write(tmp_path, "uv.lock", ('[[package]]\nname = "polars"\nversion = "1.2.1"\n'))
    vm = await VersionResolver(cache, Config()).resolve(tmp_path)
    polars = vm.lookup("polars")
    assert polars and polars.version == "1.2.1"
    assert polars.exact and polars.source == "uv.lock"


async def test_requirements_exact(tmp_path: Path, cache: DocCache):
    _write(tmp_path, "requirements.txt", "polars==1.2.1\n# comment\nnumpy>=1.26\n")
    vm = await VersionResolver(cache, Config()).resolve(tmp_path)
    assert _req(vm, "polars").version == "1.2.1"
    assert not _req(vm, "numpy").exact


async def test_import_alias_lookup(tmp_path: Path, cache: DocCache):
    _write(tmp_path, "requirements.txt", "beautifulsoup4==4.12.3\n")
    vm = await VersionResolver(cache, Config()).resolve(tmp_path)
    # imported as `bs4`, distributed as `beautifulsoup4`
    assert _req(vm, "bs4").version == "4.12.3"


async def test_cache_hit_then_mtime_invalidation(tmp_path: Path, cache: DocCache):
    path = tmp_path / "requirements.txt"
    path.write_text("polars==1.0.0\n", "utf-8")
    resolver = VersionResolver(cache, Config())

    vm1 = await resolver.resolve(tmp_path)
    assert _req(vm1, "polars").version == "1.0.0"

    # Second resolve with unchanged file -> served from cache (same result).
    vm2 = await resolver.resolve(tmp_path)
    assert _req(vm2, "polars").version == "1.0.0"

    # Change the file (and bump mtime) -> cache invalidated, new version.
    import os
    import time

    path.write_text("polars==2.0.0\n", "utf-8")
    future = time.time() + 10
    os.utime(path, (future, future))
    vm3 = await resolver.resolve(tmp_path)
    assert _req(vm3, "polars").version == "2.0.0"


async def test_poetry_dependencies(tmp_path: Path, cache: DocCache):
    _write(
        tmp_path,
        "pyproject.toml",
        ('[tool.poetry.dependencies]\npython = "^3.12"\npolars = "1.2.1"\nrequests = "^2.31"\n'),
    )
    vm = await VersionResolver(cache, Config()).resolve(tmp_path)
    assert _req(vm, "polars").version == "1.2.1"
    assert _req(vm, "polars").exact
    assert _req(vm, "requests").version == "2.31"
    assert not _req(vm, "requests").exact


def test_canonical():
    assert canonical("Foo_Bar.baz") == "foo-bar-baz"
    assert canonical("beautifulsoup4") == "beautifulsoup4"


# ---- Rust / npm (M4) -----------------------------------------------------


async def test_cargo_lock_exact(tmp_path: Path, cache: DocCache):
    _write(
        tmp_path,
        "Cargo.lock",
        (
            '[[package]]\nname = "serde_json"\nversion = "1.0.117"\n\n'
            '[[package]]\nname = "tokio"\nversion = "1.38.0"\n'
        ),
    )
    vm = await VersionResolver(cache, Config()).resolve(tmp_path)
    sj = _req(vm, "serde_json", "rust")
    assert sj.version == "1.0.117" and sj.exact and sj.ecosystem == "rust"
    assert _req(vm, "tokio", "rust").version == "1.38.0"


async def test_cargo_toml_ranges(tmp_path: Path, cache: DocCache):
    _write(
        tmp_path,
        "Cargo.toml",
        ('[dependencies]\nserde = "1.0"\ntokio = { version = "1.38", features = ["full"] }\n'),
    )
    vm = await VersionResolver(cache, Config()).resolve(tmp_path)
    assert _req(vm, "serde", "rust").version == "1.0"
    assert _req(vm, "tokio", "rust").version == "1.38"


async def test_cargo_lock_beats_toml(tmp_path: Path, cache: DocCache):
    _write(tmp_path, "Cargo.toml", '[dependencies]\nserde = "1.0"\n')
    _write(tmp_path, "Cargo.lock", '[[package]]\nname = "serde"\nversion = "1.0.203"\n')
    vm = await VersionResolver(cache, Config()).resolve(tmp_path)
    serde = _req(vm, "serde", "rust")
    assert serde.version == "1.0.203" and serde.source == "Cargo.lock"


async def test_package_json_and_lock(tmp_path: Path, cache: DocCache):
    _write(
        tmp_path,
        "package.json",
        ('{"dependencies": {"lodash": "^4.17.21"}, "devDependencies": {"jest": "29.7.0"}}'),
    )
    _write(
        tmp_path,
        "package-lock.json",
        ('{"packages": {"node_modules/lodash": {"version": "4.17.21"}}}'),
    )
    vm = await VersionResolver(cache, Config()).resolve(tmp_path)
    lodash = _req(vm, "lodash", "npm")
    assert lodash.version == "4.17.21" and lodash.exact  # lock wins
    assert _req(vm, "jest", "npm").version == "29.7.0"


async def test_ecosystems_coexist(tmp_path: Path, cache: DocCache):
    _write(tmp_path, "requirements.txt", "polars==1.2.1\n")
    _write(tmp_path, "Cargo.lock", '[[package]]\nname = "serde"\nversion = "1.0.203"\n')
    _write(tmp_path, "package.json", '{"dependencies": {"lodash": "4.17.21"}}')
    vm = await VersionResolver(cache, Config()).resolve(tmp_path)
    assert _req(vm, "polars", "python").version == "1.2.1"
    assert _req(vm, "serde", "rust").version == "1.0.203"
    assert _req(vm, "lodash", "npm").version == "4.17.21"
