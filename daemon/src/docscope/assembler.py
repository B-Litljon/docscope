"""Assemble a human-facing :class:`DocCard` from resolved parts."""

from __future__ import annotations

from .models import DocCard, DocSection, ResolvedSymbol


class Assembler:
    def assemble(
        self,
        resolved: ResolvedSymbol,
        section: DocSection | None,
        *,
        elapsed_ms: float,
        cache_tier: str,
        extra_warnings: list[str] | None = None,
    ) -> DocCard:
        section = section or DocSection()
        warnings = list(extra_warnings or [])

        if resolved.version == "latest":
            warnings.append(
                "Version not resolved from the workspace manifest; showing latest docs."
            )
        elif not resolved.exact_version:
            warnings.append(
                f"Docs may not be pinned to installed version {resolved.version} "
                "(source publishes only 'stable'/'latest')."
            )

        if not section.body_md.strip() and section.signature is None:
            warnings.append("Could not extract the doc body; follow the source link.")

        title = _short_title(resolved.symbol, resolved.role)
        return DocCard(
            title=title,
            package=resolved.package,
            version=resolved.version,
            symbol=resolved.symbol,
            signature=section.signature,
            body_md=section.body_md,
            example_md=section.example_md,
            source_url=resolved.url,
            resolver=resolved.resolver,
            exact_version=resolved.exact_version,
            elapsed_ms=elapsed_ms,
            cache_tier=cache_tier,
            warnings=warnings,
        )


def _short_title(symbol: str, role: str | None) -> str:
    tag = f" ({role})" if role else ""
    return f"{symbol}{tag}"
