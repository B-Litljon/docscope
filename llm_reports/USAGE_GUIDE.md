# docscope — usage guide

## What it is

**docscope** watches what you type and surfaces the *version-correct* documentation for the symbol under your cursor — signature, doc excerpt, and an example — in your editor's sidebar. It is a navigator, not a copilot: it never writes to your buffer. Reach for it whenever you're reaching for a browser tab to check an API; it's designed to replace that reflex.

## Install & prerequisites

- **Daemon (Python 3.12):** [`uv`](https://docs.astral.sh/uv/) is required.
- **VS Code extension:** Node/npm for building.

```bash
# ── daemon ──
cd daemon
uv sync                          # creates .venv, installs deps

# ── VS Code extension (optional) ──
cd clients/vscode
npm install
npm run compile                  # esbuild → dist/extension.js
npm run typecheck                # tsc --noEmit
```

The `docscope` CLI binary is available as `uv run docscope …` from `daemon/`.

## Quick win: CLI

Run the full pipeline without an editor to check that everything works. Line and column are **1-based** (editor convention).

```bash
cd daemon

# Cold cache (~1.2 s — fetches objects.inv over HTTP):
uv run docscope lookup /tmp/docscope-test/test_flask.py 2 6

# Warm cache (~2 ms — everything from SQLite):
uv run docscope lookup /tmp/docscope-test/test_flask.py 2 6
```

Real output (warm):

```
### flask.Flask (class)

`flask latest` *(version approximate)*

class flask.Flask(import_name, static_url_path=None, static_folder='static', ...)

    The flask object implements a WSGI application and acts as the central
    object. It is passed the name of the module or package of the
    application. ...

[Source](https://flask.palletsprojects.com/en/stable/api/#flask.Flask) · resolver: `objects.inv`

> ⚠️ Version not resolved from the workspace manifest; showing latest docs.
```

Flags:
- `--json` — emit structured JSON instead of markdown.
- `--workspace <dir>` — override workspace root (otherwise auto-detected from lockfiles).
- `--zero-based` — treat line/col as 0-based.
- `--verbose` — structured JSON-lines logs to stderr.

## Running the daemon

```bash
cd daemon
uv run docscope serve            # listens on 127.0.0.1:7317
```

Two HTTP endpoints (useful for testing):

```bash
curl http://127.0.0.1:7317/health
# → {"status":"ok","version":"0.1.0"}

curl -X POST http://127.0.0.1:7317/lookup \
  -H "Content-Type: application/json" \
  -d '{"file_path":"/tmp/test.py","language":"python","text":"import flask\nFlask.run","cursor_line":1,"cursor_col":8,"workspace_root":"/tmp","window_start_line":0}'
# → {"type":"card","card":{...},"markdown":"..."}
```

There is also a **WebSocket endpoint** at `/ws` used by editor clients. It applies a 700 ms idle debounce and a symbol-change gate (no duplicate cards) — see [Editor use](#editor-use-vs-code) below.

## Editor use (VS Code)

1. Start the daemon (`uv run docscope serve` from `daemon/`).
2. Open VS Code, press **F5** (Extension Development Host).
3. Open a Python/Rust/JS/TS file — move your cursor over a library symbol.

**Behavior:**
- **Ambient cards** appear in the sidebar (newest on top) after a 700 ms pause — no keypress needed.
- **`Ctrl+Alt+D`** (`Cmd+Alt+D` on macOS) forces an immediate lookup, bypassing the debounce, and reports errors.
- **Pin** a card to keep it; the **↗** button opens the source docs in a browser.
- Unpinned cards are pruned at 25 max.

**Settings** (VS Code `settings.json`):

| Key | Default | What it does |
|---|---|---|
| `docscope.daemonUrl` | `ws://127.0.0.1:7317/ws` | Daemon WebSocket URL |
| `docscope.contextWindowLines` | `40` | Lines above/below cursor sent as context |
| `docscope.clientDebounceMs` | `200` | Client pre-debounce before streaming |
| `docscope.enabledLanguages` | python, rust, js(x), ts(x) | Languages docscope reacts to |

## Language & version coverage

| Language | Resolver | Version source | Version-pinned? |
|---|---|---|---|
| Python | Sphinx `objects.inv` (+ RTD/PyPI discovery) | `uv.lock`, `poetry.lock`, `pyproject.toml`, `requirements.txt` | ✅ For RTD-hosted libs (numpy, pandas, flask, etc.) ⚠️ Polars publishes only `stable` |
| Rust | docs.rs / doc.rust-lang.org URL probing | `Cargo.lock`, `Cargo.toml` | ✅ Always pinned to crate version |
| JS/TS | MDN Global Objects (builtins) | `package-lock.json`, `package.json` | ❌ MDN is the living web platform |

"Version-pinned" means the docs URL embeds the version from your lockfile (e.g. `flask.palletsprojects.com/en/3.1.x/`). When it can't pin (polars only publishes `stable`, MDN has no versions), the card shows a warning.

## Configuration

Copy `config.example.toml` → `~/.docscope/config.toml`. Every key is optional.

```toml
[daemon]
host = "127.0.0.1"
port = 7317
debounce_ms = 700

[cache]
dir = "~/.docscope"            # also holds cache.db and logs/
inventory_ttl_days = 7
doc_page_ttl_days = 30

[network]
fetch_timeout_s = 4.0

# Optional LLM tier (off by default — never blocks the fast path)
[llm]
enabled = false
base_url = "http://localhost:4000"
api_key_env = "DOCSCOPE_LLM_API_KEY"

# Optional web-search tier (off by default)
[search]
provider = "none"              # "searxng" | "brave"
endpoint = ""
api_key_env = "DOCSCOPE_SEARCH_API_KEY"
```

**Where things live:**
- Cache: `~/.docscope/cache.db` (SQLite)
- Logs: `~/.docscope/logs/` (JSON lines)
- Clear cache: `rm ~/.docscope/cache.db`

## Troubleshooting

**"No card" for a symbol I expect to resolve.** Likely causes:
- The symbol is a local variable (not imported) — docscope can't infer the type without a type checker.
- The library has no Sphinx `objects.inv` (e.g. pure mkdocs projects, some small libs).
- The workspace manifest didn't match the import name (e.g. `bs4` → `beautifulsoup4` — this mapping exists for common cases but isn't exhaustive).

**Cold-cache latency.** The first lookup for a library fetches its `objects.inv` over HTTP (~200–1500 ms depending on size). Subsequent lookups are served from SQLite in 2–15 ms.

**Logs.** JSON-lines structured logs at `~/.docscope/logs/`. Each stage of the pipeline logs its result. Run with `--verbose` to see them on stderr.

**Supported languages.** Python, Rust, JavaScript/JSX, TypeScript/TSX. Language is detected from the file extension; unrecognised extensions default to Python.
