"use strict";

const vscode = require("vscode");
const { exec } = require("child_process");

let outputChannel = null;
let statusBarItem = null;
let taskTreeProvider = null;
let diagnosticCollection = null;
let apiProcess = null;       // Spawned API server
let webSocket = null;        // WebSocket for interactive mode
let currentTaskId = null;
let taskHistory = [];

// ============================================================================
// Configuration
// ============================================================================

function getConfig() {
  return vscode.workspace.getConfiguration("devagent");
}

function getApiUrl() {
  return getConfig().get("apiUrl", "http://127.0.0.1:8911");
}

function getWsUrl() {
  return getApiUrl().replace(/^http/, "ws");
}

function getWorkspace() {
  return vscode.workspace.workspaceFolders?.[0]?.uri?.fsPath || "";
}

function getOutputDir() {
  return getConfig().get("outputDir", "${workspaceFolder}/outputs")
    .replace("${workspaceFolder}", getWorkspace());
}

// ============================================================================
// Activation
// ============================================================================

function activate(context) {
  try {
    console.log("[DevAgent] Extension activating...");

    diagnosticCollection = vscode.languages.createDiagnosticCollection("devagent");
    context.subscriptions.push(diagnosticCollection);

    outputChannel = vscode.window.createOutputChannel("DevAgent");
    context.subscriptions.push(outputChannel);
    outputChannel.appendLine("[DevAgent] v3.0.0 activating...");

    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBarItem.command = "devagent.showTaskHistory";
    statusBarItem.text = "$(hubot) DevAgent";
    statusBarItem.tooltip = "DevAgent — Click for task history";
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    taskTreeProvider = new DevAgentTaskProvider();
    context.subscriptions.push(
      vscode.window.registerTreeDataProvider("devagentTasks", taskTreeProvider)
    );

  // ── Register all commands ──────────────────────────────────────────────
  const cmds = [
    // Core task commands (unified ReAct engine)
    vscode.commands.registerCommand("devagent.analyzeRequirement",
      () => executeTask("design")),
    vscode.commands.registerCommand("devagent.generateCode",
      () => executeTask("implement")),
    vscode.commands.registerCommand("devagent.runTests",
      () => executeTask("test")),
    vscode.commands.registerCommand("devagent.debugCode",
      () => executeTask("debug")),
    vscode.commands.registerCommand("devagent.repairBug",
      () => executeTask("repair")),
    vscode.commands.registerCommand("devagent.fullPipeline",
      () => executeTask("full")),
    vscode.commands.registerCommand("devagent.runAgentic",
      () => executeTask("agentic")),

    // Interactive mode commands
    vscode.commands.registerCommand("devagent.approveReview",
      () => sendReviewResponse("approve")),
    vscode.commands.registerCommand("devagent.reviseReview",
      () => sendReviewResponse("revise")),
    vscode.commands.registerCommand("devagent.rejectReview",
      () => sendReviewResponse("reject")),
    vscode.commands.registerCommand("devagent.pauseAgent",
      () => sendCommand("pause")),
    vscode.commands.registerCommand("devagent.resumeAgent",
      () => sendCommand("resume")),
    vscode.commands.registerCommand("devagent.abortAgent",
      () => sendCommand("abort")),

    // Utility commands
    vscode.commands.registerCommand("devagent.startApiServer",
      () => startApiServer()),
    vscode.commands.registerCommand("devagent.stopApiServer",
      () => stopApiServer()),
    vscode.commands.registerCommand("devagent.runWithConfig",
      () => executeTaskWithConfig()),
    vscode.commands.registerCommand("devagent.showTaskHistory",
      () => showTaskHistory()),
    vscode.commands.registerCommand("devagent.analyzeCurrentFile",
      () => analyzeCurrentFile()),
    vscode.commands.registerCommand("devagent.openOutputDir",
      () => openOutputDir()),
    vscode.commands.registerCommand("devagent.clearDiagnostics",
      () => clearDiagnostics()),

    // LSP commands
    vscode.commands.registerCommand("devagent.startLsp",
      () => startLspClient(context)),
    vscode.commands.registerCommand("devagent.stopLsp",
      () => stopLspClient()),
  ];
    cmds.forEach(c => context.subscriptions.push(c));

    console.log("[DevAgent] Extension activated");
    outputChannel.appendLine("[DevAgent] Extension ready. Ctrl+Shift+P → type 'DevAgent' to see commands.");
    outputChannel.appendLine("[DevAgent] Tip: Run 'DevAgent: Start API Server' first.");

  } catch (err) {
    console.error("[DevAgent] Activation failed:", err.message, err.stack);
    if (outputChannel) {
      outputChannel.appendLine(`[DevAgent] ACTIVATION ERROR: ${err.message}`);
    }
    vscode.window.showErrorMessage(`DevAgent activation failed: ${err.message}`);
  }
}

