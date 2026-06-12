"""DevAgent Language Server Protocol (LSP) server implementation.

Provides IDE-native language intelligence via LSP:
- Diagnostics (errors, warnings) from DevAgent LLM analysis
- Code actions (auto-fix suggestions)
- Hover documentation
- Completion suggestions
- Document symbols

Usage:
    python -m devagent.lsp.server --tcp --port 2087
    python -m devagent.lsp.server  (for IDE integration via stdio transport)
"""

import os
import sys
import json
import logging
from typing import Optional

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("devagent-lsp")

try:
    from pygls.lsp.server import LanguageServer
    from lsprotocol import types as lsp_types
    HAS_PYGLS = True
except ImportError:
    HAS_PYGLS = False
    LanguageServer = object
    lsp_types = None


CODE_ANALYSIS_PROMPT = """You are a senior code reviewer embedded in an IDE. Analyze the given code and report only real issues.

For each issue, specify:
- severity: "error" (definite bug, crash, type error) or "warning" (code smell, potential issue)
- line: line number (1-based)
- message: concise description of the problem
- suggestion: how to fix it (one sentence)

Rules:
- ONLY report real, actionable issues. Do NOT report style preferences or minor nitpicks.
- Focus on: logic errors, undefined variables, type mismatches, null/None issues, resource leaks, infinite loops, security issues.
- If the code is clean, return an empty issues array.
- Be accurate with line numbers.
- Do NOT invent issues.

Respond in JSON format:
{
  "issues": [
    {"severity": "error", "line": 5, "message": "...", "suggestion": "..."},
    {"severity": "warning", "line": 12, "message": "...", "suggestion": "..."}
  ],
  "summary": "Brief overall assessment (one sentence)"
}"""


class DevAgentLspServer(LanguageServer if HAS_PYGLS else object):
    """Language Server that provides DevAgent-powered code intelligence."""

    def __init__(self):
        if not HAS_PYGLS:
            super().__init__()
            return
        super().__init__("devagent-lsp", "1.0.0")
        self._llm = None
        self._open_docs: dict[str, str] = {}

    def _get_llm(self):
        """Lazy-load the LLM client from DevAgent config."""
        if self._llm is not None:
            return self._llm
        try:
            from ..agent_core.config_loader import load_config, get_llm_config
            from ..agent_core.llm_client import LLMClient
            config = load_config()
            llm_config = get_llm_config(config)
            self._llm = LLMClient(llm_config)
        except Exception as e:
            logger.warning(f"Failed to initialize LLM client: {e}")
            self._llm = False  # Mark as failed, don't retry
        return self._llm

    def analyze_and_publish(self, doc_uri: str):
        """Analyze document and publish diagnostics. Safe to call from any handler."""
        doc_text = self._open_docs.get(doc_uri, "")
        if not doc_text:
            return

        logger.warning(f"DevAgent-LSP: analyzing {doc_uri}...")
        self.analyze_document(doc_uri, doc_text)

    def analyze_via_llm(self, doc_uri: str, doc_text: str) -> list:
        """Analyze code using DevAgent LLM and return LSP Diagnostics."""
        if not HAS_PYGLS:
            return []

        llm = self._get_llm()
        if not llm:
            return []

        # Truncate very long files
        max_chars = 8000
        code = doc_text[:max_chars]
        if len(doc_text) > max_chars:
            code += "\n\n# ... [file truncated]"

        try:
            result = llm.chat_structured(
                messages=[{"role": "user", "content": f"Analyze this code for bugs and issues:\n\n```\n{code}\n```"}],
                system_prompt=CODE_ANALYSIS_PROMPT
            )

            issues = result.get("issues", [])
            logger.warning(f"DevAgent-LSP: LLM returned {len(issues)} issues, keys={list(result.keys())}")
            if issues:
                logger.warning(f"DevAgent-LSP: first issue: {json.dumps(issues[0], ensure_ascii=False)}")
            diagnostics = []
            for issue in issues:
                level = issue.get("severity", "").lower()
                if level in ("error", "critical"):
                    severity = lsp_types.DiagnosticSeverity.Error
                elif level == "warning":
                    severity = lsp_types.DiagnosticSeverity.Warning
                else:
                    severity = lsp_types.DiagnosticSeverity.Information

                line = max(0, (issue.get("line") or 1) - 1)
                message = issue.get("message", "Unknown issue")
                suggestion = issue.get("suggestion", "")

                diagnostic = lsp_types.Diagnostic(
                    range=lsp_types.Range(
                        start=lsp_types.Position(line=line, character=0),
                        end=lsp_types.Position(line=line, character=len(doc_text.split("\n")[line]) if line < len(doc_text.split("\n")) else 1),
                    ),
                    message=message + (" — Fix: " + suggestion if suggestion else ""),
                    severity=severity,
                    source="devagent-llm",
                    code="DA001",
                )
                diagnostics.append(diagnostic)

            return diagnostics

        except Exception as e:
            logger.warning(f"LLM chat failed: {e}")
            return []

    def analyze_document(self, doc_uri: str, doc_text: str) -> list:
        """Analyze a document — delegates to LLM analysis. Thread-safe entry point."""
        diagnostics = self.analyze_via_llm(doc_uri, doc_text)
        if HAS_PYGLS:
            try:
                from lsprotocol.types import PublishDiagnosticsParams
                self.text_document_publish_diagnostics(
                    PublishDiagnosticsParams(uri=doc_uri, diagnostics=diagnostics)
                )
            except Exception as e:
                logger.warning(f"Failed to publish diagnostics for {doc_uri}: {e}")
        return diagnostics

    def get_completions(self, doc_text: str, line: int, col: int) -> list:
        """Provide code completion suggestions."""
        if not HAS_PYGLS:
            return []

        items = []
        lines = doc_text.split("\n")
        current_line = lines[line].strip() if line < len(lines) else ""

        # Python snippet completions
        completions_map = {
            "def ": lsp_types.CompletionItem(
                label="def function_name():",
                kind=lsp_types.CompletionItemKind.Snippet,
                detail="Define a function",
                insert_text="def ${1:function_name}(${2:args}):\n    ${3:pass}",
            ),
            "class ": lsp_types.CompletionItem(
                label="class ClassName:",
                kind=lsp_types.CompletionItemKind.Snippet,
                detail="Define a class",
                insert_text="class ${1:ClassName}:\n    def __init__(self):\n        ${2:pass}",
            ),
            "if __name__": lsp_types.CompletionItem(
                label="if __name__ == '__main__':",
                kind=lsp_types.CompletionItemKind.Snippet,
                detail="Main guard",
                insert_text="if __name__ == '__main__':\n    ${1:main()}",
            ),
            "try": lsp_types.CompletionItem(
                label="try/except",
                kind=lsp_types.CompletionItemKind.Snippet,
                detail="Try-except block",
                insert_text="try:\n    ${1:pass}\nexcept ${2:Exception} as e:\n    ${3:pass}",
            ),
        }
        for prefix, item in completions_map.items():
            if prefix in current_line:
                items.append(item)

        return items


