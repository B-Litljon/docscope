"""Tiered documentation retrieval.

* **T1** — local SQLite ``doc_pages`` cache. Extracted sections are cached as
  JSON keyed by the resolved URL.
* **T2** — HTTP fetch of the resolved doc URL, then anchor-precise extraction of
  the relevant Sphinx object section (signature ``<dt>`` + description ``<dd>``),
  converted to markdown and truncated to ≤60 lines. Falls back to a
  readability-style main-content extraction when the anchor isn't found.
* **T3** — pluggable web-search fallback for unindexed libraries (wired in M3).

Every tier has a timeout and degrades to the next on failure; retrieval never
raises out of :meth:`retrieve`.
"""

from __future__ import annotations

import logging

import httpx
from bs4 import BeautifulSoup, Tag
from markdownify import markdownify as html_to_md

from .cache import DocCache
from .config import Config
from .logging_setup import get_logger, log_event
from .models import DocSection, ResolvedSymbol

log = get_logger("retriever")

_MAX_BODY_LINES = 60


class RetrievalResult:
    __slots__ = ("section", "tier")

    def __init__(self, section: DocSection, tier: str) -> None:
        self.section = section
        self.tier = tier


class Retriever:
    def __init__(self, cache: DocCache, config: Config, client: httpx.AsyncClient) -> None:
        self._cache = cache
        self._config = config
        self._client = client
        self._page_ttl = config.cache.doc_page_ttl_days

    async def retrieve(self, resolved: ResolvedSymbol) -> RetrievalResult | None:
        # T1: cached extracted section.
        cached = await self._cache.get_page(resolved.url, self._page_ttl)
        if cached is not None:
            try:
                return RetrievalResult(DocSection.model_validate_json(cached), "T1")
            except ValueError:
                pass  # corrupt/legacy cache entry -> re-fetch

        # T2: fetch + extract.
        section = await self._fetch_and_extract(resolved)
        if section is not None:
            await self._cache.put_page(resolved.url, resolved.version, section.model_dump_json())
            return RetrievalResult(section, "T2")
        return None

    async def _fetch_and_extract(self, resolved: ResolvedSymbol) -> DocSection | None:
        # Fetch the page itself (without the #fragment); the anchor is used only
        # to locate the section within the returned HTML. The cache key remains
        # the full per-symbol URL so each anchor caches its own extraction.
        page_url = resolved.url.split("#", 1)[0]
        try:
            resp = await self._client.get(page_url, timeout=self._config.network.fetch_timeout_s)
        except httpx.HTTPError as exc:
            log_event(log, logging.WARNING, "doc fetch error", url=resolved.url, error=str(exc))
            return None
        if resp.status_code != 200 or not resp.text:
            log_event(
                log, logging.INFO, "doc fetch non-200", url=resolved.url, status=resp.status_code
            )
            return None
        return self._extract(resp.text, resolved.anchor, resolved.symbol)

    def _extract(self, html: str, anchor: str | None, symbol: str) -> DocSection:
        soup = BeautifulSoup(html, "html.parser")
        target = soup.find(id=anchor) if anchor else None

        if isinstance(target, Tag) and target.name == "dt":
            return self._extract_sphinx_object(target)
        if isinstance(target, Tag):
            return self._extract_generic(target, symbol)
        # Anchor not found: readability-style fallback on the main content.
        main = soup.find("main") or soup.find("article") or soup.body or soup
        return self._extract_generic(main if isinstance(main, Tag) else soup, symbol)

    def _extract_sphinx_object(self, dt: Tag) -> DocSection:
        """Extract from a Sphinx ``<dl class="py ..."><dt>sig</dt><dd>body</dd>``."""
        signature = self._clean_signature(dt)
        dd = dt.find_next_sibling("dd")
        body_md = ""
        example_md: str | None = None
        if isinstance(dd, Tag):
            example_md = self._first_code_block(dd)
            body_md = _truncate(_to_md(dd))
        return DocSection(signature=signature, body_md=body_md, example_md=example_md)

    def _extract_generic(self, node: Tag, symbol: str) -> DocSection:
        example_md = self._first_code_block(node)
        body_md = _truncate(_to_md(node))
        return DocSection(signature=None, body_md=body_md, example_md=example_md)

    def _clean_signature(self, dt: Tag) -> str:
        clone = BeautifulSoup(str(dt), "html.parser")
        for junk in clone.select(".headerlink, .viewcode-link, .viewcode-back"):
            junk.decompose()
        # No separator: Sphinx splits signatures across many inline spans, so a
        # separator would inject a space at every boundary. The real spacing is
        # in the text nodes; we only collapse the newlines/indentation Sphinx
        # uses to pretty-print multi-line signatures.
        text = clone.get_text().replace("¶", "").replace("[source]", "")
        return " ".join(text.split())

    def _first_code_block(self, node: Tag) -> str | None:
        pre = node.find("pre")
        if not isinstance(pre, Tag):
            return None
        # <pre> whitespace is literal; concatenate spans with no separator so
        # pygments highlighting doesn't explode the code across lines.
        code = pre.get_text().strip("\n")
        if not code.strip():
            return None
        lang = "python" if (">>>" in code or "import " in code) else ""
        return f"```{lang}\n{code}\n```"


def _to_md(node: Tag) -> str:
    md = html_to_md(str(node), heading_style="ATX", bullets="-")
    # Squeeze runs of blank lines that markdownify leaves behind.
    lines = [ln.rstrip() for ln in md.splitlines()]
    out: list[str] = []
    blank = False
    for ln in lines:
        if not ln:
            if blank:
                continue
            blank = True
        else:
            blank = False
        out.append(ln)
    return "\n".join(out).strip()


def _truncate(md: str, max_lines: int = _MAX_BODY_LINES) -> str:
    lines = md.splitlines()
    if len(lines) <= max_lines:
        return md
    return "\n".join(lines[:max_lines]).rstrip() + "\n\n…"
