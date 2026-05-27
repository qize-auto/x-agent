"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.XAgentPanel = void 0;
const vscode = __importStar(require("vscode"));
const api_1 = require("./api");
class XAgentPanel {
    constructor(context, editor) {
        this.context = context;
        this.editor = editor;
        this._disposables = [];
        const config = vscode.workspace.getConfiguration("xagent");
        const host = config.get("serverHost", "127.0.0.1");
        const port = config.get("serverPort", 7727);
        this._api = new api_1.XAgentAPI(host, port);
    }
    resolveWebviewView(webviewView, _context, _token) {
        this._view = webviewView;
        webviewView.webview.options = {
            enableScripts: true,
            localResourceRoots: [this.context.extensionUri],
        };
        webviewView.webview.html = this._getHtml(webviewView.webview);
        webviewView.webview.onDidReceiveMessage(async (message) => {
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
        }, undefined, this._disposables);
        // 自动检查服务器状态
        this._checkHealth();
    }
    async _handleChat(text) {
        try {
            this._postMessage({ type: "status", text: "思考中..." });
            const result = await this._api.chat(text);
            this._postMessage({ type: "response", text: result.response });
        }
        catch (e) {
            this._postMessage({ type: "error", text: e.message });
        }
    }
    async _handleTask(goal) {
        try {
            this._postMessage({ type: "status", text: "任务规划中..." });
            const result = await this._api.task(goal);
            this._postMessage({
                type: "taskResult",
                goal: result.goal,
                status: result.status,
                subtasks: result.subtasks,
            });
        }
        catch (e) {
            this._postMessage({ type: "error", text: e.message });
        }
    }
    checkHealth() {
        this._checkHealth();
    }
    async _checkHealth() {
        const ok = await this._api.health();
        this._postMessage({
            type: "health",
            ok,
            url: this._api.baseUrl,
        });
    }
    async _insertCode(code) {
        const editor = vscode.window.activeTextEditor;
        if (!editor) {
            vscode.window.showWarningMessage("No active editor");
            return;
        }
        await editor.edit((edit) => {
            edit.insert(editor.selection.active, code);
        });
    }
    askSelection() {
        const editor = vscode.window.activeTextEditor;
        if (!editor)
            return;
        const selection = editor.document.getText(editor.selection);
        if (!selection)
            return;
        const message = `请分析以下代码:\n\n\`\`\`${editor.document.languageId}\n${selection}\n\`\`\``;
        if (this._view) {
            this._view.show?.(true);
            this._postMessage({ type: "prefill", text: message });
        }
    }
    async _handleApplyEdit(data) {
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
    _postMessage(msg) {
        this._view?.webview.postMessage(msg);
    }
    _getHtml(webview) {
        const scriptUri = webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, "media", "panel.js"));
        const styleUri = webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, "media", "panel.css"));
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
exports.XAgentPanel = XAgentPanel;
XAgentPanel.viewType = "xagent.sidebar";
//# sourceMappingURL=panel.js.map