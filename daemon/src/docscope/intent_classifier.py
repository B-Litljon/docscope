"""Slow-path intent classification via a LiteLLM proxy.

This is the *LLM-last* tier: it runs only when deterministic resolution fails
(e.g. the symbol root is a local variable whose type we can't infer). It calls
the operator's existing LiteLLM proxy over its OpenAI-compatible
``/chat/completions`` endpoint — we deliberately do **not** depend on the heavy
``litellm`` client package. Temperature 0, strict-JSON output.

Crucially, the LLM only *names* the library + symbol; the actual doc URL is
still produced by the version-pinned :class:`SymbolResolver`, so LLM-path cards
remain version-correct.

Every failure mode (disabled, unreachable, timeout, bad JSON) returns ``None``
so the pipeline degrades to fast-path/web-search without ever blocking.
"""

from __future__ import annotations

import json
import logging
import re

import httpx
from pydantic import BaseModel, ValidationError

from .config import Config
from .logging_setup import get_logger, log_event
from .models import BufferContext, ExtractedContext

log = get_logger("intent")

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)

_SYSTEM_PROMPT = (
    "You are a code documentation intent classifier. Given an editing context, "
    "identify the library symbol the developer is referencing. Respond with ONLY "
    "a compact JSON object, no prose, with exactly these keys: "
    '"library" (the importable/distribution package name, or null), '
    '"symbol" (the fully-qualified dotted symbol, e.g. "polars.DataFrame.join_asof", or null), '
    '"task_intent" (a short phrase describing what they are doing), '
    '"confidence" (a number from 0 to 1).'
)


class IntentResult(BaseModel):
    library: str | None = None
    symbol: str | None = None
    task_intent: str | None = None
    confidence: float = 0.0


class IntentClassifier:
    def __init__(self, config: Config, client: httpx.AsyncClient) -> None:
        self._config = config
        self._client = client
        self._llm = config.llm

    @property
    def available(self) -> bool:
        return self._llm.enabled and bool(self._llm.base_url)

    async def classify(
        self, extracted: ExtractedContext, buffer: BufferContext
    ) -> IntentResult | None:
        if not self.available:
            return None
        payload = {
            "model": self._llm.model_tier_fast,
            "temperature": 0,
            "max_tokens": 200,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": self._user_prompt(extracted, buffer)},
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self._llm.api_key:
            headers["Authorization"] = f"Bearer {self._llm.api_key}"

        url = self._llm.base_url.rstrip("/") + "/chat/completions"
        try:
            resp = await self._client.post(
                url, json=payload, headers=headers, timeout=self._llm.timeout_s
            )
        except httpx.HTTPError as exc:
            log_event(log, logging.WARNING, "llm unreachable (degrading)", error=str(exc))
            return None
        if resp.status_code != 200:
            log_event(log, logging.WARNING, "llm non-200 (degrading)", status=resp.status_code)
            return None

        content = self._extract_content(resp)
        if content is None:
            return None
        result = self._parse(content)
        if result is not None:
            log_event(
                log,
                logging.INFO,
                "llm intent",
                library=result.library,
                symbol=result.symbol,
                confidence=result.confidence,
            )
        return result

    def _user_prompt(self, extracted: ExtractedContext, buffer: BufferContext) -> str:
        imports = {b.local_name: b.qualified_name for b in extracted.imports}
        snippet = self._snippet(buffer)
        context = {
            "language": extracted.language,
            "imports": imports,
            "token_under_cursor": extracted.raw_token,
            "partial_symbol": extracted.symbol,
            "enclosing_call": extracted.enclosing_call,
            "incomplete_expression": extracted.incomplete,
            "code": snippet,
        }
        return json.dumps(context, ensure_ascii=False)

    def _snippet(self, buffer: BufferContext, radius: int = 12) -> str:
        lines = buffer.text.splitlines()
        rel = max(0, buffer.cursor_line - buffer.window_start_line)
        start = max(0, rel - radius)
        end = min(len(lines), rel + radius + 1)
        return "\n".join(lines[start:end])

    def _extract_content(self, resp: httpx.Response) -> str | None:
        try:
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            log_event(log, logging.WARNING, "llm bad envelope", error=str(exc))
            return None

    def _parse(self, content: str) -> IntentResult | None:
        match = _JSON_OBJECT.search(content)
        if not match:
            return None
        try:
            return IntentResult.model_validate_json(match.group(0))
        except ValidationError:
            # Try loading then coercing loosely (models sometimes stringify numbers).
            try:
                raw = json.loads(match.group(0))
                return IntentResult(
                    library=raw.get("library"),
                    symbol=raw.get("symbol"),
                    task_intent=raw.get("task_intent"),
                    confidence=float(raw.get("confidence", 0) or 0),
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                return None
