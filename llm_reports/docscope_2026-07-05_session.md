# docscope — implementation session report

**Date:** 2026-07-05
**Engineer:** Claude Opus 4.8 (Claude Code)
**Scope:** MVP build, milestones M1–M4
**Repo:** `/mnt/storage/mystuf/development/docscope` (git initialised, 4 commits, one per milestone)

---

## Status: all four milestones complete ✅

| Milestone | State | Acceptance |
| --- | --- | --- |
| M1 daemon core + fast path + CLI | ✅ | `polars.DataFrame.join_asof` card in **3 ms warm** (643 ms cold), version-pinned |
| M2 VS Code extension + WS + sidebar | ✅ | Ambient card pushed after 700 ms debounce; change-gate suppresses duplicates (verified live over a real WS client) |
| M3 IntentClassifier + T3 search + degradation | ✅ | Incomplete `pl.DataFrame().join_as` → correct card **deterministically**; dead LiteLLM proxy does not break fast path |
| M4 Rust (docs.rs) + JS/TS (MDN) + Cargo/npm | ✅ | `serde_json::Value` → docs.rs enum pinned to 1.0.117; `JSON.parse` → correct MDN URL |

**Quality gates (daemon):** `ruff` clean, `ruff format` clean, `pyright` 0 errors, **72 tests passing** (hermetic; real tmp-path SQLite, HTTP served via `httpx.MockTransport`, a vendored 221-byte `objects.inv` + sample Sphinx HTML). **VS Code extension:** `tsc --noEmit` clean, esbuild bundle succeeds.

---

## What was built, per milestone

### M1 — daemon core + deterministic fast path + CLI
- `ContextExtractor` (tree-sitter-python): import table, dotted symbol under/before cursor, enclosing call + arg index. Handles half-typed expressions and collapses constructor chains (`pl.DataFrame().join_asof` → `polars.DataFrame.join_asof`) so method lookups resolve **without type inference**.
- `VersionResolver`: pyproject (PEP 621 + poetry), requirements.txt, uv.lock, poetry.lock. Lockfile-exact preferred over manifest ranges; SQLite-cached; invalidated on manifest-file mtime change; import→distribution aliasing (`bs4`→`beautifulsoup4`, …).
- `DocRegistry` + `SymbolResolver`: version-templated `objects.inv` URLs for common libs, generic ReadTheDocs guesses, exact match then **deterministic prefix completion**.
- Tiered `Retriever`: T1 SQLite `doc_pages`, T2 anchor-precise Sphinx `<dt>/<dd>` extraction → markdown (≤60 lines), with a chrome-stripping generic fallback.
- `Assembler` → human-facing markdown `DocCard` with an honest version badge/warnings.
- `DocCache` (aiosqlite, WAL). CLI `docscope lookup/serve/version`; FastAPI daemon skeleton (`/health`, `POST /lookup`); JSON-lines logging to `~/.docscope/logs/`.

### M2 — WebSocket transport + debounce + editor client
- `ws.py`: `/ws` endpoint, per-connection **700 ms idle debounce** + **symbol-change gate** (no duplicate cards). `context` events are ambient (silent on miss); `lookup` events force an immediate, gate-bypassing lookup and surface errors.
- VS Code extension (`clients/vscode`, TypeScript, esbuild): reconnecting `DaemonClient`, cursor streaming with 200 ms client pre-debounce and a ±40-line context window, `CardsViewProvider` sidebar (markdown-it rendered cards, pin, source click-through, connection status, strict CSP), `ctrl+alt+d` force-lookup, status bar, settings. Thin by design — context capture + rendering only, no buffer writes.

### M3 — LLM-last classifier + web fallback + graceful degradation
- `intent_classifier.py`: calls a LiteLLM proxy over its OpenAI-compatible `/chat/completions` endpoint, temperature 0, strict-JSON parsed leniently. **The model only names the symbol**; it is re-resolved through the version-pinned fast path (resolver `llm+…`), so LLM cards stay version-correct. Every failure mode returns `None`.
- `search.py`: `SearchProvider` protocol + **SearXNG and Brave** implementations + null default; errors degrade to `[]`. Drives T3 for libraries with no inventory (resolver `web-search`, flagged in the card).
- `pipeline.py`: three-tier resolution (fast path → LLM → web), LLM strictly last.

### M4 — Rust + JS/TS + Cargo/npm
- `version_resolver.py`: Cargo.toml/Cargo.lock and package.json/package-lock.json; ecosystem-aware dedup and lookup.
- `context_extractor.py`: generalized backward scanner (`.` and `::` separators) + regex `use`/`import`/`require` binding resolution (aliases, braced groups, default/named/namespace imports).
- `rust_resolver.py`: docs.rs / doc.rust-lang.org URL resolution by probing item-kind filenames (struct/enum/trait/fn/…) + method anchors + module-index fallback; version-pinned to the Cargo version; cached.
- `mdn_resolver.py`: MDN Global Objects URLs for JS/TS builtins (prototype-aware).
- Per-language resolver dispatch behind a common `LanguageResolver` protocol.

---

## Deviations from the spec (with rationale)

