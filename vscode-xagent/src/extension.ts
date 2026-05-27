import * as vscode from "vscode";
import { XAgentPanel } from "./panel";
import { EditorIntegration } from "./editor";
import { spawn, ChildProcess } from "child_process";
import * as path from "path";

let serverProcess: ChildProcess | null = null;
let panelProvider: XAgentPanel | null = null;
let editorIntegration: EditorIntegration | null = null;

export function activate(context: vscode.ExtensionContext) {
  editorIntegration = new EditorIntegration();
  context.subscriptions.push(editorIntegration);

  // 注册 Sidebar WebView
  panelProvider = new XAgentPanel(context, editorIntegration);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(XAgentPanel.viewType, panelProvider)
  );

  // 注册命令: Ask X-Agent (右键菜单)
  context.subscriptions.push(
    vscode.commands.registerCommand("xagent.ask", () => {
      panelProvider?.askSelection();
    })
  );

  // 注册命令: Apply Edit (原生编辑器集成)
  context.subscriptions.push(
    vscode.commands.registerCommand("xagent.applyEdit", async (filePath: string, oldText: string, newText: string) => {
      const success = await editorIntegration?.applyEdit(filePath, oldText, newText);
      if (success) {
        vscode.window.showInformationMessage("X-Agent: Edit applied");
      }
    })
  );

  // 注册命令: Show Diff
  context.subscriptions.push(
    vscode.commands.registerCommand("xagent.showDiff", (filePath: string, oldText: string) => {
      editorIntegration?.showDiff(filePath, oldText);
    })
  );

  // 注册命令: Start Server
  context.subscriptions.push(
    vscode.commands.registerCommand("xagent.startServer", async () => {
      if (serverProcess) {
        vscode.window.showInformationMessage("X-Agent server already running");
        return;
      }
      const pythonPath = "python";
      serverProcess = spawn(pythonPath, ["-m", "xagent.server"], {
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
    })
  );

  // 注册命令: Stop Server
  context.subscriptions.push(
    vscode.commands.registerCommand("xagent.stopServer", () => {
      if (serverProcess) {
        serverProcess.kill();
        serverProcess = null;
        vscode.window.showInformationMessage("X-Agent server stopped");
      }
    })
  );
}

export function deactivate() {
  if (serverProcess) {
    serverProcess.kill();
    serverProcess = null;
  }
  panelProvider?.dispose();
}
