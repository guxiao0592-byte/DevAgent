"""Phase Review Gate — human-in-the-loop quality review for V2 Agentic Loop.

Architecture:
  ┌─────────────────────────────────────────────────────────────────┐
  │                     PhaseReviewGate                             │
  │                                                                 │
  │  ReviewSession:  lifecycle management of review requests        │
  │  ReviewArtifact: typed artifact (code, test, diff, doc)         │
  │  QualityEvaluator: LLM-powered quality scoring                  │
  │  ReviewFormatter:  human-readable review document               │
  │                                                                 │
  │  Flow:  Agent completes work → calls request_review tool       │
  │         → artifacts collected → quality scored                 │
  │         → review doc formatted → pushed to human               │
  │         → human: approve / revise( +feedback) / reject         │
  │         → feedback injected into agent context                 │
  └─────────────────────────────────────────────────────────────────┘
"""

import os
import re
import time
import enum
import json
import hashlib
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


# ============================================================================
# Data Types
# ============================================================================

class ReviewStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REVISION_REQUESTED = "revision_requested"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"


class ArtifactType(str, enum.Enum):
    CODE = "code"
    TEST = "test"
    DOCUMENT = "document"
    DIFF = "diff"
    REPORT = "report"
    PLAN = "plan"
    MIXED = "mixed"


class QualityLevel(str, enum.Enum):
    """Quality levels aligned with software engineering standards."""
    CRITICAL_FAILURE = "critical_failure"  # Doesn't work / fundamentally wrong
    NEEDS_MAJOR_REWORK = "needs_major_rework"  # Missing key elements
    NEEDS_MINOR_FIXES = "needs_minor_fixes"  # Largely correct, small issues
    MEETS_STANDARD = "meets_standard"  # Acceptable for production
    EXCEEDS_STANDARD = "exceeds_standard"  # Well above minimum


@dataclass
class ReviewArtifact:
    """A single artifact submitted for review."""
    artifact_type: ArtifactType
    description: str                # What this artifact represents
    file_paths: list[str]           # Absolute or relative file paths
    content_preview: str = ""       # Key excerpt (first N lines of main file)
    test_results: Optional[dict] = None  # If tests were run
    diff_summary: str = ""          # Summary of changes made
    metrics: dict = field(default_factory=dict)  # lines changed, coverage, etc.


@dataclass
class ReviewRequest:
    """A complete review request from the agent."""
    id: str
    phase: str                      # "exploration" / "implementation" / "testing" / "fix" / "delivery"
    title: str                      # Short title of what was done
    summary: str                    # Agent's own summary of the work
    artifacts: list[ReviewArtifact]
    quality_self_assessment: str = ""  # Agent's self-assessment
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    status: ReviewStatus = ReviewStatus.PENDING
    quality_score: Optional[QualityLevel] = None
    quality_report: str = ""        # AI quality evaluator's report

    # Human's response
    human_decision: Optional[ReviewStatus] = None
    human_feedback: str = ""
    human_suggestions: list[str] = field(default_factory=list)  # specific actionable items
    responded_at: str = ""


@dataclass
class ReviewFormatResult:
    """The formatted review document to present to a human."""
    markdown: str
    artifact_count: int
    quality_score: QualityLevel
    summary_sections: list[str]


# ============================================================================
# Quality Evaluator — LLM-powered review scoring
# ============================================================================

