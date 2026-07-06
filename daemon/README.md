# docscope daemon

The Python 3.12 core of [docscope](../README.md): a FastAPI daemon that resolves
the symbol under your cursor to version-correct documentation, plus the
`docscope` CLI.

```bash
uv sync
uv run docscope lookup <file> <line> <col>   # full pipeline, no editor needed
uv run docscope serve                        # daemon on 127.0.0.1:7317
uv run pytest && uv run ruff check . && uv run pyright
```

Pipeline: `ContextExtractor` (tree-sitter) → `VersionResolver` (manifests/locks)
→ `SymbolResolver` (Sphinx `objects.inv`) → `Retriever` (SQLite cache → HTTP
section extraction) → `Assembler` (markdown card). See the
[top-level README](../README.md) for the full picture.
