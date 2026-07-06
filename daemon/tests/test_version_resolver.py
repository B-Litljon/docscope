"""VersionResolver: manifest parsing, lockfile priority, cache invalidation."""

from __future__ import annotations

from pathlib import Path

from docscope.cache import DocCache
from docscope.config import Config
from docscope.models import PackageVersion
from docscope.version_resolver import VersionMap, VersionResolver, canonical


def _write(root: Path, name: str, body: str) -> None:
    (root / name).write_text(body, "utf-8")


def _req(vm: VersionMap, name: str) -> PackageVersion:
    """Look up a package, asserting it was resolved (keeps tests type-safe)."""
    pv = vm.lookup(name)
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
