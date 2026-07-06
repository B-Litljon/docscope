"""Registry mapping a package to its Sphinx ``objects.inv`` location(s).

Each package resolves to an ordered list of candidate inventory URLs, best
(most version-pinned) first. Templates may embed ``{version}``,
``{major_minor}``, ``{major}`` and ``{minor}``; a candidate whose template
needs a token that can't be filled from the resolved version is skipped.

The built-in table is deliberately small and high-confidence. The long tail is
covered at runtime by (a) a generic ReadTheDocs guess and (b) PyPI-metadata
discovery in the resolver. User config can add or override any package.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import RegistryOverride


@dataclass(slots=True)
class DocSource:
    inv_url: str
    versioned: bool

    @property
    def base_url(self) -> str:
        """Directory containing ``objects.inv`` — used to resolve relative uris."""
        return self.inv_url.rsplit("/", 1)[0] + "/"


# package (canonical) -> list of (inv_url_template, versioned)
_BUILTIN: dict[str, list[tuple[str, bool]]] = {
    "polars": [("https://docs.pola.rs/api/python/stable/objects.inv", False)],
    "numpy": [
        ("https://numpy.org/doc/{major_minor}/objects.inv", True),
        ("https://numpy.org/doc/stable/objects.inv", False),
    ],
    "pandas": [
        ("https://pandas.pydata.org/pandas-docs/version/{version}/objects.inv", True),
        ("https://pandas.pydata.org/docs/objects.inv", False),
    ],
    "scipy": [("https://docs.scipy.org/doc/scipy/objects.inv", False)],
    "matplotlib": [
        ("https://matplotlib.org/{version}/objects.inv", True),
        ("https://matplotlib.org/stable/objects.inv", False),
    ],
    "requests": [("https://requests.readthedocs.io/en/latest/objects.inv", False)],
    "flask": [
        ("https://flask.palletsprojects.com/en/{major_minor}.x/objects.inv", True),
        ("https://flask.palletsprojects.com/en/stable/objects.inv", False),
    ],
    "click": [("https://click.palletsprojects.com/en/stable/objects.inv", False)],
    "django": [("https://docs.djangoproject.com/en/{major_minor}/_objects/", True)],
    "pydantic": [
        ("https://docs.pydantic.dev/{major_minor}/objects.inv", True),
        ("https://docs.pydantic.dev/latest/objects.inv", False),
    ],
    "aiohttp": [("https://docs.aiohttp.org/en/stable/objects.inv", False)],
    "rich": [("https://rich.readthedocs.io/en/stable/objects.inv", False)],
    "sqlalchemy": [("https://docs.sqlalchemy.org/en/{major}{minor}/objects.inv", True)],
}


def _version_tokens(version: str) -> dict[str, str] | None:
    """Extract {version, major_minor, major, minor} from a version string.

    Returns ``None`` for non-numeric versions (e.g. ``"*"``), which disables
    version-templated candidates.
    """
    m = re.match(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", version)
    if not m or m.group(1) is None:
        return None
    major = m.group(1)
    minor = m.group(2) or "0"
    return {
        "version": version,
        "major_minor": f"{major}.{minor}",
        "major": major,
        "minor": minor,
    }


def _expand(template: str, tokens: dict[str, str] | None) -> str | None:
    needs_tokens = "{" in template
    if not needs_tokens:
        return template
    if tokens is None:
        return None
    try:
        return template.format(**tokens)
    except (KeyError, IndexError):
        return None


class DocRegistry:
    def __init__(self, overrides: dict[str, RegistryOverride] | None = None) -> None:
        self._overrides = {
            re.sub(r"[-_.]+", "-", k).lower(): v for k, v in (overrides or {}).items()
        }

    def candidates(self, package: str, version: str) -> list[DocSource]:
        """Ordered candidate inventories for ``package@version``."""
        canon = re.sub(r"[-_.]+", "-", package).lower()
        tokens = _version_tokens(version)
        out: list[DocSource] = []

        override = self._overrides.get(canon)
        if override and override.inv_url:
            expanded = _expand(override.inv_url, tokens)
            if expanded:
                out.append(DocSource(expanded, override.versioned))

        for template, versioned in _BUILTIN.get(canon, []):
            expanded = _expand(template, tokens)
            if expanded:
                out.append(DocSource(expanded, versioned))

        # Generic ReadTheDocs guesses for the long tail.
        rtd_host = f"https://{canon}.readthedocs.io/en"
        if tokens is not None:
            out.append(DocSource(f"{rtd_host}/v{version}/objects.inv", True))
            out.append(DocSource(f"{rtd_host}/{version}/objects.inv", True))
        out.append(DocSource(f"{rtd_host}/stable/objects.inv", False))
        out.append(DocSource(f"{rtd_host}/latest/objects.inv", False))

        # De-duplicate preserving order.
        seen: set[str] = set()
        deduped: list[DocSource] = []
        for src in out:
            if src.inv_url not in seen:
                seen.add(src.inv_url)
                deduped.append(src)
        return deduped
