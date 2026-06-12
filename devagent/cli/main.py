"""Command-line interface for DevAgent — Unified Architecture.

All task modes (design/implement/repair/full) are routed through the V2
ReAct loop with full pipeline tool support. The unified architecture uses
a single execution engine for all workflows.
"""

import argparse
import sys
import os

# ============================================================================
# Mode descriptors — map legacy task names to agent instructions
# ============================================================================

MODE_INSTRUCTIONS = {
    "design": "Analyze the project in the input file. Call analyze_requirements then design_architecture. Submit when done.",
    "implement": "Build the project described in the input file. Call analyze_requirements, design_architecture, generate_code, then test_run. If tests pass, submit.",
    "repair": "Fix bugs in the workspace. Use test_run to reproduce, debug_issue to analyze, repair_code to fix. Submit when tests pass.",
    "full": "Build the complete project from the input file: analyze_requirements → design_architecture → generate_code → test_run → (debug_issue+repair_code if needed) → submit.",
    "agentic": "Complete the task using available tools. Choose the best approach.",
    "test": "Generate and run tests for the code in the workspace. Use generate_tests or test_run.",
    "debug": "Debug failures in the codebase. Use test_run, debug_issue, then repair_code.",
}


def create_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser — unified architecture."""
    parser = argparse.ArgumentParser(
        prog="agent",
        description="DevAgent: Unified LLM-based Software Engineering Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full end-to-end development
  %(prog)s --mode full --input requirements.md --output outputs/project

  # Analyze and design only
  %(prog)s --mode design --input requirements.md --output outputs/design

  # Fix bugs in existing code
  %(prog)s --mode repair --workspace ./buggy_app/ --output outputs/repair

  # Interactive mode (human-in-the-loop)
  %(prog)s --input requirements.md --interactive full

  # Start API server (WebSocket + REST)
  devagent-api --interactive full
        """
    )

    parser.add_argument(
        "--mode", "-m",
        choices=["design", "implement", "test", "debug", "repair", "full", "agentic"],
        default="agentic",
        help=(
            "Task mode (default: agentic). All modes use the unified ReAct engine.\n"
            "  design    — Requirement analysis + architecture design\n"
            "  implement — Code generation + testing\n"
            "  repair    — Debug + fix (bug fixing)\n"
            "  full      — Complete end-to-end pipeline\n"
            "  agentic   — Autonomous determination of approach\n"
            "  test      — Test generation + execution\n"
            "  debug     — Debug + root cause analysis"
        )
    )

    parser.add_argument(
        "--input", "-i", default="",
        help="Input file path (requirements or design document)"
    )

    parser.add_argument(
        "--workspace", "-w", default=".",
        help="Workspace directory (default: current directory)"
    )

    parser.add_argument(
        "--output", "-o", default="./outputs",
        help="Output directory for artifacts (default: ./outputs)"
    )

    parser.add_argument(
        "--max-iterations", type=int, default=50,
        help="Maximum ReAct loop iterations (default: 50)"
    )

    parser.add_argument(
        "--config", default="",
        help="Path to custom config YAML file"
    )

    parser.add_argument(
        "--provider", choices=["openai", "deepseek"], default="",
        help="Override LLM provider"
    )

    parser.add_argument(
        "--interactive", "-I",
        choices=["full", "approval", "observe", "off"],
        default="off",
        help=(
            "Real-time interactive mode:\n"
            "  full     — Approval gates + agent dialogue + streaming\n"
            "  approval — Approval gates only (dangerous op blocking)\n"
            "  observe  — Streaming observation (read-only)\n"
            "  off      — No interaction (default)"
        )
    )

    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Verbose output"
    )

    parser.add_argument(
        "--version", "-V", action="store_true",
        help="Show version"
    )

    return parser


