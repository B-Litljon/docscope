"""Pydantic data models shared across the docscope pipeline.

These types form the contract between pipeline stages: a raw
:class:`BufferContext` comes in from a client (or the CLI harness), gets
enriched into an :class:`ExtractedContext`, resolved against a
:class:`PackageVersion` map into a :class:`ResolvedSymbol`, and finally
rendered into a :class:`DocCard` that is shipped to the human.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class BufferContext(BaseModel):
    """Raw editor context. This is the wire format clients send.

    ``text`` is the ±40 line window around the cursor (the CLI harness may
    pass the whole file). ``window_start_line`` records which absolute file
    line ``text``'s first line corresponds to, so the daemon can translate
    the absolute cursor position into a window-relative one.
    """

    file_path: str
    language: str
    text: str
    cursor_line: int = Field(ge=0, description="0-based absolute line of the cursor")
    cursor_col: int = Field(ge=0, description="0-based column of the cursor")
    workspace_root: str | None = None
    window_start_line: int = Field(default=0, ge=0)


class ImportBinding(BaseModel):
    """A name introduced into the file's namespace by an import statement."""

    local_name: str
    qualified_name: str
    package: str


class ExtractedContext(BaseModel):
    """Structured facts extracted from the buffer by the ContextExtractor."""

    language: str
    symbol: str | None = None
    raw_token: str | None = None
    enclosing_call: str | None = None
    arg_index: int | None = None
    imports: list[ImportBinding] = Field(default_factory=list)
    incomplete: bool = False


class PackageVersion(BaseModel):
    """A single {package -> version} resolution with provenance."""

    package: str
    version: str
    ecosystem: str
    source: str
    exact: bool


class ResolvedSymbol(BaseModel):
    """A symbol resolved to a concrete, version-pinned documentation URL."""

    package: str
    version: str
    symbol: str
    url: str
    anchor: str | None = None
    role: str | None = None
    resolver: str
    exact_version: bool = True


class DocSection(BaseModel):
    """Extracted documentation content for a single symbol, tier-agnostic."""

    signature: str | None = None
    body_md: str = ""
    example_md: str | None = None


class DocCard(BaseModel):
    """The human-facing output: a rendered documentation card."""

    title: str
    package: str
    version: str
    symbol: str
    signature: str | None = None
    body_md: str = ""
    example_md: str | None = None
    source_url: str
    resolver: str
    exact_version: bool = True
    elapsed_ms: float = 0.0
    cache_tier: str = "fresh"
    warnings: list[str] = Field(default_factory=list)

    def to_markdown(self) -> str:
        """Render the card as a self-contained markdown document."""
        badge = f"`{self.package} {self.version}`"
        if not self.exact_version:
            badge += " *(version approximate)*"
        parts: list[str] = [f"### {self.title}", "", badge, ""]
        if self.signature:
            lang = "python" if self.resolver == "objects.inv" else ""
            parts += [f"```{lang}", self.signature.strip(), "```", ""]
        if self.body_md.strip():
            parts += [self.body_md.strip(), ""]
        if self.example_md and self.example_md.strip():
            parts += ["**Example**", "", self.example_md.strip(), ""]
        parts += [f"[Source]({self.source_url}) · resolver: `{self.resolver}`"]
        if self.warnings:
            parts += ["", *[f"> ⚠️ {w}" for w in self.warnings]]
        return "\n".join(parts).rstrip() + "\n"


class LookupError(BaseModel):
    """Structured negative result: no card could be produced."""

    reason: str
    detail: str | None = None
    elapsed_ms: float = 0.0
