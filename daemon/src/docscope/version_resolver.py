"""Resolve a workspace's dependency manifest into {package -> exact version}.

Version-pinned retrieval is docscope's core value, so this resolver prefers
*lockfiles* (exact) over *manifests* (ranges), and records the provenance of
every version so the doc card can honestly flag when a version is approximate.

MVP ecosystems: Python (``uv.lock`` > ``poetry.lock`` > ``requirements.txt`` >
``pyproject.toml``). Rust/npm parsers are added in M4 but the dispatch and
cache plumbing are already ecosystem-agnostic.

Results are cached per workspace in SQLite and invalidated when any source
file's mtime changes.
"""

from __future__ import annotations

import logging
import re
import tomllib
from pathlib import Path

from .cache import DocCache
from .config import Config
from .logging_setup import get_logger, log_event
from .models import PackageVersion

log = get_logger("version_resolver")

# import-name -> PyPI distribution name, for the common mismatches.
_IMPORT_ALIASES = {
    "bs4": "beautifulsoup4",
    "PIL": "pillow",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "yaml": "pyyaml",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
}

_PYTHON_SOURCE_PRIORITY = ["uv.lock", "poetry.lock", "requirements.txt", "pyproject.toml"]

_REQ_LINE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(?P<spec>.*?)\s*(?:#.*)?$"
)
_EXACT_SPEC = re.compile(r"==\s*(?P<v>[A-Za-z0-9][A-Za-z0-9.+!*-]*)")
_ANY_SPEC = re.compile(r"(?P<v>[0-9][A-Za-z0-9.+!*-]*)")


def canonical(name: str) -> str:
    """PEP 503 canonical form: lowercase, runs of [-_.] collapsed to '-'."""
    return re.sub(r"[-_.]+", "-", name).lower()


class VersionMap:
    """A resolved dependency set with import-name-aware lookup."""

    def __init__(self, packages: list[PackageVersion]) -> None:
        self.packages = packages
        self._by_canonical: dict[str, PackageVersion] = {}
        for p in packages:
            # First writer wins if two sources disagree; callers order by
            # priority so the exact lockfile entry lands first.
            self._by_canonical.setdefault(canonical(p.package), p)

    def lookup(self, import_name: str) -> PackageVersion | None:
        """Resolve a top-level import name to a version, applying aliases."""
        dist = _IMPORT_ALIASES.get(import_name, import_name)
        return self._by_canonical.get(canonical(dist))

    def __len__(self) -> int:
        return len(self.packages)


class VersionResolver:
    def __init__(self, cache: DocCache, config: Config) -> None:
        self._cache = cache
        self._config = config

    async def resolve(self, workspace_root: str | Path | None) -> VersionMap:
        if workspace_root is None:
            return VersionMap([])
        root = Path(workspace_root).expanduser().resolve()
        if not root.is_dir():
            return VersionMap([])

        sources = self._discover_sources(root)
        current_mtimes = {str(p): p.stat().st_mtime for p in sources}

        cached_sources = await self._cache.get_manifest_sources(str(root))
        if cached_sources and cached_sources == current_mtimes:
            cached = await self._cache.get_manifest(str(root))
            if cached:
                log_event(
                    log,
                    logging.INFO,
                    "manifest cache hit",
                    workspace=str(root),
                    packages=len(cached),
                )
                return VersionMap(cached)

        packages = self._parse_all(root, sources)
        await self._cache.replace_manifest(str(root), packages, current_mtimes)
        log_event(
            log,
            logging.INFO,
            "manifest resolved",
            workspace=str(root),
            packages=len(packages),
            sources=[p.name for p in sources],
        )
        return VersionMap(packages)

    # ---- discovery -------------------------------------------------------

    def _discover_sources(self, root: Path) -> list[Path]:
        names = [
            "uv.lock",
            "poetry.lock",
            "requirements.txt",
            "pyproject.toml",
            "Cargo.lock",
            "Cargo.toml",
            "package.json",
        ]
        return [root / n for n in names if (root / n).is_file()]

    def _parse_all(self, root: Path, sources: list[Path]) -> list[PackageVersion]:
        present = {p.name for p in sources}
        packages: list[PackageVersion] = []
        seen: set[str] = set()

        def add_from(pvs: list[PackageVersion]) -> None:
            for pv in pvs:
                key = canonical(pv.package)
                if key in seen:
                    continue
                seen.add(key)
                packages.append(pv)

        # Python: exact lockfiles first so their entries win.
        for name in _PYTHON_SOURCE_PRIORITY:
            if name not in present:
                continue
            path = root / name
            try:
                if name == "uv.lock":
                    add_from(_parse_uv_lock(path))
                elif name == "poetry.lock":
                    add_from(_parse_poetry_lock(path))
                elif name == "requirements.txt":
                    add_from(_parse_requirements(path))
                elif name == "pyproject.toml":
                    add_from(_parse_pyproject(path))
            except Exception as exc:  # never let one bad file kill resolution
                log_event(
                    log, logging.WARNING, "manifest parse failed", file=str(path), error=str(exc)
                )
        return packages


