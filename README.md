# docscope

An ambient, human-facing documentation navigator for your editor. docscope
watches what you're writing and surfaces the **actual, version-correct**
documentation for the symbol under your cursor — signature, a doc excerpt, and
one canonical example — in a sidebar. It is a navigator, not a copilot: it never
writes code into your buffer.

## Why it's different

- **Version-pinned.** It resolves your workspace's dependency manifest and
  fetches docs for the *installed* version, not "latest".
- **Deterministic first, LLM-last.** Symbols resolve through tree-sitter + Sphinx
  `objects.inv` inventories with no model in the loop. A local LLM is consulted
  only when intent is genuinely ambiguous (M3).
- **Local-first.** Docs are cached on disk (SQLite); the network is a fallback.

## Repo layout

```
daemon/           Python 3.12 FastAPI daemon — all the logic (and the CLI)
clients/vscode/   Thin VS Code extension: context capture + sidebar (M2)
config.example.toml
llm_reports/      Session reports
```

## Quickstart (daemon + CLI)

```bash
cd daemon
uv sync

# Look up docs for a file position (1-based line/col). This runs the full
# pipeline without an editor — the integration-test entry point.
uv run docscope lookup path/to/file.py 42 17

# Run the daemon for editor clients
uv run docscope serve            # listens on 127.0.0.1:7317
```

Example against a workspace pinning `polars==1.2.1`:

```bash
uv run docscope lookup analysis.py 5 44
# ### polars.DataFrame.join_asof (method)
# `polars 1.2.1`
# DataFrame.join_asof(other: DataFrame, *, left_on: ..., strategy: ... = 'backward', ...) → DataFrame
# Perform an asof join. ...
# [Source](https://docs.pola.rs/.../polars.DataFrame.join_asof.html) · resolver: objects.inv
```

## HTTP surface

- `GET /health` → `{"status": "ok", "version": "..."}`
- `POST /lookup` with a `BufferContext` JSON body → `{"type": "card"|"error", ...}`
  (bypasses debounce; used for keybound manual lookups and by the CLI/tests).

The WebSocket streaming endpoint with daemon-side debounce arrives in M2.

## Configuration

Copy `config.example.toml` to `~/.docscope/config.toml`. Everything is optional.
Logs are written as JSON lines to `~/.docscope/logs/`.

## Development

```bash
cd daemon
uv run pytest        # hermetic: vendored objects.inv + HTML, tmp-path SQLite
uv run ruff check .
uv run pyright
```

## Milestones

- **M1 ✅** daemon core, tree-sitter Python context, version resolver
  (pyproject/uv.lock/requirements/poetry), `objects.inv` resolver, SQLite cache,
  tiered retriever, CLI + HTTP daemon skeleton.
- **M2** VS Code extension + WebSocket transport + sidebar + debounce.
- **M3** LiteLLM IntentClassifier + web-search fallback + graceful degradation.
- **M4** Rust (docs.rs) + JS/TS (MDN) resolvers, Cargo/npm manifests.

MIT licensed.
