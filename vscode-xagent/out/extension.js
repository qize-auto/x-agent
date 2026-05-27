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
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = __importStar(require("vscode"));
const panel_1 = require("./panel");
const editor_1 = require("./editor");
const child_process_1 = require("child_process");
const path = __importStar(require("path"));
let serverProcess = null;
let panelProvider = null;
let editorIntegration = null;
function activate(context) {
    editorIntegration = new editor_1.EditorIntegration();
    context.subscriptions.push(editorIntegration);
    // 注册 Sidebar WebView
    panelProvider = new panel_1.XAgentPanel(context, editorIntegration);
    context.subscriptions.push(vscode.window.registerWebviewViewProvider(panel_1.XAgentPanel.viewType, panelProvider));
    // 注册命令: Ask X-Agent (右键菜单)
    context.subscriptions.push(vscode.commands.registerCommand("xagent.ask", () => {
        panelProvider?.askSelection();
    }));
    // 注册命令: Apply Edit (原生编辑器集成)
    context.subscriptions.push(vscode.commands.registerCommand("xagent.applyEdit", async (filePath, oldText, newText) => {
        const success = await editorIntegration?.applyEdit(filePath, oldText, newText);
        if (success) {
            vscode.window.showInformationMessage("X-Agent: Edit applied");
        }
    }));
    // 注册命令: Show Diff
    context.subscriptions.push(vscode.commands.registerCommand("xagent.showDiff", (filePath, oldText) => {
        editorIntegration?.showDiff(filePath, oldText);
    }));
    // 注册命令: Start Server
    context.subscriptions.push(vscode.commands.registerCommand("xagent.startServer", async () => {
        if (serverProcess) {
            vscode.window.showInformationMessage("X-Agent server already running");
            return;
        }
        const pythonPath = "python";
        serverProcess = (0, child_process_1.spawn)(pythonPath, ["-m", "xagent.server"], {
            cwd: path.join(context.extensionPath, ".."),
            detached: false,
        });
        serverProcess.stdout?.on("data", (data) => {
            console.log(`[X-Agent] ${data}`);
        });
        serverProcess.stderr?.on("data", (data) => {
            console.error(`[X-Agent] ${data}`);
        });
        vscode.window.showInformationMessage("X-Agent server starting...");
        setTimeout(() => {
            panelProvider?.checkHealth();
        }, 2000);
    }));
    // 注册命令: Stop Server
    context.subscriptions.push(vscode.commands.registerCommand("xagent.stopServer", () => {
        if (serverProcess) {
            serverProcess.kill();
            serverProcess = null;
            vscode.window.showInformationMessage("X-Agent server stopped");
        }
    }));
}
function deactivate() {
    if (serverProcess) {
        serverProcess.kill();
        serverProcess = null;
    }
    panelProvider?.dispose();
}
//# sourceMappingURL=extension.js.map