// ============================================================================
// Task execution — unified architecture
// ============================================================================

const MODE_LABELS = {
  design:    { label: "Analyze & Design",    icon: "$(list-tree)" },
  implement: { label: "Generate Code + Test", icon: "$(code)" },
  repair:    { label: "Debug & Repair",      icon: "$(bug)" },
  full:      { label: "Full Pipeline",       icon: "$(rocket)" },
  test:      { label: "Run Tests",           icon: "$(beaker)" },
  debug:     { label: "Debug Analysis",      icon: "$(debug-alt)" },
  agentic:   { label: "Autonomous Agent",    icon: "$(hubot)" },
};

async function executeTask(taskType) {
  if (!MODE_LABELS[taskType]) return;
  const ml = MODE_LABELS[taskType];
  const apiUrl = getApiUrl();
  const wsUrl = getWsUrl();
  const workspace = getWorkspace();
  const outputDir = getOutputDir();
  const interactive = getConfig().get("interactive", false);

  // ── Collect inputs ────────────────────────────────────────────────────
  let inputPath = "";
  let codePath = "";

  if (["design", "implement", "full"].includes(taskType)) {
    // Always ask user to select the input file — don't auto-pick
    // to avoid accidentally selecting old generated artifacts
    const r = await vscode.window.showOpenDialog({
      canSelectFiles: true, canSelectFolders: false,
      openLabel: "Select your REQUIREMENTS or DESIGN document (.md)",
      filters: { "Requirements / Design": ["md"] },
      defaultUri: workspace ? vscode.Uri.file(workspace) : undefined,
    });
    if (!r || r.length === 0) return;
    inputPath = r[0].fsPath;

    // Warn if user selected something that looks like generated output
    if (inputPath.includes("01_requirements") || inputPath.includes("02_design")
        || inputPath.includes("03_implementation") || inputPath.includes("outputs")) {
      vscode.window.showWarningMessage(
        "[DevAgent] You selected a file in an output/generated directory. "
        + "For best results, select the ORIGINAL requirements document instead."
      );
    }
  }

  if (["repair", "test", "debug"].includes(taskType)) {
    codePath = workspace || "";
    if (!codePath) {
      const r = await vscode.window.showOpenDialog({
        canSelectFiles: false, canSelectFolders: true,
        openLabel: "Select workspace directory",
      });
      if (!r || r.length === 0) return;
      codePath = r[0].fsPath;
    }
  }

  // ── Execute ────────────────────────────────────────────────────────────
  outputChannel.show();
  outputChannel.appendLine(`\n${"━".repeat(55)}`);
  outputChannel.appendLine(`[DevAgent] ${ml.label} (mode=${taskType})`);
  outputChannel.appendLine(`  Input:    ${inputPath || "(none)"}`);
  outputChannel.appendLine(`  Workspace: ${codePath || workspace}`);
  outputChannel.appendLine(`  Output:   ${outputDir}`);
  if (interactive) outputChannel.appendLine(`  Mode:     INTERACTIVE (approval + review)`);
  outputChannel.appendLine(`${"━".repeat(55)}`);

  statusBarItem.text = `$(sync~spin) DevAgent: ${ml.label}...`;

  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: `[DevAgent] ${ml.label}...`, cancellable: true },
    async (progress, token) => {
      try {
        // Build mode instruction for unified agent
        const modeInstructions = {
          design:    "Analyze the requirements document. Call analyze_requirements, then design_architecture, then submit.",
          implement: "Build the project from the design. Call analyze_requirements, design_architecture, generate_code, test_run. If tests pass, submit.",
          repair:    "Fix bugs in the codebase. Explore the code, run tests, use debug_issue and repair_code. Submit when tests pass.",
          full:      "Complete end-to-end development from the input. Call analyze_requirements, design_architecture, generate_code, test_run. If tests fail, use debug_issue+repair_code. When all tests pass, submit.",
          test:      "Generate and run tests for the code. Use generate_tests or test_run.",
          debug:     "Debug failures: run tests, use debug_issue, then repair_code.",
          agentic:   "Complete the task autonomously using available tools.",
        };

        let taskBody = modeInstructions[taskType] || modeInstructions.agentic;
        if (inputPath) {
          const fs = require("fs");
          taskBody += `\n\n## Input Document\n\n${fs.readFileSync(inputPath, "utf-8")}`;
        }
        taskBody += `\n\n## Workspace\n${codePath || workspace}`;

        // Always use V2 endpoint with unified agent
        const payload = {
          task: "agentic",
          input: taskBody,
          code: codePath || workspace,
          output: outputDir,
          language: "python",
          max_retry: taskType === "full" ? 5 : 2,
        };

        const response = await fetch(`${apiUrl}/api/v2/tasks/agentic`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });

        if (!response.ok) {
          const err = await response.text();
          throw new Error(`Server ${response.status}: ${err.slice(0, 300)}`);
        }

        const result = await response.json();
        const backendTaskId = result.task_id;
        currentTaskId = backendTaskId;

        // Connect WebSocket with the REAL server-assigned task ID
        connectWebSocket(
          `${wsUrl}/api/v2/tasks/${backendTaskId}/interactive?mode=controller`,
          backendTaskId
        );

        outputChannel.appendLine(`  Task: ${backendTaskId}`);
        outputChannel.appendLine(`  ℹ️  Review popups will appear at each phase milestone.`);

        // Poll for completion
        let finalR = result;
        for (let i = 0; i < 1200; i++) {
          if ((i + 1) % 40 === 0) {
            // Every minute, show progress
            outputChannel.appendLine(`  运行中... (${Math.round((i+1)*1.5/60)}min)`);
          }
          await new Promise(r => setTimeout(r, 1500));
          try {
            const sr = await fetch(`${apiUrl}/api/v1/tasks/${backendTaskId}`);
            if (sr.ok) {
              finalR = await sr.json();
              const st = finalR.status || "RUNNING";
              const m = finalR.metrics || {};
              statusBarItem.text = `$(sync~spin) DevAgent [${st}]${m.iterations ? ' #'+m.iterations : ''}${m.test_collected ? ' ✓'+m.test_passed+'/'+m.test_collected : ''}`;
              if (st !== "RUNNING") break;
            }
          } catch (_) {}
        }

        outputChannel.appendLine(`\n[DevAgent] ${finalR.status || "Done"}`);

        if (finalR.errors?.length) {
          outputChannel.appendLine(`  Errors (${finalR.errors.length}):`);
          finalR.errors.forEach(e => {
            const msg = typeof e === "string" ? e : (e.message || e.phase + ": " + (e.message || ""));
            outputChannel.appendLine(`    - ${msg.slice(0, 200)}`);
          });
        }
        if (finalR.metrics) {
          const m = finalR.metrics;
          if (m.test_collected) outputChannel.appendLine(`  Tests: ${m.test_passed}/${m.test_collected} passed`);
          if (m.iterations) outputChannel.appendLine(`  Iterations: ${m.iterations}`);
        }

        const record = {
          task_id: backendTaskId, task_type: taskType,
          displayName: ml.label, status: finalR.status || "TIMEOUT",
          timestamp: new Date().toLocaleString(),
          errors: finalR.errors || [], metrics: finalR.metrics || {},
        };
        taskHistory.unshift(record);
        if (taskTreeProvider) taskTreeProvider.refresh();

        try { await vscode.commands.executeCommand("revealFileInOS", vscode.Uri.file(outputDir)); } catch (_) {}

        vscode.window.showInformationMessage(
          `[DevAgent] ${ml.label}: ${finalR.status || "Done"}`,
          "View Output"
        ).then(a => { if (a) outputChannel.show(); });

      } catch (error) {
        outputChannel.appendLine(`\n[DevAgent] ✗ Failed: ${error.message}`);
        vscode.window.showErrorMessage(`[DevAgent] ${error.message}`);
      } finally {
        statusBarItem.text = `$(hubot) DevAgent`;
        // Don't disconnect immediately — keep WS for a moment
        // to receive any late-arriving events
        setTimeout(() => disconnectWebSocket(), 3000);
      }
    }
  );
}