QUALITY_EVALUATION_PROMPT = """You are a senior software engineering quality reviewer. Evaluate the submitted work against intermediate-level professional software engineering standards.

## Evaluation Criteria

### CODE (source code)
1. **Correctness**: Does the logic solve the problem correctly? Are edge cases handled?
2. **Structure**: Is the code well-organized? Single responsibility? Clean interfaces?
3. **Error Handling**: Are exceptions caught appropriately? Are error messages clear?
4. **Type Safety**: Are type hints complete and correct? No type mismatches?
5. **Documentation**: Are docstrings complete (Google/NumPy style)? Complex logic explained?
6. **Testability**: Is the code structured to be testable? Dependencies injectable?
7. **Readability**: Clear variable names, consistent style, no magic numbers, appropriate comments?

### TESTS
1. **Coverage**: Happy path + error path + edge cases + boundary conditions
2. **Isolation**: Tests independent, use fixtures, no shared state
3. **Assertions**: Specific, meaningful assertions; not just `assert True`
4. **Structure**: Arrange-Act-Assert pattern; clear test names
5. **Performance**: Parametrized where appropriate; no slow I/O in unit tests

### DOCUMENTS / REPORTS
1. **Completeness**: All required sections present; no placeholders
2. **Specificity**: Concrete details, not generic templates; real code references
3. **Consistency**: Internal consistency between sections; no contradictions
4. **Professionalism**: Correct terminology; proper formatting; actionable content
5. **Traceability**: Requirements traceable to design; design traceable to code

## Scoring

For each criterion, assign: PASS / NEEDS_WORK / MISSING

Then determine overall quality level:
- **meets_standard**: ≥80% criteria PASS, no criteria MISSING
- **needs_minor_fixes**: ≥60% criteria PASS, minor issues in specific areas
- **needs_major_rework**: <60% PASS or key sections MISSING
- **critical_failure**: Fundamentally incorrect or unusable

## Output Format

Respond with ONLY valid JSON:
{
  "overall_quality": "meets_standard / needs_minor_fixes / needs_major_rework / critical_failure",
  "criteria_assessment": {
    "code_correctness": "PASS / NEEDS_WORK / MISSING / N/A",
    "code_structure": "...",
    "error_handling": "...",
    "type_safety": "...",
    "documentation": "...",
    "test_coverage": "...",
    "test_isolation": "...",
    "report_completeness": "...",
    "report_specificity": "...",
    "report_professionalism": "..."
  },
  "strengths": ["specific thing done well"],
  "weaknesses": ["specific issue with file:line reference if applicable"],
  "specific_suggestions": [
    "Actionable suggestion 1",
    "Actionable suggestion 2"
  ],
  "overall_assessment": "2-3 paragraph professional assessment summarizing quality"
}
"""


class QualityEvaluator:
    """LLM-powered quality evaluation against intermediate SE standards."""

    def __init__(self, llm_client=None):
        self.llm = llm_client

    def evaluate(self, request: ReviewRequest, workspace: str = ".") -> dict:
        """Evaluate a review request against quality standards.

        Args:
            request: The review request with artifacts
            workspace: Workspace path for reading files

        Returns:
            Quality evaluation dict with score, strengths, weaknesses, suggestions
        """
        # Build evaluation context
        context = self._build_eval_context(request, workspace)

        if self.llm is None:
            return self._fallback_evaluation(context)

        try:
            result = self.llm.chat_structured(
                messages=[{
                    "role": "user",
                    "content": (
                        f"Evaluate this software engineering work against professional standards.\n\n"
                        f"{context}"
                    )
                }],
                system_prompt=QUALITY_EVALUATION_PROMPT
            )
            return result
        except Exception:
            return self._fallback_evaluation(context)

    def _build_eval_context(self, request: ReviewRequest, workspace: str) -> str:
        """Build the evaluation context from artifacts."""
        lines = []
        lines.append(f"## Phase: {request.phase}")
        lines.append(f"## Title: {request.title}")
        lines.append(f"## Agent Summary: {request.summary}")
        lines.append(f"## Agent Self-Assessment: {request.quality_self_assessment or 'Not provided'}")
        lines.append(f"\n## Artifacts ({len(request.artifacts)})")

        for i, art in enumerate(request.artifacts):
            lines.append(f"\n### Artifact {i+1}: {art.description}")
            lines.append(f"Type: {art.artifact_type.value}")
            lines.append(f"Files: {', '.join(art.file_paths)}")

            if art.metrics:
                lines.append(f"Metrics: {json.dumps(art.metrics)}")

            if art.test_results:
                tr = art.test_results
                lines.append(f"Tests: {tr.get('passed',0)} passed, {tr.get('failed',0)} failed, "
                           f"{tr.get('collected',0)} total")

            if art.diff_summary:
                lines.append(f"Changes: {art.diff_summary[:500]}")

            if art.content_preview:
                lines.append(f"\nContent Preview:\n```\n{art.content_preview[:3000]}\n```")

            # Read actual files for code review
            for fpath in art.file_paths:
                full_path = os.path.join(workspace, fpath) if not os.path.isabs(fpath) else fpath
                if os.path.exists(full_path) and os.path.isfile(full_path):
                    try:
                        content = Path(full_path).read_text(encoding="utf-8", errors="replace")
                        if len(content) > 5000:
                            content = content[:5000] + "\n... [truncated]"
                        lines.append(f"\n### File: {fpath}")
                        lines.append(f"```\n{content}\n```")
                    except Exception:
                        pass

        return "\n".join(lines)

    def _fallback_evaluation(self, context: str) -> dict:
        """Simple rule-based evaluation when LLM is unavailable."""
        return {
            "overall_quality": QualityLevel.NEEDS_MINOR_FIXES.value,
            "criteria_assessment": {},
            "strengths": ["Work submitted for review"],
            "weaknesses": ["Automated evaluation unavailable — awaiting human review"],
            "specific_suggestions": ["Please manually review the submitted artifacts"],
            "overall_assessment": "Automated quality evaluation is not available. Human review required."
        }


