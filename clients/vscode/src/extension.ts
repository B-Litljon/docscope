import MarkdownIt from "markdown-it";
import * as vscode from "vscode";

import { CardsViewProvider } from "./cardsView";
import { DaemonClient, IncomingMessage } from "./daemonClient";

const md = new MarkdownIt({ html: false, linkify: true, breaks: false });

// vscode languageId -> docscope language tag
const LANGUAGE_MAP: Record<string, string> = {
  python: "python",
  rust: "rust",
  javascript: "javascript",
  javascriptreact: "javascript",
  typescript: "typescript",
  typescriptreact: "typescript",
};

let cardSeq = 0;

export function activate(context: vscode.ExtensionContext): void {
  const provider = new CardsViewProvider(context.extensionUri);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(CardsViewProvider.viewType, provider, {
      webviewOptions: { retainContextWhenHidden: true },
    })
  );

  const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBar.text = "$(book) docscope";
  statusBar.tooltip = "docscope documentation navigator";
  statusBar.show();
  context.subscriptions.push(statusBar);

  const client = new DaemonClient(configString("daemonUrl", "ws://127.0.0.1:7317/ws"));
  client.onState = (state) => {
    provider.setState(state);
    statusBar.text = state === "connected" ? "$(book) docscope" : "$(book) docscope $(warning)";
  };
  client.onMessage = (msg) => handleMessage(msg, provider);
  client.connect();
  context.subscriptions.push({ dispose: () => client.dispose() });

  // Client-side pre-debounce on cursor movement.
  let debounceTimer: NodeJS.Timeout | undefined;
  const onMove = (editor: vscode.TextEditor | undefined) => {
    if (!editor || !isEnabled(editor.document)) {
      return;
    }
    if (debounceTimer) {
      clearTimeout(debounceTimer);
    }
    const delay = configNumber("clientDebounceMs", 200);
    debounceTimer = setTimeout(() => {
      const ctx = buildContext(editor);
      if (ctx) {
        client.send({ type: "context", context: ctx });
      }
    }, delay);
  };

  context.subscriptions.push(
    vscode.window.onDidChangeTextEditorSelection((e) => onMove(e.textEditor))
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("docscope.forceLookup", () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) {
        return;
      }
      const ctx = buildContext(editor, /*force*/ true);
      if (ctx) {
        client.send({ type: "lookup", context: ctx });
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("docscope.reconnect", () => client.reconnect())
  );

  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("docscope.daemonUrl")) {
        client.setUrl(configString("daemonUrl", "ws://127.0.0.1:7317/ws"));
      }
    })
  );
}

export function deactivate(): void {
  /* subscriptions handle teardown */
}

function handleMessage(msg: IncomingMessage, provider: CardsViewProvider): void {
  if (msg.type === "card") {
    provider.pushCard({
      id: ++cardSeq,
      symbol: msg.card.symbol,
      version: `${msg.card.package} ${msg.card.version}`,
      exact: msg.card.exact_version,
      tier: msg.card.cache_tier,
      sourceUrl: msg.card.source_url,
      html: md.render(msg.markdown),
    });
  } else if (msg.type === "error") {
    provider.pushCard({
      id: ++cardSeq,
      symbol: msg.error.reason,
      version: "",
      exact: false,
      tier: "",
      sourceUrl: "",
      html: md.render(msg.error.detail ?? "No documentation found."),
      isError: true,
    });
  }
}

interface BufferContextPayload {
  file_path: string;
  language: string;
  text: string;
  cursor_line: number;
  cursor_col: number;
  workspace_root: string | null;
  window_start_line: number;
}

function buildContext(
  editor: vscode.TextEditor,
  force = false
): BufferContextPayload | undefined {
  const doc = editor.document;
  if (!force && !isEnabled(doc)) {
    return undefined;
  }
  const language = LANGUAGE_MAP[doc.languageId];
  if (!language) {
    return undefined;
  }
  const cursor = editor.selection.active;
  const window = configNumber("contextWindowLines", 40);
  const startLine = Math.max(0, cursor.line - window);
  const endLine = Math.min(doc.lineCount - 1, cursor.line + window);
  const range = new vscode.Range(
    new vscode.Position(startLine, 0),
    new vscode.Position(endLine, doc.lineAt(endLine).text.length)
  );
  const workspaceFolder = vscode.workspace.getWorkspaceFolder(doc.uri);
  return {
    file_path: doc.uri.fsPath,
    language,
    text: doc.getText(range),
    cursor_line: cursor.line,
    cursor_col: cursor.character,
    workspace_root: workspaceFolder ? workspaceFolder.uri.fsPath : null,
    window_start_line: startLine,
  };
}

function isEnabled(doc: vscode.TextDocument): boolean {
  const enabled = configArray("enabledLanguages", Object.keys(LANGUAGE_MAP));
  return enabled.includes(doc.languageId);
}

function config(): vscode.WorkspaceConfiguration {
  return vscode.workspace.getConfiguration("docscope");
}

function configString(key: string, fallback: string): string {
  return config().get<string>(key, fallback);
}

function configNumber(key: string, fallback: number): number {
  return config().get<number>(key, fallback);
}

function configArray(key: string, fallback: string[]): string[] {
  return config().get<string[]>(key, fallback);
}