// ============================================================================
// WebSocket — real-time events
// ============================================================================

function connectWebSocket(url, taskId) {
  disconnectWebSocket();
  currentTaskId = taskId || currentTaskId;
  try {
    const WS = require("ws");
    webSocket = new WS(url, { timeout: 3000 });
    webSocket.on("open", () => {
      outputChannel.appendLine("  🔗 Connected to DevAgent (real-time)");
    });
    webSocket.on("message", (data) => {
      try {
        const msg = JSON.parse(data.toString());
        handleWsMessage(msg);
      } catch (_) {}
    });
    webSocket.on("error", () => {});
    webSocket.on("close", () => {
      outputChannel.appendLine("  🔌 Disconnected");
    });
  } catch (_) {}
}

function disconnectWebSocket() {
  if (webSocket) {
    try { webSocket.close(); } catch (_) {}
    webSocket = null;
  }
  // Auto-check task status when WS disconnects (agent might have finished)
  setTimeout(async () => {
    if (currentTaskId) {
      try {
        const resp = await fetch(`${getApiUrl()}/api/v1/tasks/${currentTaskId}`);
        if (resp.ok) {
          const data = await resp.json();
          if (data.status && data.status !== "RUNNING") {
            statusBarItem.text = `$(hubot) DevAgent`;
            outputChannel.appendLine(`[DevAgent] ${data.status} (iterations: ${(data.metrics||{}).iterations||0})`);
          }
        }
      } catch (_) {}
    }
  }, 2000);
}

