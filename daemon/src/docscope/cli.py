"""``docscope`` command-line entry point.

Subcommands:

* ``docscope lookup <file> <line> <col>`` — run the full daemon pipeline against
  a real file position and print the resulting doc card. This is the M1
  integration-test entry point; it exercises every stage without an editor.
* ``docscope serve`` — start the daemon (FastAPI + WebSocket) for editor clients.
* ``docscope version`` — print the version.

Line and column are 1-based (editor convention) unless ``--zero-based``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from . import __version__
from .config import load_config
from .logging_setup import configure_logging
from .models import BufferContext, DocCard, LookupError
from .pipeline import Pipeline

_LANG_BY_SUFFIX = {
    ".py": "python",
    ".pyi": "python",
    ".rs": "rust",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
}

_WORKSPACE_MARKERS = (
    "uv.lock",
    "poetry.lock",
    "requirements.txt",
    "pyproject.toml",
    "Cargo.lock",
    "Cargo.toml",
    "package.json",
    ".git",
)


def _detect_language(path: Path) -> str:
    return _LANG_BY_SUFFIX.get(path.suffix.lower(), "python")


def _find_workspace_root(path: Path) -> Path:
    for parent in [path.parent, *path.parent.parents]:
        if any((parent / marker).exists() for marker in _WORKSPACE_MARKERS):
            return parent
    return path.parent


async def _run_lookup(args: argparse.Namespace) -> int:
    file_path = Path(args.file).expanduser().resolve()
    if not file_path.is_file():
        print(f"error: no such file: {file_path}", file=sys.stderr)
        return 2
    text = file_path.read_text("utf-8", errors="replace")

    line = args.line if args.zero_based else args.line - 1
    col = args.col if args.zero_based else args.col - 1
    line = max(0, line)
    col = max(0, col)

    workspace = (
        Path(args.workspace).expanduser().resolve()
        if args.workspace
        else _find_workspace_root(file_path)
    )

    ctx = BufferContext(
        file_path=str(file_path),
        language=_detect_language(file_path),
        text=text,
        cursor_line=line,
        cursor_col=col,
        workspace_root=str(workspace),
        window_start_line=0,
    )

    config = load_config(args.config)
    async with Pipeline(config) as pipeline:
        result = await pipeline.lookup(ctx)

    if isinstance(result, LookupError):
        if args.json:
            print(json.dumps(result.model_dump(), indent=2))
        else:
            print(f"no card: {result.reason} — {result.detail}", file=sys.stderr)
            print(f"({result.elapsed_ms:.0f} ms)", file=sys.stderr)
        return 1

    assert isinstance(result, DocCard)
    if args.json:
        print(json.dumps(result.model_dump(), indent=2))
    else:
        print(result.to_markdown())
        print(f"\n— {result.elapsed_ms:.0f} ms · cache tier {result.cache_tier}", file=sys.stderr)
    return 0


async def _run_serve(args: argparse.Namespace) -> int:
    # Imported lazily so the CLI (and M1 pipeline) does not depend on the
    # server stack unless the user actually starts the daemon.
    from .server import run_server

    config = load_config(args.config)
    await run_server(config, config_path=args.config)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docscope", description=__doc__)
    parser.add_argument("--config", help="path to config.toml", default=None)
    parser.add_argument("--verbose", action="store_true", help="log to stderr")
    sub = parser.add_subparsers(dest="command", required=True)

    lookup = sub.add_parser("lookup", help="look up docs for a file position")
    lookup.add_argument("file")
    lookup.add_argument("line", type=int)
    lookup.add_argument("col", type=int)
    lookup.add_argument("--workspace", help="override workspace root", default=None)
    lookup.add_argument("--zero-based", action="store_true", help="line/col are 0-based")
    lookup.add_argument("--json", action="store_true", help="emit JSON instead of markdown")

    serve = sub.add_parser("serve", help="run the docscope daemon")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)

    sub.add_parser("version", help="print version")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    config = load_config(args.config)
    level = logging.DEBUG if args.verbose else logging.WARNING
    configure_logging(config.log_dir, level=level, to_stderr=args.verbose)

    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "lookup":
        return asyncio.run(_run_lookup(args))
    if args.command == "serve":
        return asyncio.run(_run_serve(args))
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
