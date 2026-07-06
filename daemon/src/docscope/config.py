"""Configuration loading for docscope.

Config lives at ``~/.docscope/config.toml``. Any missing key falls back to a
sane default, so the daemon runs with zero configuration out of the box. API
keys are never stored in config; instead config names an environment variable
(``*_api_key_env``) that is resolved at call time.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_CONFIG_DIR = Path("~/.docscope").expanduser()
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.toml"


class DaemonConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 7317
    debounce_ms: int = 700
    client_debounce_ms: int = 200


class CacheConfig(BaseModel):
    dir: str = "~/.docscope"
    inventory_ttl_days: int = 7
    doc_page_ttl_days: int = 30

    @property
    def resolved_dir(self) -> Path:
        return Path(self.dir).expanduser()

    @property
    def db_path(self) -> Path:
        return self.resolved_dir / "cache.db"


class LLMConfig(BaseModel):
    enabled: bool = False
    base_url: str = ""
    api_key_env: str = "DOCSCOPE_LLM_API_KEY"
    model_tier_fast: str = "local-fast"
    timeout_s: float = 6.0

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env) or None


class SearchConfig(BaseModel):
    provider: str = "none"  # "none" | "searxng" | "brave"
    endpoint: str = ""
    api_key_env: str = "DOCSCOPE_SEARCH_API_KEY"
    timeout_s: float = 5.0

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env) or None


class NetworkConfig(BaseModel):
    fetch_timeout_s: float = 4.0
    user_agent: str = "docscope/0.1 (+https://github.com/docscope)"


class RegistryOverride(BaseModel):
    """User-supplied documentation source for a package.

    ``inv_url`` may contain ``{version}`` / ``{major_minor}`` placeholders for
    version-pinned inventories (e.g. ReadTheDocs ``/en/{version}/``).
    """

    inv_url: str | None = None
    base_url: str | None = None
    versioned: bool = False


class Config(BaseModel):
    daemon: DaemonConfig = Field(default_factory=DaemonConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    registry: dict[str, RegistryOverride] = Field(default_factory=dict)

    @property
    def log_dir(self) -> Path:
        return self.cache.resolved_dir / "logs"


def load_config(path: str | Path | None = None) -> Config:
    """Load config from ``path`` (or the default), applying defaults for
    anything absent. A missing file yields an all-defaults config."""
    cfg_path = Path(path).expanduser() if path else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        return Config()
    with cfg_path.open("rb") as fh:
        raw = tomllib.load(fh)
    return Config.model_validate(raw)
