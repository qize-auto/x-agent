import * as vscode from "vscode";

/**
 * VS Code 编辑器原生集成
 * 提供 Apply Edit、Insert、Diff 高亮等功能
 */
export class EditorIntegration {
  private _decorationType: vscode.TextEditorDecorationType;
  private _diffDecorationType: vscode.TextEditorDecorationType;

  constructor() {
    // Agent 修改区域的高亮样式
    this._decorationType = vscode.window.createTextEditorDecorationType({
      backgroundColor: "rgba(88, 166, 255, 0.15)",
      border: "1px solid rgba(88, 166, 255, 0.4)",
      borderRadius: "3px",
      overviewRulerColor: "rgba(88, 166, 255, 0.5)",
      overviewRulerLane: vscode.OverviewRulerLane.Right,
    });

    // Diff 删除区域的高亮样式
    this._diffDecorationType = vscode.window.createTextEditorDecorationType({
      backgroundColor: "rgba(248, 81, 73, 0.15)",
      border: "1px solid rgba(248, 81, 73, 0.4)",
      borderRadius: "3px",
      textDecoration: "line-through",
    });
  }

  /**
   * 在指定文件中执行 SEARCH/REPLACE 编辑
   */
  async applyEdit(filePath: string, oldText: string, newText: string): Promise<boolean> {
    const uri = vscode.Uri.file(filePath);
    let doc: vscode.TextDocument;
    try {
      doc = await vscode.workspace.openTextDocument(uri);
    } catch {
      vscode.window.showErrorMessage(`无法打开文件: ${filePath}`);
      return false;
    }

    const fullText = doc.getText();
    const idx = fullText.indexOf(oldText);
    if (idx === -1) {
      vscode.window.showWarningMessage("SEARCH text not found in document");
      return false;
    }

    const editor = await vscode.window.showTextDocument(doc);
    const startPos = doc.positionAt(idx);
    const endPos = doc.positionAt(idx + oldText.length);
    const range = new vscode.Range(startPos, endPos);

    const edit = new vscode.WorkspaceEdit();
    edit.replace(uri, range, newText);
    const success = await vscode.workspace.applyEdit(edit);

    if (success) {
      await doc.save();
      // 高亮修改区域 3 秒
      editor.setDecorations(this._decorationType, [range]);
      setTimeout(() => editor.setDecorations(this._decorationType, []), 3000);
    }
    return success;
  }

  /**
   * 在当前光标位置插入文本
   */
  async insertAtCursor(text: string): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      vscode.window.showWarningMessage("No active editor");
      return;
    }
    await editor.edit((editBuilder) => {
      editBuilder.insert(editor.selection.active, text);
    });
  }

  /**
   * 在编辑器中显示 Diff 高亮（删除区域）
   */
  showDiff(filePath: string, oldText: string): void {
    const uri = vscode.Uri.file(filePath);
    vscode.workspace.openTextDocument(uri).then((doc) => {
      vscode.window.showTextDocument(doc).then((editor) => {
        const fullText = doc.getText();
        const idx = fullText.indexOf(oldText);
        if (idx === -1) return;
        const startPos = doc.positionAt(idx);
        const endPos = doc.positionAt(idx + oldText.length);
        editor.setDecorations(this._diffDecorationType, [new vscode.Range(startPos, endPos)]);
        setTimeout(() => editor.setDecorations(this._diffDecorationType, []), 5000);
      });
    });
  }

  /**
   * 添加诊断信息（lint 结果）
   */
  showDiagnostics(uri: vscode.Uri, diagnostics: vscode.Diagnostic[]): void {
    const collection = vscode.languages.createDiagnosticCollection("xagent");
    collection.set(uri, diagnostics);
  }

  dispose() {
    this._decorationType.dispose();
    this._diffDecorationType.dispose();
  }
}
