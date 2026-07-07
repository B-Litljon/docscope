import * as vscode from "vscode";

export interface CardPayload {
  id: number;
  symbol: string;
  version: string;
  exact: boolean;
  tier: string;
  sourceUrl: string;
  html: string;
  isError?: boolean;
}

export interface SidebarSettings {
  enabledLanguages: string[];
  contextWindowLines: number;
  clientDebounceMs: number;
  daemonUrl: string;
}

/**
 * Sidebar webview that renders doc cards, newest on top. All markdown is
 * rendered to HTML in the extension host; the webview only lays out cards,
 * handles the pin toggle, and forwards "open source" clicks. Zero doc logic.
 */
export class CardsViewProvider implements vscode.WebviewViewProvider {
  public static readonly viewType = "docscope.cards";
  private view: vscode.WebviewView | undefined;
  private readonly pending: CardPayload[] = [];
  private state = "disconnected";
  private settings: SidebarSettings | undefined;

  onUpdateSetting: (key: string, value: string | number | string[]) => void = () => {};
  onAddPackageOverride: (pkg: string, invUrl: string, versioned: boolean) => void = () => {};

  constructor(private readonly extensionUri: vscode.Uri) {}

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "media")],
    };
    view.webview.html = this.html(view.webview);
    view.webview.onDidReceiveMessage((msg) => {
      if (msg?.type === "openSource" && typeof msg.url === "string") {
        void vscode.env.openExternal(vscode.Uri.parse(msg.url));
      } else if (msg?.type === "updateSetting" && typeof msg.key === "string") {
        this.onUpdateSetting(msg.key, msg.value);
      } else if (
        msg?.type === "addPackageOverride" &&
        typeof msg.package === "string" &&
        typeof msg.invUrl === "string"
      ) {
        this.onAddPackageOverride(msg.package, msg.invUrl, Boolean(msg.versioned));
      }
    });
    // Flush anything that arrived before the view was ready.
    view.webview.postMessage({ type: "state", state: this.state });
    if (this.settings) {
      void view.webview.postMessage({ type: "settings", settings: this.settings });
    }
    for (const card of this.pending) {
      void view.webview.postMessage({ type: "card", card });
    }
    this.pending.length = 0;
  }

  pushCard(card: CardPayload): void {
    if (this.view) {
      void this.view.webview.postMessage({ type: "card", card });
    } else {
      this.pending.push(card);
    }
  }

  setState(state: string): void {
    this.state = state;
    void this.view?.webview.postMessage({ type: "state", state });
  }

  setSettings(settings: SidebarSettings): void {
    this.settings = settings;
    void this.view?.webview.postMessage({ type: "settings", settings });
  }

  private html(webview: vscode.Webview): string {
    const nonce = getNonce();
    const scriptUri = webview.asWebviewUri(
      vscode.Uri.joinPath(this.extensionUri, "media", "main.js")
    );
    const styleUri = webview.asWebviewUri(
      vscode.Uri.joinPath(this.extensionUri, "media", "style.css")
    );
    const csp = [
      "default-src 'none'",
      `img-src ${webview.cspSource} https: data:`,
      `style-src ${webview.cspSource} 'unsafe-inline'`,
      `script-src 'nonce-${nonce}'`,
    ].join("; ");
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="${csp}" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <link href="${styleUri}" rel="stylesheet" />
  <title>docscope</title>
</head>
<body>
  <div id="status" class="status"></div>
  <details id="settings" class="settings">
    <summary>Settings</summary>
    <div class="settings-body">
      <div class="field">
        <label>Enabled languages</label>
        <div class="lang-list">
          <label><input type="checkbox" class="lang" data-lang="python" /> python</label>
          <label><input type="checkbox" class="lang" data-lang="rust" /> rust</label>
          <label><input type="checkbox" class="lang" data-lang="javascript" /> javascript</label>
          <label><input type="checkbox" class="lang" data-lang="javascriptreact" /> javascriptreact</label>
          <label><input type="checkbox" class="lang" data-lang="typescript" /> typescript</label>
          <label><input type="checkbox" class="lang" data-lang="typescriptreact" /> typescriptreact</label>
        </div>
      </div>
      <label class="field">Context window lines
        <input id="contextWindowLines" type="number" min="1" />
      </label>
      <label class="field">Client debounce (ms)
        <input id="clientDebounceMs" type="number" min="0" />
      </label>
      <label class="field">Daemon URL
        <input id="daemonUrl" type="text" />
      </label>
      <hr />
      <div class="field">
        <label>Add a doc source for a package with no docs found</label>
        <input id="pkgName" type="text" placeholder="package name, e.g. httpx" />
        <input id="pkgInvUrl" type="text" placeholder="objects.inv URL" />
        <label><input id="pkgVersioned" type="checkbox" /> URL is version-templated ({version})</label>
        <button id="pkgAdd">Add doc source</button>
      </div>
    </div>
  </details>
  <div id="cards"></div>
  <div id="empty" class="empty">Move your cursor over a library symbol, or press
    <kbd>Ctrl+Alt+D</kbd> to look it up.</div>
  <script nonce="${nonce}" src="${scriptUri}"></script>
</body>
</html>`;
  }
}

function getNonce(): string {
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  let text = "";
  for (let i = 0; i < 32; i++) {
    text += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return text;
}