# --------------------------------------------------------------------------
# Ecosystem parsers
# --------------------------------------------------------------------------


def _pv(name: str, version: str, source: str, exact: bool) -> PackageVersion:
    return PackageVersion(
        package=name, version=version, ecosystem="python", source=source, exact=exact
    )


def _parse_uv_lock(path: Path) -> list[PackageVersion]:
    data = tomllib.loads(path.read_text("utf-8"))
    out: list[PackageVersion] = []
    for pkg in data.get("package", []):
        name = pkg.get("name")
        version = pkg.get("version")
        if name and version:
            out.append(_pv(name, str(version), "uv.lock", exact=True))
    return out


def _parse_poetry_lock(path: Path) -> list[PackageVersion]:
    data = tomllib.loads(path.read_text("utf-8"))
    out: list[PackageVersion] = []
    for pkg in data.get("package", []):
        name = pkg.get("name")
        version = pkg.get("version")
        if name and version:
            out.append(_pv(name, str(version), "poetry.lock", exact=True))
    return out


def _parse_requirements(path: Path) -> list[PackageVersion]:
    out: list[PackageVersion] = []
    for raw in path.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-", "git+", "http")):
            continue
        m = _REQ_LINE.match(line)
        if not m:
            continue
        name = m.group("name")
        spec = m.group("spec") or ""
        exact = _EXACT_SPEC.search(spec)
        if exact:
            out.append(_pv(name, exact.group("v"), "requirements.txt", exact=True))
        else:
            any_v = _ANY_SPEC.search(spec)
            if any_v:
                out.append(_pv(name, any_v.group("v"), "requirements.txt", exact=False))
    return out


def _parse_pyproject(path: Path) -> list[PackageVersion]:
    data = tomllib.loads(path.read_text("utf-8"))
    out: list[PackageVersion] = []

    # PEP 621 dependencies (list of PEP 508 strings)
    project = data.get("project", {})
    deps: list[str] = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        deps.extend(group)
    for spec_str in deps:
        pv = _pep508_to_pv(spec_str)
        if pv:
            out.append(pv)

    # Poetry-style dependencies (table)
    poetry = data.get("tool", {}).get("poetry", {})
    for name, constraint in poetry.get("dependencies", {}).items():
        if name.lower() == "python":
            continue
        out.append(_poetry_constraint_to_pv(name, constraint))
    return out


def _pep508_to_pv(spec_str: str) -> PackageVersion | None:
    m = _REQ_LINE.match(spec_str.split(";", 1)[0].strip())
    if not m:
        return None
    name = m.group("name")
    spec = m.group("spec") or ""
    exact = _EXACT_SPEC.search(spec)
    if exact:
        return _pv(name, exact.group("v"), "pyproject", exact=True)
    any_v = _ANY_SPEC.search(spec)
    if any_v:
        return _pv(name, any_v.group("v"), "pyproject", exact=False)
    return _pv(name, "*", "pyproject", exact=False)


def _poetry_constraint_to_pv(name: str, constraint: object) -> PackageVersion:
    if isinstance(constraint, dict):
        version = str(constraint.get("version", "*"))
    else:
        version = str(constraint)
    cleaned = version.lstrip("^~>=< ")
    exact = bool(re.fullmatch(r"[0-9][A-Za-z0-9.+!-]*", version.strip()))
    return _pv(name, cleaned or "*", "pyproject", exact=exact)