function handleWsMessage(msg) {
  const type = msg.type || "";
  const data = msg.data || {};

  switch (type) {
    case "tool.completed": {
      const tool = data.tool || "?";
      const ok = data.success ? "✓" : "✗";
      const preview = (data.output_preview || "").slice(0, 100);
      outputChannel.appendLine(`  ${ok} ${tool} ${preview}`);
      break;
    }
    case "tool.error": {
      outputChannel.appendLine(`  ✗ ${data.tool}: ${(data.error || "").slice(0, 120)}`);
      break;
    }
    case "review.requested": {
      const reviewId = data.id || "";
      const phase = data.phase || "?";
      const title = data.title || "";
      const quality = data.quality_score || "?";
      const summary = (data.summary || "").slice(0, 300);
      outputChannel.appendLine(`\n  📋 阶段审核 — ${phase}: ${title}`);
      outputChannel.appendLine(`     质量评分: ${quality}`);
      outputChannel.appendLine(`     摘要: ${summary}`);
      vscode.window.showInformationMessage(
        `[DevAgent 审核] ${title} (${quality})`,
        { modal: false },
        "批准", "要求修改", "拒绝"
      ).then(async choice => {
        if (!choice || !reviewId) return;
        let decision, feedback = "", suggestions = [];
        if (choice === "批准") {
          decision = "approve";
        } else if (choice === "要求修改") {
          decision = "revise";
          feedback = await vscode.window.showInputBox({
            prompt: "需要改什么？", placeHolder: "描述需要修改的内容..."
          }) || "";
          const sugText = await vscode.window.showInputBox({
            prompt: "具体建议（用逗号分隔）", placeHolder: "添加空值检查, 修复边界条件..."
          }) || "";
          suggestions = sugText.split(",").map(s => s.trim()).filter(Boolean);
        } else {
          decision = "reject";
          feedback = await vscode.window.showInputBox({
            prompt: "为什么拒绝？", placeHolder: "根本性问题..."
          }) || "";
        }
        sendReviewResponse(reviewId, decision, feedback, suggestions);
      });
      break;
    }
    case "approval.requested": {
      const apId = data.id || "";
      const desc = (data.description || "").slice(0, 200);
      outputChannel.appendLine(`  ⚠ 审批请求: ${desc}`);
      vscode.window.showInformationMessage(
        `[DevAgent 审批] ${desc}`,
        { modal: false },
        "批准", "拒绝"
      ).then(choice => {
        if (!choice || !apId) return;
        sendApprovalResponse(apId, choice === "批准" ? "approve" : "deny");
      });
      break;
    }
    case "agent.question": {
      const qId = data.id || "";
      const q = data.question || "";
      outputChannel.appendLine(`  ❓ Agent提问: ${q}`);
      vscode.window.showInputBox({
        prompt: `Agent: ${q}`,
        placeHolder: "输入你的回答..."
      }).then(answer => {
        if (answer && qId && webSocket) {
          webSocket.send(JSON.stringify({
            type: "question.response",
            data: { question_id: qId, answer: answer }
          }));
          outputChannel.appendLine(`  → 已回答: ${answer.slice(0, 100)}`);
        }
      });
      break;
    }
    case "progress.snapshot": {
      const iter = data.iteration || 0;
      const max = data.max_iterations || 50;
      const phase = data.phase || "?";
      const tests = data.test_summary;
      let testStr = "";
      if (tests) testStr = ` 测试:${tests.passed}/${tests.collected}`;
      statusBarItem.text = `$(hubot) [${iter}/${max}] ${phase}${testStr}`;
      break;
    }
    case "task.completed":
    case "task.failed": {
      statusBarItem.text = `$(hubot) DevAgent`;
      break;
    }
  }
}