# ============================================================================
# Review Formatter — generates human-readable review documents
# ============================================================================

class ReviewFormatter:
    """Formats review requests into well-structured, scannable Markdown documents."""

    @staticmethod
    def format(request: ReviewRequest, quality_eval: dict = None) -> ReviewFormatResult:
        """Format a review request for human consumption.

        Returns a ReviewFormatResult with the Markdown document.
        """
        lines = [
            f"# 📋 Phase Review: {request.title}",
            "",
            f"**Task ID**: `{request.id}`  ",
            f"**Phase**: `{request.phase}`  ",
            f"**Status**: `{request.status.value}`  ",
            f"**Submitted**: {request.created_at[:19]}  ",
            "",
            "---",
            "",
            "## 📝 Agent Summary",
            "",
            request.summary,
            "",
        ]

        if request.quality_self_assessment:
            lines.extend([
                "### Agent Self-Assessment",
                "",
                request.quality_self_assessment,
                "",
            ])

        # Quality evaluation section
        if quality_eval:
            level = quality_eval.get("overall_quality", "unknown")
            level_icons = {
                "meets_standard": "✅ Meets Standard",
                "exceeds_standard": "🌟 Exceeds Standard",
                "needs_minor_fixes": "🔧 Needs Minor Fixes",
                "needs_major_rework": "⚠️ Needs Major Rework",
                "critical_failure": "❌ Critical Failure",
            }
            icon = level_icons.get(level, f"❓ {level}")

            lines.extend([
                "---",
                "",
                "## 🤖 Automated Quality Assessment",
                "",
                f"**Overall**: {icon}",
                "",
            ])

            # Criteria grid
            criteria = quality_eval.get("criteria_assessment", {})
            if criteria:
                lines.extend([
                    "| Criterion | Assessment |",
                    "|-----------|------------|",
                ])
                for name, result in criteria.items():
                    result_icon = {"PASS": "✅", "NEEDS_WORK": "🔧", "MISSING": "❌", "N/A": "—"}
                    icon_str = result_icon.get(result, "❓")
                    lines.append(f"| {name.replace('_', ' ').title()} | {icon_str} {result} |")
                lines.append("")

            strengths = quality_eval.get("strengths", [])
            if strengths:
                lines.append("### ✅ Strengths")
                for s in strengths:
                    lines.append(f"- {s}")
                lines.append("")

            weaknesses = quality_eval.get("weaknesses", [])
            if weaknesses:
                lines.append("### ❌ Issues Found")
                for w in weaknesses:
                    lines.append(f"- {w}")
                lines.append("")

            suggestions = quality_eval.get("specific_suggestions", [])
            if suggestions:
                lines.append("### 💡 Specific Suggestions")
                for i, s in enumerate(suggestions, 1):
                    lines.append(f"{i}. {s}")
                lines.append("")

            assessment = quality_eval.get("overall_assessment", "")
            if assessment:
                lines.extend([
                    "### Assessment",
                    "",
                    assessment,
                    "",
                ])

        # Artifacts section
        lines.extend([
            "---",
            "",
            f"## 📦 Submitted Artifacts ({len(request.artifacts)})",
            "",
        ])

        for i, art in enumerate(request.artifacts, 1):
            type_icons = {
                ArtifactType.CODE: "💻",
                ArtifactType.TEST: "🧪",
                ArtifactType.DOCUMENT: "📄",
                ArtifactType.DIFF: "📊",
                ArtifactType.REPORT: "📈",
                ArtifactType.PLAN: "🗺️",
                ArtifactType.MIXED: "📦",
            }
            type_icon = type_icons.get(art.artifact_type, "📌")

            lines.extend([
                f"### {type_icon} {i}. {art.description}",
                "",
                f"**Type**: `{art.artifact_type.value}`",
                "",
                "**Files**:",
            ])
            for fp in art.file_paths:
                lines.append(f"- `{fp}`")

            if art.metrics:
                lines.append(f"\n**Metrics**:")
                for k, v in art.metrics.items():
                    lines.append(f"- {k}: {v}")

            if art.test_results:
                tr = art.test_results
                passed = tr.get("passed", 0)
                failed = tr.get("failed", 0)
                collected = tr.get("collected", 0)
                success_rate = (passed / max(collected, 1)) * 100
                lines.append(f"\n**Tests**: {passed}/{collected} passed ({success_rate:.0f}%), "
                           f"{failed} failed")

            if art.diff_summary:
                lines.append(f"\n**Changes**:\n```diff\n{art.diff_summary[:2000]}\n```")

            if art.content_preview:
                lines.append(f"\n**Preview**:\n```\n{art.content_preview[:2000]}\n```")

            lines.append("")

        # Human action section
        lines.extend([
            "---",
            "",
            "## 👤 Human Review Required",
            "",
            "Please review the above work and choose one:",
            "",
            "| Action | Description |",
            "|--------|-------------|",
            "| **✅ Approve** | Work meets standards, proceed to next phase |",
            "| **🔧 Request Changes** | Work is on the right track but needs specific fixes (provide details below) |",
            "| **❌ Reject** | Work is fundamentally wrong or needs complete redo (explain why) |",
            "",
            "### Your Feedback",
            "",
            "> _Provide specific, actionable feedback. Reference file paths and line numbers when possible._",
            "",
            "```",
            "[Your feedback here — what needs to change and why]",
            "```",
            "",
        ])

        md_content = "\n".join(lines)

        return ReviewFormatResult(
            markdown=md_content,
            artifact_count=len(request.artifacts),
            quality_score=QualityLevel(quality_eval.get("overall_quality", "needs_minor_fixes"))
                          if quality_eval else QualityLevel.NEEDS_MINOR_FIXES,
            summary_sections=[request.title, request.summary[:100]],
        )