if HAS_PYGLS:

    server = DevAgentLspServer()

    @server.feature(lsp_types.TEXT_DOCUMENT_DID_OPEN)
    def did_open(ls, params: lsp_types.DidOpenTextDocumentParams):
        """Handle document open — run DevAgent LLM analysis."""
        doc_uri = params.text_document.uri
        doc_text = params.text_document.text
        ls._open_docs[doc_uri] = doc_text
        ls.analyze_and_publish(doc_uri)

    @server.feature(lsp_types.TEXT_DOCUMENT_DID_CHANGE)
    def did_change(ls, params: lsp_types.DidChangeTextDocumentParams):
        """Handle document change — update stored text only (no analysis on every keystroke)."""
        doc_uri = params.text_document.uri
        if params.content_changes:
            ls._open_docs[doc_uri] = params.content_changes[-1].text

    @server.feature(lsp_types.TEXT_DOCUMENT_DID_SAVE)
    def did_save(ls, params: lsp_types.DidSaveTextDocumentParams):
        """Handle document save — trigger DevAgent analysis."""
        doc_uri = params.text_document.uri
        doc_text = ls._open_docs.get(doc_uri, "")
        if params.text:
            doc_text = params.text
            ls._open_docs[doc_uri] = doc_text
        ls.analyze_and_publish(doc_uri)

    @server.feature(lsp_types.TEXT_DOCUMENT_DID_CLOSE)
    def did_close(ls, params: lsp_types.DidCloseTextDocumentParams):
        """Handle document close — clean up."""
        doc_uri = params.text_document.uri
        ls._open_docs.pop(doc_uri, None)

    @server.feature(lsp_types.TEXT_DOCUMENT_COMPLETION)
    def completions(ls, params: lsp_types.CompletionParams):
        """Provide code completions."""
        doc_uri = params.text_document.uri
        doc_text = ls._open_docs.get(doc_uri, "")
        items = ls.get_completions(doc_text, params.position.line, params.position.character)
        return lsp_types.CompletionList(is_incomplete=False, items=items) if items else None

    @server.feature(lsp_types.TEXT_DOCUMENT_HOVER)
    def hover(ls, params: lsp_types.HoverParams):
        """Provide hover documentation."""
        doc_uri = params.text_document.uri
        doc_text = ls._open_docs.get(doc_uri, "")
        lines = doc_text.split("\n")
        line, col = params.position.line, params.position.character

        if line >= len(lines):
            return None

        current_line = lines[line]
        word = ""
        if col < len(current_line):
            start = col
            while start > 0 and (current_line[start - 1].isalnum() or current_line[start - 1] == "_"):
                start -= 1
            end = col
            while end < len(current_line) and (current_line[end].isalnum() or current_line[end] == "_"):
                end += 1
            word = current_line[start:end]

        if word:
            return lsp_types.Hover(
                contents=lsp_types.MarkupContent(
                    kind=lsp_types.MarkupKind.Markdown,
                    value=f"**{word}**\n\n*Analyzed by DevAgent*",
                )
            )
        return None

    @server.feature(lsp_types.TEXT_DOCUMENT_CODE_ACTION)
    def code_actions(ls, params: lsp_types.CodeActionParams):
        """Provide code actions for diagnostics."""
        actions = []
        for diagnostic in params.context.diagnostics:
            if diagnostic.source == "devagent-llm":
                # Extract suggestion from diagnostic message
                msg = diagnostic.message
                fix_suggestion = msg.split(" — Fix: ")[-1] if " — Fix: " in msg else ""
                actions.append(
                    lsp_types.CodeAction(
                        title=f"DevAgent: {fix_suggestion[:60]}" if fix_suggestion else "DevAgent: Review issue",
                        kind=lsp_types.CodeActionKind.QuickFix,
                        diagnostics=[diagnostic],
                    )
                )
        return actions if actions else None

    @server.feature(lsp_types.TEXT_DOCUMENT_DOCUMENT_SYMBOL)
    def document_symbols(ls, params: lsp_types.DocumentSymbolParams):
        """Provide document symbols for outline/navigation."""
        doc_uri = params.text_document.uri
        doc_text = ls._open_docs.get(doc_uri, "")
        symbols = []

        for i, line in enumerate(doc_text.split("\n")):
            stripped = line.strip()
            if stripped.startswith("def ") or stripped.startswith("async def "):
                name = stripped.replace("def ", "").replace("async def ", "").split("(")[0]
                symbols.append(
                    lsp_types.SymbolInformation(
                        name=name,
                        kind=lsp_types.SymbolKind.Function,
                        location=lsp_types.Location(
                            uri=doc_uri,
                            range=lsp_types.Range(
                                start=lsp_types.Position(line=i, character=0),
                                end=lsp_types.Position(line=i, character=len(line)),
                            ),
                        ),
                    )
                )
            elif stripped.startswith("class "):
                name = stripped.replace("class ", "").split("(")[0].rstrip(":")
                symbols.append(
                    lsp_types.SymbolInformation(
                        name=name,
                        kind=lsp_types.SymbolKind.Class,
                        location=lsp_types.Location(
                            uri=doc_uri,
                            range=lsp_types.Range(
                                start=lsp_types.Position(line=i, character=0),
                                end=lsp_types.Position(line=i, character=len(line)),
                            ),
                        ),
                    )
                )

        return symbols if symbols else None


def run_lsp_tcp(host: str = "127.0.0.1", port: int = 2087):
    """Run LSP server over TCP transport."""
    if not HAS_PYGLS:
        logger.error("pygls is required for LSP server. Install with: pip install devagent[lsp]")
        sys.exit(1)
    print(f"[DevAgent-LSP] Starting LSP TCP server on {host}:{port}")
    server.start_tcp(host, port)


def run_lsp_stdio():
    """Run LSP server over stdio transport (standard for IDE integration)."""
    if not HAS_PYGLS:
        logger.error("pygls is required for LSP server. Install with: pip install devagent[lsp]")
        sys.exit(1)
    print("[DevAgent-LSP] Starting LSP server over stdio", file=sys.stderr)
    server.start_io()


def main():
    """Entry point for the LSP server."""
    import argparse
    parser = argparse.ArgumentParser(description="DevAgent LSP Server")
    parser.add_argument("--tcp", action="store_true", help="Use TCP transport")
    parser.add_argument("--host", default="127.0.0.1", help="TCP host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=2087, help="TCP port (default: 2087)")
    args = parser.parse_args()

    if args.tcp:
        run_lsp_tcp(args.host, args.port)
    else:
        run_lsp_stdio()


if __name__ == "__main__":
    main()