async function sendReviewResponse(reviewId, decision, feedback, suggestions) {
  if (!webSocket) { vscode.window.showErrorMessage("[DevAgent] 未连接 — 请重新运行任务"); return; }
  webSocket.send(JSON.stringify({
    type: "review.response",
    data: {
      review_id: reviewId,
      decision: decision,
      feedback: feedback || "",
      suggestions: suggestions || [],
    }
  }));
  outputChannel.appendLine(`  → 审核响应: ${decision}${feedback ? " — " + feedback.slice(0, 80) : ""}`);
}

async function sendApprovalResponse(approvalId, resolution, note) {
  if (!webSocket) { vscode.window.showErrorMessage("[DevAgent] 未连接"); return; }
  webSocket.send(JSON.stringify({
    type: "approval.response",
    data: {
      approval_id: approvalId,
      resolution: resolution,
      note: note || "",
    }
  }));
  outputChannel.appendLine(`  → 审批响应: ${resolution}`);
}

async function sendCommand(command) {
  if (!webSocket) {
    vscode.window.showErrorMessage("[DevAgent] No active session");
    return;
  }
  webSocket.send(JSON.stringify({
    type: `command.${command}`,
    data: {},
  }));
  outputChannel.appendLine(`  → Command: ${command}`);
}

// ============================================================================
// API Server lifecycle — spawn devagent-api as child process
// ============================================================================

function resolvePython() {
  const path = require("path");
  const fs = require("fs");
  const extRoot = vscode.extensions.getExtension("devagent.devagent")?.extensionPath || __dirname;
  const projectRoot = path.dirname(path.dirname(extRoot)); // DevAgent/
  const candidates = [
    path.join(projectRoot, "venv", "bin", "python3"),
    path.join(projectRoot, "venv", "bin", "python"),
    "python3", "python",
  ];
  for (const c of candidates) {
    try { if (fs.existsSync(c)) return c; } catch (_) {}
  }
  return "python3";
}

function resolveProjectRoot() {
  const path = require("path");
  const extRoot = vscode.extensions.getExtension("devagent.devagent")?.extensionPath || __dirname;
  return path.dirname(path.dirname(extRoot));
}

