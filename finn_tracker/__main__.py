"""
finn-tracker CLI entry point.

Usage:
    finn-tracker [--version] [--help] [--demo]

Starts the finn-tracker server at http://localhost:5050 and opens your browser.
Data directory: ~/Documents/finn-tracker/
"""
import importlib.metadata
import logging
import os
import socket
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


# ── Testable helpers ──────────────────────────────────────────────────────────

def _check_python_version() -> None:
    """Exit with a clear message if Python is too old."""
    if sys.version_info < (3, 9):
        print(
            f"finn-tracker requires Python 3.9 or later.\n"
            f"Your version: {sys.version.split()[0]}\n"
            f"Download Python at https://python.org/downloads"
        )
        sys.exit(1)


def _resolve_data_dir() -> Path:
    """Return the data directory, creating subdirs if needed.

    Override with EXPENSE_TRACKER_DATA env var (useful for dev:
    EXPENSE_TRACKER_DATA=./data finn-tracker).
    """
    raw = os.environ.get("EXPENSE_TRACKER_DATA")
    data_dir = Path(raw).expanduser().resolve() if raw else Path.home() / "Documents" / "finn-tracker"
    try:
        (data_dir / "expense").mkdir(parents=True, exist_ok=True)
        (data_dir / "income").mkdir(parents=True, exist_ok=True)
    except PermissionError:
        print(
            f"finn-tracker could not create its data directory:\n"
            f"  {data_dir}\n"
            f"Check that you have write permission, or set a different location:\n"
            f"  EXPENSE_TRACKER_DATA=/path/to/writable/dir finn-tracker"
        )
        sys.exit(1)
    return data_dir


def _check_port(port: int) -> bool:
    """Return True if the port is free."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


# ── Main entry point ──────────────────────────────────────────────────────────

def main() -> None:
    _check_python_version()

    # ── Parse args ────────────────────────────────────────────────────────────
    args = sys.argv[1:]
    demo_mode = False
    for arg in args:
        if arg == "--version":
            try:
                version = importlib.metadata.version("finn-tracker")
            except importlib.metadata.PackageNotFoundError:
                version = "dev"
            print(f"finn-tracker {version}")
            return
        elif arg == "--help":
            print(
                "Usage: finn-tracker [--version] [--help] [--demo]\n"
                "Starts the finn-tracker server at http://localhost:5050 and opens your browser.\n"
                "Data: ~/Documents/finn-tracker/\n\n"
                "  --version   Print version and exit\n"
                "  --help      Show this help and exit\n"
                "  --demo      Load synthetic sample data to try the dashboard"
            )
            return
        elif arg == "--demo":
            demo_mode = True
        else:
            print(f"Unknown option: {arg}\nRun `finn-tracker --help` for usage.")
            sys.exit(1)

    # ── Resolve port ──────────────────────────────────────────────────────────
    port = int(os.environ.get("EXPENSE_TRACKER_PORT", 5050))

    # ── Resolve data directory (MUST happen before importing app) ─────────────
    data_dir = _resolve_data_dir()
    os.environ["EXPENSE_TRACKER_DATA"] = str(data_dir)

    # ── Demo mode: seed sample data ───────────────────────────────────────────
    if demo_mode:
        try:
            from sample_data.generators import write_demo_files  # type: ignore
            write_demo_files(str(data_dir / "expense"))
            print(f"Sample data loaded into {data_dir}/expense/ and income/ — starting finn-tracker...")
        except Exception as e:
            print(f"Warning: could not load sample data ({e}). Starting anyway.")

    # ── First-run guidance ────────────────────────────────────────────────────
    expense_dir = data_dir / "expense"
    has_files = any(
        p.suffix.lower() in {".csv", ".pdf"}
        for p in expense_dir.iterdir()
        if expense_dir.exists()
    )
    if not has_files and not demo_mode:
        print(
            f"No transactions yet. To get started:\n"
            f"  1. Export a CSV or PDF from your bank\n"
            f"  2. Drop it into {data_dir}/expense/\n"
            f"  3. Refresh the page in your browser\n"
            f"\n  Or try: finn-tracker --demo"
        )

    # ── Port check ────────────────────────────────────────────────────────────
    if not _check_port(port):
        print(
            f"Port {port} is already in use.\n"
            f"Is finn-tracker already running? Check http://localhost:{port}\n"
            f"To use a different port: EXPENSE_TRACKER_PORT=5051 finn-tracker"
        )
        sys.exit(1)

    # ── Suppress werkzeug banner ──────────────────────────────────────────────
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    # ── Import app (after env var is set so _init_db uses correct path) ───────
    import finn_tracker.app as flask_app

    # ── Start browser daemon thread ───────────────────────────────────────────
    url = f"http://localhost:{port}"
    print(f"finn-tracker running at {url}  (Ctrl+C to stop)")

    def _open_browser() -> None:
        base = f"http://127.0.0.1:{port}/"
        for _ in range(50):  # 50 × 100ms = 5s max
            try:
                urllib.request.urlopen(base, timeout=0.5)  # noqa: S310
                break
            except urllib.error.URLError:
                import time
                time.sleep(0.1)
        else:
            print(f"Could not open browser automatically. Visit {url} manually.")
            return
        try:
            webbrowser.open(url)
        except Exception:
            pass

    t = threading.Thread(target=_open_browser, daemon=True)
    t.start()

    # ── Start Flask in main thread (handles Ctrl+C correctly) ─────────────────
    flask_app.app.run(host="127.0.0.1", port=port, use_reloader=False)


if __name__ == "__main__":
    main()