# ============================================================================
# Review Session — manages review lifecycle
# ============================================================================

class ReviewSession:
    """Manages the lifecycle of a single review request."""

    def __init__(self, request: ReviewRequest, evaluator: QualityEvaluator = None,
                 timeout_seconds: int = 600):
        self.request = request
        self.evaluator = evaluator
        self.timeout_seconds = timeout_seconds
        self._resolution_future: Optional[asyncio.Future] = None
        self._formatted: Optional[ReviewFormatResult] = None

    @property
    def is_resolved(self) -> bool:
        return self.request.status not in (ReviewStatus.PENDING,)

    async def run_evaluation(self, workspace: str = ".") -> ReviewFormatResult:
        """Run quality evaluation and format the review document."""
        quality = {}
        if self.evaluator:
            quality = self.evaluator.evaluate(self.request, workspace)
            self.request.quality_score = QualityLevel(
                quality.get("overall_quality", "needs_minor_fixes")
            )
            self.request.quality_report = quality.get("overall_assessment", "")

        self._formatted = ReviewFormatter.format(self.request, quality)
        return self._formatted

    def get_formatted(self) -> Optional[ReviewFormatResult]:
        return self._formatted

    async def wait_for_decision(self) -> dict:
        """Block until the human makes a decision or timeout.

        If already resolved (e.g. by terminal channel), returns immediately.

        Returns:
            {"decision": "approve"|"revise"|"reject", "feedback": "...", "suggestions": [...]}
        """
        # Already resolved? Return immediately
        if self.is_resolved:
            decision_map = {
                ReviewStatus.APPROVED: "approve",
                ReviewStatus.REVISION_REQUESTED: "revise",
                ReviewStatus.REJECTED: "reject",
                ReviewStatus.TIMED_OUT: "approve",
            }
            return {
                "decision": decision_map.get(self.request.status, "approve"),
                "feedback": self.request.human_feedback or "",
                "suggestions": self.request.human_suggestions or [],
            }

        self._resolution_future = asyncio.Future()
        try:
            result = await asyncio.wait_for(
                self._resolution_future,
                timeout=self.timeout_seconds
            )
            return result
        except asyncio.TimeoutError:
            self.request.status = ReviewStatus.TIMED_OUT
            return {
                "decision": "approve",
                "feedback": "Auto-approved due to review timeout",
                "suggestions": [],
            }

    def resolve(self, decision: str, feedback: str = "",
                suggestions: list[str] = None):
        """Resolve the review with a human decision.

        Args:
            decision: "approve", "revise", or "reject"
            feedback: Human-readable feedback
            suggestions: Specific actionable suggestions
        """
        if decision == "approve":
            self.request.status = ReviewStatus.APPROVED
            self.request.human_decision = ReviewStatus.APPROVED
        elif decision == "reject":
            self.request.status = ReviewStatus.REJECTED
            self.request.human_decision = ReviewStatus.REJECTED
        else:  # revise
            self.request.status = ReviewStatus.REVISION_REQUESTED
            self.request.human_decision = ReviewStatus.REVISION_REQUESTED

        self.request.human_feedback = feedback
        self.request.human_suggestions = suggestions or []
        self.request.responded_at = datetime.now().isoformat()

        if self._resolution_future and not self._resolution_future.done():
            self._resolution_future.set_result({
                "decision": decision,
                "feedback": feedback,
                "suggestions": self.request.human_suggestions,
            })

    def build_revision_context(self) -> str:
        """Build the context to inject back to the agent for revision.

        Returns a string the agent can use to understand what to fix.
        Only meaningful when human_decision is REVISION_REQUESTED or REJECTED.
        """
        if self.request.human_decision not in (ReviewStatus.REVISION_REQUESTED, ReviewStatus.REJECTED):
            return ""

        feedback = self.request.human_feedback
        suggestions = self.request.human_suggestions
        weaknesses = []

        if self.request.quality_report:
            weaknesses.append(f"Quality assessment noted: {self.request.quality_report[:500]}")

        parts = [
            "## HUMAN REVIEW: Changes Requested",
            "",
            f"**Decision**: {self.request.human_decision.value if self.request.human_decision else 'revise'}",
            "",
            "### Reviewer Feedback",
            feedback,
        ]

        if suggestions:
            parts.append("\n### Specific Changes Required")
            for s in suggestions:
                parts.append(f"- [ ] {s}")

        if weaknesses:
            parts.append("\n### Quality Issues to Address")
            for w in weaknesses:
                parts.append(f"- {w}")

        parts.append(
            "\n\n**ACTION**: Address ALL the feedback above. "
            "Make focused, specific improvements. Then call request_review again "
            "to submit the revised work for re-evaluation."
        )

        return "\n".join(parts)


