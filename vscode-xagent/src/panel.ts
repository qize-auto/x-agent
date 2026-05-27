import * as vscode from "vscode";
import { XAgentAPI } from "./api";
import { EditorIntegration } from "./editor";

export class XAgentPanel {
  public static readonly viewType = "xagent.sidebar";
  private _view?: vscode.WebviewView;
  private _api: XAgentAPI;
  private _disposables: vscode.Disposable[] = [];

  constructor(
    private context: vscode.ExtensionContext,
    private editor: EditorIntegration,
  ) {
    const config = vscode.workspace.getConfiguration("xagent");
    const host = config.get<string>("serverHost", "127.0.0.1");
    const port = config.get<number>("serverPort", 7727);
    this._api = new XAgentAPI(host, port);
  }

  resolveWebviewView(
    webviewView: vscode.WebviewView,
    _context: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken
  ) {
    this._view = webviewView;
    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [this.context.extensionUri],
    };
    webviewView.webview.html = this._getHtml(webviewView.webview);

    webviewView.webview.onDidReceiveMessage(
      async (message) => {
        switch (message.type) {
          case "chat":
            await this._handleChat(message.text);
            break;
          case "task":
            await this._handleTask(message.text);
            break;
          case "checkHealth":
            await this._checkHealth();
            break;
          case "insertCode":
            await this._insertCode(message.code);
            break;
          case "applyEdit":
            await this._handleApplyEdit(message);
            break;
        }
      },
      undefined,
      this._disposables
    );

    // 自动检查服务器状态
    this._checkHealth();
  }

  private async _handleChat(text: string) {
    try {
      this._postMessage({ type: "status", text: "思考中..." });
      const result = await this._api.chat(text);
      this._postMessage({ type: "response", text: result.response });
    } catch (e: any) {
      this._postMessage({ type: "error", text: e.message });
    }
  }

  private async _handleTask(goal: string) {
    try {
      this._postMessage({ type: "status", text: "任务规划中..." });
      const result = await this._api.task(goal);
      this._postMessage({
        type: "taskResult",
        goal: result.goal,
        status: result.status,
        subtasks: result.subtasks,
      });
    } catch (e: any) {
      this._postMessage({ type: "error", text: e.message });
    }
  }

  public checkHealth() {
    this._checkHealth();
  }

  private async _checkHealth() {
    const ok = await this._api.health();
    this._postMessage({
      type: "health",
      ok,
      url: (this._api as any).baseUrl,
    });
  }

  private async _insertCode(code: string) {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      vscode.window.showWarningMessage("No active editor");
      return;
    }
    await editor.edit((edit) => {
      edit.insert(editor.selection.active, code);
    });
  }

  public askSelection() {
    const editor = vscode.window.activeTextEditor;
    if (!editor) return;
    const selection = editor.document.getText(editor.selection);
    if (!selection) return;

    const message = `请分析以下代码:\n\n\`\`\`${editor.document.languageId}\n${selection}\n\`\`\``;
    if (this._view) {
      this._view.show?.(true);
      this._postMessage({ type: "prefill", text: message });
    }
  }

  private async _handleApplyEdit(data: any) {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      vscode.window.showWarningMessage("No active editor to apply edit");
      return;
    }
    const doc = editor.document;
    const fullText = doc.getText();
    const search = data.search || "";
    const replace = data.replace || "";
    const idx = fullText.indexOf(search);
    if (idx === -1) {
      this._postMessage({ type: "error", text: "SEARCH text not found in current document" });
      return;
    }
    const success = await this.editor.applyEdit(doc.fileName, search, replace);
    if (success) {
      this._postMessage({ type: "status", text: "Edit applied successfully" });
    }
  }

  private _postMessage(msg: any) {
    this._view?.webview.postMessage(msg);
  }

  private _getHtml(webview: vscode.Webview): string {
    const scriptUri = webview.asWebviewUri(
      vscode.Uri.joinPath(this.context.extensionUri, "media", "panel.js")
    );
    const styleUri = webview.asWebviewUri(
      vscode.Uri.joinPath(this.context.extensionUri, "media", "panel.css")
    );
    return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link href="${styleUri}" rel="stylesheet">
</head>
<body>
  <div id="app">
    <div id="status-bar">未连接</div>
    <div id="chat-history"></div>
    <div id="input-area">
      <textarea id="input-box" placeholder="Ask X-Agent..."></textarea>
      <button id="send-btn">Send</button>
    </div>
  </div>
  <script src="${scriptUri}"></script>
</body>
</html>`;
  }

  dispose() {
    this._disposables.forEach((d) => d.dispose());
  }
}
