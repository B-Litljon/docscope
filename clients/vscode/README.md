# docscope — VS Code extension

The thin editor client for docscope. It streams your cursor
context to the daemon over a WebSocket and renders the doc cards it pushes back
in a sidebar. It never writes to your buffer and holds no documentation logic —
context capture + rendering only.

## Features

- **Ambient sidebar.** As you move the cursor over a library symbol, the
  version-correct doc card appears (newest on top).
- **Force lookup.** `Ctrl+Alt+D` (`Cmd+Alt+D` on macOS) looks up the symbol under
  the cursor immediately, bypassing the debounce.
- **Pin** a card to keep it; **↗** opens the canonical source docs in a browser.
- Auto-reconnects to the daemon with backoff; a status bar item shows the link
  state.

## Requirements

The docscope daemon must be running:

```bash
cd ../../daemon && uv run docscope serve   # ws://127.0.0.1:7317/ws
```

## Build / run from source

```bash
npm install
npm run compile        # bundle to dist/extension.js (esbuild)
npm run typecheck      # tsc --noEmit
```

Then press **F5** in VS Code (Extension Development Host) to launch it.

## Install as a normal extension

To have docscope active in your everyday VS Code window (rather than an
Extension Development Host you have to relaunch every session):

```bash
npm run package                              # -> docscope-<version>.vsix
code --install-extension docscope-*.vsix --force
```

Reload the window afterwards. After making changes, re-run both commands to
pick up the rebuild — `code --install-extension` overwrites the previous
install.

## Settings

| Setting | Default | Description |
| --- | --- | --- |
| `docscope.daemonUrl` | `ws://127.0.0.1:7317/ws` | Daemon WebSocket URL |
| `docscope.contextWindowLines` | `40` | Lines above/below the cursor sent as context |
| `docscope.clientDebounceMs` | `200` | Client-side debounce before streaming a cursor event |
| `docscope.enabledLanguages` | python, rust, js(x), ts(x) | Language ids docscope reacts to |

## Protocol

Client → daemon: `{"type": "context"|"lookup"|"ping", "context": {BufferContext}}`.
Daemon → client: `{"type": "card"|"error"|"pong", ...}`. The daemon applies the
700 ms idle debounce and the symbol-change gate; `lookup` bypasses both.