# ============================================================================
# PhaseReviewGate — main orchestrator
# ============================================================================

class PhaseReviewGate:
    """Orchestrates human review at development phase boundaries.

    Usage in DevAgentCore:
        gate = PhaseReviewGate(llm_client, streaming_server, session_manager)
        review_session = await gate.submit_for_review(
            phase="implementation",
            title="User authentication module",
            summary="Implemented login, registration, and JWT session management",
            artifacts=[...],
            workspace="./project"
        )
        decision = await review_session.wait_for_decision()
        if decision["decision"] == "approve":
            # proceed to next phase
        elif decision["decision"] == "revise":
            # inject feedback and redo
    """

    def __init__(self, llm_client=None, streaming_server=None,
                 session_manager=None, terminal_channel=None):
        self.llm = llm_client
        self.streaming = streaming_server
        self.session_mgr = session_manager
        self.evaluator = QualityEvaluator(llm_client)
        self.formatter = ReviewFormatter()
        self.terminal = terminal_channel
        self._thread_channel = None  # Set by API endpoint for bg thread mode

        # Active review sessions
        self._active_session: Optional[ReviewSession] = None
        self._review_history: list[ReviewRequest] = []

    @property
    def has_active_review(self) -> bool:
        return self._active_session is not None and not self._active_session.is_resolved

    def get_active_session(self) -> Optional[ReviewSession]:
        return self._active_session

    async def submit_for_review(self, phase: str, title: str, summary: str,
                                 artifacts: list[ReviewArtifact],
                                 task_id: str = "",
                                 workspace: str = ".",
                                 quality_self_assessment: str = "",
                                 timeout_seconds: int = 600) -> ReviewSession:
        """Submit work for human review. Blocks until evaluation completes.

        Args:
            phase: "exploration" / "implementation" / "testing" / "fix" / "delivery"
            title: Short title of what was done
            summary: Agent's own summary of work
            artifacts: List of ReviewArtifacts
            task_id: Task identifier
            workspace: Workspace path
            quality_self_assessment: Agent's self-assessment
            timeout_seconds: Max wait time for human response

        Returns:
            ReviewSession with formatted review doc and decision
        """
        request = ReviewRequest(
            id=f"rev_{task_id}_{int(time.time())}",
            phase=phase,
            title=title,
            summary=summary,
            artifacts=artifacts,
            quality_self_assessment=quality_self_assessment,
        )

        session = ReviewSession(request, self.evaluator, timeout_seconds)
        self._active_session = session

        # Run quality evaluation
        formatted = await session.run_evaluation(workspace)

        # PRIORITY 1: Thread channel (background agent)
        if self._thread_channel:
            quality = request.quality_score.value if request.quality_score else "unknown"
            req = self._thread_channel.create_review(
                phase=phase, title=title, summary=summary,
                quality_score=quality, timeout=timeout_seconds,
            )
            result = req.wait(timeout_seconds=timeout_seconds + 5)
            self._thread_channel._pending.pop(req.id, None)
            session.resolve(
                result.get("decision", "approve"),
                result.get("feedback", ""),
                result.get("suggestions", []),
            )
            return session

        # Check if WS clients are connected
        has_clients = (self.session_mgr and
                       self.session_mgr.get_connected_count(task_id) > 0)

        if has_clients:
            # Push review document to streaming channel
            if self.streaming:
                await self.streaming.push_event({
                    "type": "review.requested",
                    "task_id": task_id,
                    "data": {
                        "review_id": request.id,
                        "phase": phase,
                        "title": title,
                        "summary": summary,
                        "quality_score": request.quality_score.value if request.quality_score else "unknown",
                        "markdown": formatted.markdown,
                        "artifact_count": formatted.artifact_count,
                        "status": "pending",
                    },
                    "timestamp": datetime.now().isoformat(),
                })

            # Broadcast to session clients
            if self.session_mgr:
                await self.session_mgr.broadcast(task_id, {
                    "type": "review.requested",
                    "data": {
                        "review_id": request.id,
                        "phase": phase,
                        "title": title,
                        "quality_score": request.quality_score.value if request.quality_score else "unknown",
                        "markdown": formatted.markdown[:5000],
                        "markdown_full_length": len(formatted.markdown),
                    },
                })

        elif self.terminal and self.terminal.available:
            # Terminal-based review
            quality = request.quality_score.value if request.quality_score else "unknown"
            quality_icons = {
                "meets_standard": "✅", "needs_minor_fixes": "🔧",
                "needs_major_rework": "⚠️", "critical_failure": "❌",
            }
            icon = quality_icons.get(quality, "❓")

            print(f"\n  Quality: {icon} {quality}")
            print(f"  Summary: {summary[:300]}")
            print(f"  Artifacts: {len(artifacts)}")

            decision = await self.terminal.prompt_review(
                request.id, phase, title, quality, timeout_seconds
            )
            session.resolve(
                decision["decision"],
                decision.get("feedback", ""),
                decision.get("suggestions", []),
            )

        else:
            # No interaction channel — auto-approve immediately (non-blocking)
            session.resolve("approve", "Auto-approved (no interaction channel available)")

        return session

    def resolve_review(self, review_id: str, decision: str,
                       feedback: str = "", suggestions: list[str] = None):
        """Resolve an active review with human decision.

        Args:
            review_id: The review ID
            decision: "approve", "revise", or "reject"
            feedback: Human feedback text
            suggestions: Specific actionable items
        """
        if not self._active_session:
            return False

        if self._active_session.request.id != review_id:
            return False

        self._active_session.resolve(decision, feedback, suggestions)
        self._review_history.append(self._active_session.request)

        # Push resolution event
        event_data = {
            "review_id": review_id,
            "decision": decision,
            "feedback": feedback[:500],
        }

        if self.streaming:
            import asyncio as _asyncio
            try:
                loop = _asyncio.get_event_loop()
                if loop.is_running():
                    _asyncio.ensure_future(
                        self.streaming.push_event({
                            "type": "review.resolved",
                            "data": event_data,
                            "timestamp": datetime.now().isoformat(),
                        })
                    )
            except RuntimeError:
                pass

        return True

    def get_revision_context(self) -> str:
        """Get the revision context for the active session."""
        if not self._active_session:
            return ""
        return self._active_session.build_revision_context()

    def get_review_history(self) -> list[dict]:
        """Get review history summary."""
        return [
            {
                "id": r.id,
                "phase": r.phase,
                "title": r.title,
                "status": r.status.value,
                "quality": r.quality_score.value if r.quality_score else "unknown",
                "created": r.created_at[:19],
                "responded": r.responded_at[:19] if r.responded_at else "",
                "decision": r.human_decision.value if r.human_decision else "",
            }
            for r in self._review_history
        ]

    def clear_active(self):
        """Clear the active session after resolution."""
        if self._active_session and self._active_session.is_resolved:
            self._active_session = None


