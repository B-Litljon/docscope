"""JS/TS builtin resolution via MDN's predictable Global Objects URL scheme.

For standard-library globals (``Array``, ``JSON``, ``Promise``, ``Math``, …)
MDN URLs are deterministic:

    https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/{Object}[/{member}]

We resolve only these builtins deterministically; npm-package symbols have no
uniform doc scheme and fall through to the LLM / web-search tiers. MDN tracks
the living web platform, so these cards are intentionally not version-pinned.
"""

from __future__ import annotations

from .config import Config
from .models import ExtractedContext, ResolvedSymbol
from .version_resolver import VersionMap

_MDN_BASE = "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/"

_GLOBALS = {
    "Array",
    "Object",
    "JSON",
    "Math",
    "Promise",
    "String",
    "Number",
    "Boolean",
    "Date",
    "Map",
    "Set",
    "Symbol",
    "RegExp",
    "Error",
    "Function",
    "Reflect",
    "Proxy",
    "BigInt",
    "WeakMap",
    "WeakSet",
    "globalThis",
    "Intl",
}


class MdnResolver:
    def __init__(self, config: Config) -> None:
        self._config = config

    async def resolve(
        self, extracted: ExtractedContext, versions: VersionMap
    ) -> ResolvedSymbol | None:
        symbol = extracted.symbol
        if not symbol:
            return None
        # Normalise: drop `.prototype.` and any leading array/instance noise.
        parts = [p for p in symbol.split(".") if p and p != "prototype"]
        if not parts:
            return None
        root = parts[0]
        if root not in _GLOBALS:
            return None  # only builtins resolve deterministically

        url = f"{_MDN_BASE}{root}" if len(parts) == 1 else f"{_MDN_BASE}{root}/{parts[-1]}"

        return ResolvedSymbol(
            package="javascript",
            version="MDN",
            symbol=".".join(parts),
            url=url,
            anchor=None,
            role="builtin",
            resolver="mdn",
            exact_version=False,
        )