def main():
    """Main entry point — unified architecture."""
    if "--version" in sys.argv or "-V" in sys.argv:
        from .. import __version__
        print(f"DevAgent v{__version__}")
        sys.exit(0)

    parser = create_parser()
    args = parser.parse_args()

    from ..agentic.core import DevAgentCore

    mode = args.mode or "agentic"

    # Validate — if mode needs input, input must exist
    if mode in ("design", "implement", "full") and not args.input:
        print(f"[ERROR] --mode {mode} requires --input <file>", file=sys.stderr)
        print(f"  Example: agent --mode {mode} --input requirements.md", file=sys.stderr)
        sys.exit(1)

    if args.input and not os.path.exists(args.input):
        print(f"[ERROR] Input file not found: {args.input}", file=sys.stderr)
        print(f"  Create one first:", file=sys.stderr)
        print(f'    echo "# Your Project" > {args.input}', file=sys.stderr)
        sys.exit(1)

    # Build task description
    task_parts = []

    # Add mode instruction
    instruction = MODE_INSTRUCTIONS.get(mode, MODE_INSTRUCTIONS["agentic"])
    task_parts.append(instruction)

    # Read input file
    if args.input:
        input_content = open(args.input, "r").read()
        task_parts.append(f"\n\n## Input Document\n\n{input_content}")

    # Workspace context
    workspace = os.path.abspath(args.workspace)
    task_parts.append(f"\n\n## Workspace\nWorkspace directory: {workspace}")

    task_description = "\n".join(task_parts)

    output_root = os.path.abspath(args.output)
    os.makedirs(output_root, exist_ok=True)

    if args.verbose:
        print(f"[DevAgent] Mode: {mode}")
        print(f"[DevAgent] Workspace: {workspace}")
        print(f"[DevAgent] Output: {output_root}")
        print(f"[DevAgent] Task: {task_description[:300]}...")
        if args.interactive != "off":
            print(f"[DevAgent] Interactive: {args.interactive}")
        print()

    if args.interactive != "off" and sys.stdout.isatty():
        print(f"{'='*60}")
        print(f"DevAgent Unified — Mode: {mode}")
        print(f"{'='*60}")
        print(f"Controls:")
        if args.interactive == "full":
            print(f"  - Review & approve at each phase milestone")
            print(f"  - Agent will ask questions when unclear")
        print(f"  - Ctrl+C to abort")
        print(f"  - For full WS control: devagent-api --interactive {args.interactive}")
        print(f"{'='*60}\n")

    # Create and configure core
    config_path = args.config if args.config else None
    core = DevAgentCore(config_path=config_path)

    if args.interactive != "off":
        core.enable_interaction(mode=args.interactive)

    if args.provider:
        core.config["model"]["provider"] = args.provider
        from ..agent_core.llm_client import LLMClient
        core.llm = LLMClient(core.config.get("model", {}))

    # Execute — use PipelineRunner for structured modes
    use_pipeline = mode in ("full", "design", "implement")
    if use_pipeline:
        try:
            pstate = core.run_pipeline(
                task_description=task_description,
                workspace=workspace,
                output_root=output_root,
            )
            _print_pipeline_summary(pstate, core)
        except KeyboardInterrupt:
            print("\n[DevAgent] Interrupted", file=sys.stderr)
            sys.exit(130)
        except Exception as e:
            print(f"[DevAgent] Fatal error: {e}", file=sys.stderr)
            if args.verbose:
                import traceback
                traceback.print_exc()
            sys.exit(1)
    else:
        try:
            state = core.execute(
                task_description=task_description,
                workspace=workspace,
                max_iterations=args.max_iterations,
            )
            _print_agent_summary(state, core)
        except KeyboardInterrupt:
            print("\n[DevAgent] Interrupted by user", file=sys.stderr)
            sys.exit(130)
        except Exception as e:
            print(f"[DevAgent] Fatal error: {e}", file=sys.stderr)
            if args.verbose:
                import traceback
                traceback.print_exc()
            sys.exit(1)


def _print_pipeline_summary(pstate, core):
    """Print PipelineRunner results."""
    print(f"\n[DevAgent Pipeline] Status: {pstate.status}")
    print(f"[DevAgent Pipeline] Phases: {len(pstate.results)}")
    for r in pstate.results:
        icon = "✅" if r.success else "❌"
        print(f"  {icon} {r.phase} — {r.review_decision} ({r.retries} retries, {r.elapsed_seconds:.0f}s)")
        if r.review_feedback:
            print(f"     Feedback: {r.review_feedback[:120]}")
    if pstate.status in ("COMPLETED", "FINISHED"):
        sys.exit(0)
    else:
        sys.exit(1)


def _print_agent_summary(state, core):
    """Print ReAct agent results."""
    print(f"\n[DevAgent] Status: {state.status}")
    print(f"[DevAgent] Iterations: {state.current_iteration}/{state.max_iterations}")
    print(f"[DevAgent] Files modified: {len(state.modified_files)}")
    if state.modified_files:
        for f in state.modified_files:
            print(f"  - {f}")
    if state.test_results:
        tr = state.test_results
        print(f"[DevAgent] Tests: {tr.get('passed', 0)} passed, {tr.get('failed', 0)} failed")
    if core.interaction:
        print(f"[DevAgent] Steps: {core.interaction._step_counter}")
    if state.status in ("COMPLETED", "FINISHED"):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