# ============================================================================
# Artifact builders — convenience helpers
# ============================================================================

class ArtifactBuilder:
    """Build ReviewArtifact instances from common patterns."""

    @staticmethod
    def from_code_files(files: list[str], description: str = "",
                        workspace: str = ".") -> ReviewArtifact:
        """Build from a list of code file paths."""
        metrics = {"file_count": len(files)}
        preview = ""
        for f in files[:2]:
            fpath = os.path.join(workspace, f) if not os.path.isabs(f) else f
            try:
                content = Path(fpath).read_text(encoding="utf-8", errors="replace")
                metrics[f"lines_{os.path.basename(f)}"] = len(content.split("\n"))
                if not preview and content:
                    preview = content[:1500]
            except Exception:
                pass
        return ReviewArtifact(
            artifact_type=ArtifactType.CODE,
            description=description or f"Source code: {len(files)} file(s)",
            file_paths=files,
            content_preview=preview,
            metrics=metrics,
        )

    @staticmethod
    def from_test_results(test_files: list[str], test_results: dict,
                          description: str = "") -> ReviewArtifact:
        """Build from test files and their execution results."""
        return ReviewArtifact(
            artifact_type=ArtifactType.TEST,
            description=description or f"Test suite: {len(test_files)} file(s)",
            file_paths=test_files,
            test_results=test_results,
            metrics={
                "test_files": len(test_files),
                "passed": test_results.get("passed", 0),
                "failed": test_results.get("failed", 0),
                "collected": test_results.get("collected", 0),
            },
        )

    @staticmethod
    def from_diff(modified_files: list[str], diff_text: str,
                  description: str = "") -> ReviewArtifact:
        """Build from a git diff."""
        return ReviewArtifact(
            artifact_type=ArtifactType.DIFF,
            description=description or f"Changes: {len(modified_files)} file(s)",
            file_paths=modified_files,
            diff_summary=diff_text[:3000],
            metrics={"files_changed": len(modified_files),
                     "diff_lines": len(diff_text.split("\n"))},
        )

    @staticmethod
    def from_plan(plan_data: dict, description: str = "") -> ReviewArtifact:
        """Build from a plan/work-breakdown."""
        sub_tasks = plan_data.get("sub_tasks", [])
        return ReviewArtifact(
            artifact_type=ArtifactType.PLAN,
            description=description or f"Execution plan: {len(sub_tasks)} sub-tasks",
            file_paths=[],
            content_preview=json.dumps(plan_data, indent=2, ensure_ascii=False)[:3000],
            metrics={
                "sub_tasks_count": len(sub_tasks),
                "estimated_iterations": plan_data.get("estimated_total_iterations", 0),
            },
        )