function startApiServer() {
  if (apiProcess) {
    vscode.window.showInformationMessage("[DevAgent] API server already running");
    return;
  }

  const python = resolvePython();
  const cwd = resolveProjectRoot();
  const interactive = getConfig().get("interactive", false) ? "full" : "off";

  outputChannel.show();
  outputChannel.appendLine(`[DevAgent] Starting API server...`);
  outputChannel.appendLine(`  Python: ${python}`);
  outputChannel.appendLine(`  CWD: ${cwd}`);
  outputChannel.appendLine(`  Interactive: ${interactive}`);

  apiProcess = exec(
    `${python} -m uvicorn devagent.api.app:app --host 127.0.0.1 --port 8911`,
    { cwd: cwd, env: { ...process.env, DEVAGENT_INTERACTIVE: interactive } }
  );

  apiProcess.stdout?.on("data", (d) => outputChannel.appendLine(`[API] ${d.toString().trim()}`));
  apiProcess.stderr?.on("data", (d) => outputChannel.appendLine(`[API] ${d.toString().trim()}`));
  apiProcess.on("close", (code) => {
    outputChannel.appendLine(`[DevAgent] API server stopped (code ${code})`);
    apiProcess = null;
  });

  statusBarItem.text = "$(circle-filled) DevAgent API";
  vscode.window.showInformationMessage("[DevAgent] API server starting on http://127.0.0.1:8911");
}

function stopApiServer() {
  if (!apiProcess) {
    vscode.window.showInformationMessage("[DevAgent] API server not running");
    return;
  }
  apiProcess.kill();
  apiProcess = null;
  statusBarItem.text = "$(hubot) DevAgent";
  vscode.window.showInformationMessage("[DevAgent] API server stopped");
}

// ============================================================================
// Utility commands
// ============================================================================

async function executeTaskWithConfig() {
  const modes = Object.entries(MODE_LABELS).map(([k, m]) => ({
    label: `${m.icon} ${m.label}`,
    description: k,
  }));
  const picked = await vscode.window.showQuickPick(modes, {
    placeHolder: "Select task mode...",
  });
  if (!picked) return;
  await executeTask(picked.description);
}

async function analyzeCurrentFile() {
  const editor = vscode.window.activeTextEditor;
  if (!editor) return vscode.window.showWarningMessage("[DevAgent] No active editor");

  const apiUrl = getApiUrl();
  const doc = editor.document;

  outputChannel.show();
  outputChannel.appendLine(`\n[DevAgent] Analyzing: ${doc.fileName}`);

  try {
    const resp = await fetch(`${apiUrl}/api/v1/analyze/file`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: doc.fileName, language: doc.languageId, content: doc.getText() }),
    });
    if (!resp.ok) throw new Error(`Server ${resp.status}`);
    const result = await resp.json();
    const issues = result.issues || [];

    diagnosticCollection.delete(doc.uri);
    const diagnostics = [];
    for (const issue of issues) {
      const line = Math.max(0, (issue.line || 1) - 1);
      const sev = issue.level === "error" ? vscode.DiagnosticSeverity.Error
                : issue.level === "warning" ? vscode.DiagnosticSeverity.Warning
                : vscode.DiagnosticSeverity.Information;
      diagnostics.push(new vscode.Diagnostic(
        new vscode.Range(line, 0, line, 999), issue.message, sev
      ));
      outputChannel.appendLine(`  [${issue.level || "?"}] L${issue.line}: ${issue.message}`);
    }
    diagnosticCollection.set(doc.uri, diagnostics);
    outputChannel.appendLine(`  → ${issues.length} issue(s)`);
    vscode.window.showInformationMessage(`[DevAgent] ${issues.length} issue(s) found`);
  } catch (e) {
    vscode.window.showErrorMessage(`[DevAgent] Analysis failed: ${e.message}`);
  }
}

