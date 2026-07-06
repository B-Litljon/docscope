# M2M PROMPT — TARGET: opencode (any coding agent) — TASK: explain how to use `docscope`

## ROLE
You are a technical writer with shell access. A tool called **docscope** already exists and is fully built in this repository. Your job is NOT to modify it. Your job is to produce a clear, correct, copy-pasteable **usage guide for a human developer**, grounded in the actual code — not from memory or assumption.

## REPO
Root: `/mnt/storage/mystuf/development/docscope`
Layout: `daemon/` (Python 3.12 core + the `docscope` CLI, managed with `uv`), `clients/vscode/` (thin VS Code extension, TypeScript/esbuild), `config.example.toml`, `README.md`, `llm_reports/` (see the latest `docscope_*_session.md` for design notes and known limitations).

## WHAT DOCSCOPE IS (one paragraph — verify, then restate in your own words)
An ambient, version-pinned documentation navigator. Given a file + cursor position, it resolves the symbol under the cursor to the **actual, version-correct** documentation for the *installed* version of that library (read from the workspace's dependency manifest/lockfile) and returns a rendered card: signature + doc excerpt + one example + source URL + version badge. It is a navigator, not a copilot — it never writes code. Resolution is deterministic-first (tree-sitter + Sphinx `objects.inv` for Python, docs.rs URL scheme for Rust, MDN for JS/TS builtins); an LLM and a web-search backend are optional last-resort fallbacks that are OFF by default.

## GROUND YOURSELF FIRST (do this before writing anything)
Read, don't guess:
1. `daemon/src/docscope/cli.py` — the exact subcommands, arguments, and whether line/col are 1-based.
2. `daemon/src/docscope/server.py` and `ws.py` — the HTTP (`/health`, `POST /lookup`) and WebSocket (`/ws`) surface and message shapes.
3. `daemon/src/docscope/config.py` + `config.example.toml` — every config key and its default; how API keys are referenced via env vars.
4. `clients/vscode/README.md` + `clients/vscode/package.json` — extension build, settings, and the keybinding.
5. `README.md` and the newest `llm_reports/docscope_*_session.md` — supported languages, version sources, and known limitations.

## VERIFY THE HAPPY PATH (run it; paste real output into the guide)
From `daemon/`:
```bash
uv sync
uv run docscope version
uv run docscope lookup <path-to-a-py-file-in-a-uv/pip/poetry-project> <line> <col>
```
Create a tiny throwaway workspace if needed: a `pyproject.toml` pinning e.g. `polars==1.2.1` and a `.py` file that references `pl.DataFrame(...).join_asof(...)`, then look up the cursor on `join_asof`. Confirm the card shows the pinned version. Note first-call latency (cold, hits the network) vs a second call (warm, served from the `~/.docscope/cache.db` SQLite cache). Also smoke-test the daemon: `uv run docscope serve` in one shell, then `curl http://127.0.0.1:7317/health` and a `POST /lookup` in another. If a command behaves differently from what the code implied, trust the observed behavior and say so.

## DELIVERABLE — the usage guide
Produce a single Markdown document aimed at a developer who has never seen docscope. Cover, in this order:
1. **What it is / when to reach for it** — 3-4 sentences, plus the one hard rule: it never edits your buffer.
2. **Install / prerequisites** — `uv` for the daemon; Node/npm for the extension. Exact `uv sync` step and where the binary comes from (`uv run docscope …`).
3. **Quick win (CLI)** — the `docscope lookup <file> <line> <col>` invocation with a real example and real trimmed output. State clearly that line/col are 1-based (unless `--zero-based`), and mention `--json`, `--workspace`, and `--verbose`.
4. **Running the daemon** — `docscope serve` (host/port, default `127.0.0.1:7317`), the `GET /health` and `POST /lookup` contract with a real `curl` example, and one sentence on the `/ws` streaming endpoint (debounce + symbol-change gating happen daemon-side).
5. **Editor use (VS Code)** — build steps (`npm install && npm run compile`, then F5), the sidebar behavior (ambient cards, pin, open-source), the force-lookup keybinding, and the relevant settings keys (daemon URL, context window, debounce, enabled languages).
6. **Language & version coverage** — a small table: Python (`objects.inv`), Rust (docs.rs), JS/TS (MDN builtins), and which manifest/lockfiles each reads. Explain what "version-pinned" means and when a card is honestly flagged as *not* pinned (e.g. libraries that publish only "stable", or MDN).
7. **Configuration** — copy `config.example.toml` → `~/.docscope/config.toml`; everything is optional. How to enable the optional LLM tier (`[llm]`, LiteLLM proxy, key via env var) and the web-search tier (`[search]`, SearXNG/Brave), and that both are off by default and never block the fast path. Where logs (`~/.docscope/logs/`) and the cache (`~/.docscope/cache.db`) live.
8. **Troubleshooting** — "no card" cases (symbol is a local variable / un-imported name / library without a Sphinx inventory), cold-cache latency, how to clear the cache, and where to read the JSON-lines logs. Pull the real limitations from the session report rather than inventing them.

## CONSTRAINTS
- Do not modify docscope source, config, or tests. Read-only except for a throwaway sample workspace under a temp dir.
- Every command in the guide must be one you actually ran or verified exists in the code. No invented flags.
- Prefer showing real output over describing it. Keep it concise and skimmable; a developer should be productive in under five minutes.
- If something in the code contradicts this brief, follow the code and note the discrepancy.
