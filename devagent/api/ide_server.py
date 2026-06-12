"""Unified IDE integration server that starts both the HTTP API and LSP server.

Usage:
    python -m devagent.api.ide_server          # Start both servers
    python -m devagent.api.ide_server --api     # Start only API
    python -m devagent.api.ide_server --lsp     # Start only LSP
"""

import os
import sys
import argparse
import subprocess
import threading
import signal


def start_api(host: str = "127.0.0.1", port: int = 8911, interactive: str = "off"):
    """Start the DevAgent HTTP API server with WebSocket support.

    Args:
        host: Bind address
        port: Bind port
        interactive: Interactive mode — "full" / "approval" / "observe" / "off"
    """
    from devagent.api.app import run_server
    print(f"[DevAgent-IDE] Starting HTTP API on http://{host}:{port}")
    print(f"[DevAgent-IDE] Interactive mode: {interactive}")
    run_server(host=host, port=port, interactive=interactive)


def start_lsp(host: str = "127.0.0.1", port: int = 2087):
    """Start the DevAgent LSP server over TCP."""
    try:
        from devagent.lsp.server import run_lsp_tcp
        print(f"[DevAgent-IDE] Starting LSP server on {host}:{port}")
        run_lsp_tcp(host=host, port=port)
    except ImportError as e:
        print(f"[DevAgent-IDE] Failed to start LSP: {e}")
        print("[DevAgent-IDE] Install with: pip install devagent[lsp] or pip install pygls")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="DevAgent IDE Integration Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start both API and LSP servers
  devagent-ide

  # Start only the HTTP API server
  devagent-ide --api

  # Start only the LSP server
  devagent-ide --lsp

  # Custom ports
  devagent-ide --api-port 9000 --lsp-port 2088
        """
    )
    parser.add_argument("--api", action="store_true", help="Start HTTP API server only")
    parser.add_argument("--lsp", action="store_true", help="Start LSP server only")
    parser.add_argument("--api-host", default="127.0.0.1", help="HTTP API host (default: 127.0.0.1)")
    parser.add_argument("--api-port", type=int, default=8911, help="HTTP API port (default: 8911)")
    parser.add_argument("--interactive", default="off",
                       choices=["full", "approval", "observe", "off"],
                       help="Interactive mode (default: off)")
    parser.add_argument("--lsp-host", default="127.0.0.1", help="LSP host (default: 127.0.0.1)")
    parser.add_argument("--lsp-port", type=int, default=2087, help="LSP port (default: 2087)")

    args = parser.parse_args()

    start_api_only = args.api
    start_lsp_only = args.lsp
    start_both = not start_api_only and not start_lsp_only

    threads = []

    if start_api_only or start_both:
        t = threading.Thread(
            target=start_api,
            args=(args.api_host, args.api_port, args.interactive),
            daemon=True,
        )
        t.start()
        threads.append(t)

    if start_lsp_only or start_both:
        t = threading.Thread(
            target=start_lsp,
            args=(args.lsp_host, args.lsp_port),
            daemon=True,
        )
        t.start()
        threads.append(t)

    print(f"[DevAgent-IDE] IDE Integration servers running.")
    print(f"[DevAgent-IDE] Press Ctrl+C to stop all servers.")

    def shutdown(sig, frame):
        print("\n[DevAgent-IDE] Shutting down...")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Keep main thread alive
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("\n[DevAgent-IDE] Shutting down...")
        sys.exit(0)


if __name__ == "__main__":
    main()