async function showTaskHistory() {
  if (taskHistory.length === 0) {
    return vscode.window.showInformationMessage("[DevAgent] No task history");
  }
  const items = taskHistory.map(t => ({
    label: `${t.status === "COMPLETED" || t.status === "FINISHED" ? "$(check)" : "$(error)"} ${t.displayName}`,
    description: `${t.status} | ${t.timestamp}`,
    detail: `Errors: ${t.errors?.length || 0} | Interactive: ${t.interactive ? "yes" : "no"}`,
    task: t,
  }));
  const picked = await vscode.window.showQuickPick(items, { placeHolder: "Task history" });
  if (picked?.task) {
    outputChannel.appendLine(`\n── ${picked.task.task_id} ──`);
    outputChannel.appendLine(JSON.stringify(picked.task, null, 2));
    outputChannel.show();
  }
}

async function openOutputDir() {
  try {
    await vscode.commands.executeCommand("revealFileInOS", vscode.Uri.file(getOutputDir()));
  } catch {
    vscode.window.showErrorMessage(`[DevAgent] Output dir not found: ${getOutputDir()}`);
  }
}

function clearDiagnostics() {
  diagnosticCollection.clear();
  vscode.window.showInformationMessage("[DevAgent] Diagnostics cleared");
}

// ============================================================================
// LSP (unchanged)
// ============================================================================

let lspClient = null;

function resolvePythonForLsp(context) {
  const path = require("path");
  const fs = require("fs");
  const extRoot = context.extensionPath;
  const projectRoot = path.dirname(extRoot);
  const candidates = [
    path.join(projectRoot, "venv", "bin", "python3"),
    path.join(projectRoot, "venv", "bin", "python"),
    "python3", "python",
  ];
  for (const c of candidates) {
    try { if (fs.existsSync(c)) return c; } catch (_) {}
  }
  return "python3";
}

async function startLspClient(context) {
  const lspMode = getConfig().get("lspMode", "stdio");
  outputChannel.appendLine(`[DevAgent] Starting LSP (${lspMode})...`);
  try {
    const { LanguageClient } = require("vscode-languageclient/node");
    const python = resolvePythonForLsp(context);
    const serverOpts = {
      command: python,
      args: ["-m", "devagent.lsp.server"],
      options: { cwd: require("path").dirname(context.extensionPath) },
    };
    const clientOpts = {
      documentSelector: [{ scheme: "file", language: "python" }],
      outputChannel: outputChannel,
    };
    lspClient = new LanguageClient("devagent-lsp", "DevAgent LSP", serverOpts, clientOpts);
    context.subscriptions.push(lspClient);
    await lspClient.start();
    outputChannel.appendLine("[DevAgent] LSP started");
  } catch (e) {
    outputChannel.appendLine(`[DevAgent] LSP failed: ${e.message}`);
  }
}

async function stopLspClient() {
  if (lspClient) { await lspClient.stop(); lspClient = null; }
}

// ============================================================================
// Tree view
// ============================================================================

class DevAgentTaskProvider {
  constructor() {
    this._onDidChangeTreeData = new vscode.EventEmitter();
    this.onDidChangeTreeData = this._onDidChangeTreeData.event;
  }
  refresh() { this._onDidChangeTreeData.fire(); }
  getTreeItem(el) { return el; }
  getChildren() {
    if (taskHistory.length === 0) {
      return [new vscode.TreeItem("$(hubot) No tasks — run a DevAgent command", vscode.TreeItemCollapsibleState.None)];
    }
    return taskHistory.slice(0, 10).map(t => {
      const icon = (t.status === "COMPLETED" || t.status === "FINISHED") ? "$(check)" : "$(error)";
      const item = new vscode.TreeItem(`${icon} ${t.displayName}`, vscode.TreeItemCollapsibleState.None);
      item.description = `${t.status} | ${t.timestamp}`;
      item.tooltip = `Task: ${t.task_id}\nStatus: ${t.status}\nInteractive: ${t.interactive}`;
      return item;
    });
  }
}

function deactivate() {
  disconnectWebSocket();
  if (apiProcess) { apiProcess.kill(); apiProcess = null; }
  if (lspClient) { lspClient.stop(); lspClient = null; }
}

module.exports = { activate, deactivate };