1. **httpx is NOT a valid reference inventory.** httpx docs are mkdocs and ship **no `objects.inv`** (confirmed: 404). The spec named polars + httpx as the two reference libraries. I used **polars + numpy/requests** instead (both ship real inventories). No code impact; httpx simply routes to the LLM/web tiers.
2. **LiteLLM called over HTTP, not via the `litellm` package.** The proxy exposes an OpenAI-compatible API, so a direct `httpx` POST avoids a very heavy dependency while meeting the spec ("call the LiteLLM proxy at the operator's endpoint, tier local-fast").
3. **Rust/JS context extraction is regex/text-scan, not tree-sitter.** Python uses tree-sitter (precise imports + enclosing call). For Rust, `use`-expansion + path scanning is handled cleanly by regex; for JS/TS MDN builtins, no import resolution is even needed. This avoids three extra grammar deps for marginal gain. I **removed** the now-unused `tree-sitter-rust/js/ts` optional extras. (Easy to upgrade later behind the same interface.)
4. **Incomplete expressions resolve deterministically, before any LLM.** M3's acceptance (`pl.DataFrame().join_as` → correct card) is met by fast-path **prefix completion**, not the model — which is more aligned with the "LLM-last" principle than the spec's implied LLM-for-incomplete. The LLM is reserved for genuinely unresolvable cases (e.g. a method on a local variable whose type we can't infer).
5. **`manifests` schema extended** with an additive `manifest_sources(workspace_root, source_path, mtime)` table. The spec's bare `manifests` schema cannot express "invalidate on mtime change"; the four spec tables are otherwise present as specified.
6. **Async SQLite via `aiosqlite`** (the spec left this to my discretion). Single WAL connection, write-through commits.
7. **Two search backends** (SearXNG + Brave) delivered where the spec asked for one concrete impl.
8. **Version-pinning honesty.** Genuinely version-pinned: ReadTheDocs-hosted libs, numpy/pandas, and **docs.rs (Rust)**. Not pinnable: polars (publishes only a `stable` Python API — the card shows the installed version but warns it's not pinned) and MDN (living web platform). The `exact_version` flag reflects whether the *URL* actually embeds the installed version, and the card warns when it doesn't.
9. **Daemon skeleton landed in M1** (`/health` + `POST /lookup`) since M1 is "daemon skeleton"; the WebSocket + debounce is M2.

---

## Known limitations / open items (no known crashing bugs)

- **Non-ASCII cursor columns:** the client column is treated as a character index and converted to a byte offset per line; tree-sitter points are byte-based, so columns on lines with multibyte characters can be slightly off. ASCII (the common case) is exact.
- **MDN body extraction** can still be verbose on large pages, and MDN may rate-limit/403 automated fetches — the card is still returned with the correct URL and badge even when the body can't be extracted.
- **Rust item/method heuristic:** assumes the item is the Uppercase segment before a lowercase tail; associated constants and trait methods only partially covered (falls back to probing/module index). Turbofish/generics are ignored by the scanner.
- **import→distribution mapping** relies on a small alias table; uncommon mismatches resolve to `latest` (unpinned) rather than the installed version.
- **T3 web search** picks the top result blindly (no re-ranking).
- **Manifest coverage:** npm supports `package.json` + `package-lock.json` v1/v2/v3 only (no yarn/pnpm lockfiles).

## Architectural concerns to flag (not silently worked around)

- **docs.rs cold-cache latency:** resolution probes up to ~6 `GET`s to find the right item-kind filename (then caches). Within the fast-path budget in practice, but a rustdoc search-index parse would be faster and fully offline — worth considering if Rust becomes a primary target.
- **Webview HTML injection:** the sidebar renders daemon-produced HTML via `innerHTML`. The CSP forbids script execution and content originates from trusted doc fetches, but this is a mild XSS surface; sanitising in the host (e.g. DOMPurify) would harden it.
- **Change-gate vs forced lookups:** a forced lookup updates the "last pushed symbol", so an immediately following *ambient* move onto the same symbol is suppressed. Intended, but worth revisiting if users expect a re-push.

---

## Commands

All daemon commands run from `daemon/`:

```bash
cd daemon
uv sync                                    # create env (downloads Python 3.12)

# Tests + quality gates
uv run pytest                              # 72 tests, hermetic
uv run ruff check . && uv run ruff format --check . && uv run pyright

# Run the daemon (127.0.0.1:7317)
uv run docscope serve

# Exercise the full pipeline without an editor (1-based line/col)
uv run docscope lookup path/to/file.py 42 17
uv run docscope lookup path/to/main.rs 1 20      # Rust (docs.rs)
uv run docscope lookup path/to/app.js 3 23       # JS/TS (MDN)

# Regenerate the vendored objects.inv fixture
uv run python tests/fixtures/make_inventory.py
```

VS Code extension:

```bash
cd clients/vscode
npm install
npm run compile        # esbuild bundle -> dist/extension.js
npm run typecheck      # tsc --noEmit
# then press F5 in VS Code to launch the Extension Development Host
```

Configuration: copy `config.example.toml` → `~/.docscope/config.toml` (all keys optional). Enable the LLM tier by setting `[llm].enabled = true` and `base_url`; enable T3 by setting `[search].provider` + `endpoint`.

---

## Files of note

```
daemon/src/docscope/
  context_extractor.py   tree-sitter Python + regex Rust/JS extraction
  version_resolver.py    pyproject/uv/poetry/requirements + Cargo + npm
  registry.py            per-package objects.inv URL templates
  symbol_resolver.py     Python objects.inv fast path + prefix completion
  rust_resolver.py       docs.rs / doc.rust-lang.org probing
  mdn_resolver.py        MDN Global Objects (JS/TS builtins)
  intent_classifier.py   LiteLLM proxy (OpenAI-compatible), LLM-last
  search.py              SearchProvider + SearXNG/Brave
  retriever.py           tiered T1/T2 + chrome-stripping extraction
  cache.py               aiosqlite: inventories/doc_pages/manifests(+sources)
  pipeline.py            orchestration + per-language dispatch + fallbacks
  server.py / ws.py      FastAPI HTTP + WebSocket/debounce
  cli.py                 docscope lookup/serve/version
clients/vscode/          thin TypeScript extension (esbuild)
```
