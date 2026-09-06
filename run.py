"""Start PAI and its workers with: py run.py (Ctrl+C stops everything)."""

import argparse
import os
import runpy
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8000"


def run_service(service):
    """Catch Ctrl+C even while a child is still importing its dependencies."""
    try:
        if service == "api":
            import uvicorn

            uvicorn.run("pai.app:create_app_from_env", factory=True, host="127.0.0.1", port=8000)
        else:
            sys.argv = ["pai.interfaces.workers", service]
            runpy.run_module("pai.interfaces.workers", run_name="__main__")
        return 0
    except KeyboardInterrupt:
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-browser", action="store_true", help="Do not open Swagger automatically"
    )
    parser.add_argument("--skip-migrations", action="store_true", help="Skip database migrations")
    parser.add_argument(
        "--service", choices=("api", "intelligence", "goals", "documents"), help=argparse.SUPPRESS
    )
    args = parser.parse_args()
    if args.service:
        return run_service(args.service)

    python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        print("Missing .venv. Run 'uv sync' in this folder first.", flush=True)
        return 1
    if not (ROOT / ".env").is_file():
        print("Missing .env. Copy .env.example to .env and fill in your settings.", flush=True)
        return 1

    env = os.environ.copy()
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", ""),
            "APP_ENV": "development",  # Make Swagger available for this local launcher.
            "RUN_WORKERS_IN_API": "false",
        }
    )
    processes = []
    interrupted = False
    try:
        # Refuse to start a second set of workers when another API owns the port.
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", 8000))
            except OSError:
                print("Port 8000 is busy. Stop the existing server, then run this again.")
                return 1

        if not args.skip_migrations:
            print("Updating database migrations...", flush=True)
            result = subprocess.run(
                [str(python), "-m", "alembic", "upgrade", "head"],
                cwd=ROOT,
                env=env,
            )
            if result.returncode:
                print("Migrations failed. Fix the database error above and try again.", flush=True)
                return result.returncode

        services = [("API", "api")] + [
            (f"{kind} worker", kind) for kind in ("intelligence", "goals", "documents")
        ]
        for name, service in services:
            print(f"Starting {name}...", flush=True)
            child = subprocess.Popen(
                [str(python), str(ROOT / "run.py"), "--service", service], cwd=ROOT, env=env
            )
            processes.append((name, child))

        print(f"\nSwagger: {URL}/docs\nPress Ctrl+C to stop all services.\n", flush=True)
        print("Loading services and connecting to the database. Please wait...", flush=True)
        announced = False
        next_notice = time.monotonic() + 10
        # Local readiness checks should not go through an HTTP proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while True:
            for name, child in processes:
                if child.poll() is not None:
                    print(f"{name} exited (code {child.returncode}). Stopping all services.")
                    return child.returncode or 1
            if not announced:
                try:
                    with opener.open(f"{URL}/health/live", timeout=0.5) as response:
                        if response.status == 200:
                            announced = True
                            print(f"PAI is running. Open {URL}/docs", flush=True)
                            if not args.no_browser:
                                try:
                                    webbrowser.open(f"{URL}/docs")
                                except webbrowser.Error:
                                    print("Could not open your browser; use the Swagger URL above.")
                except (urllib.error.URLError, TimeoutError, OSError):
                    pass
                if not announced and time.monotonic() >= next_notice:
                    print(
                        "Still starting; waiting for the API. Any startup errors appear above.",
                        flush=True,
                    )
                    next_notice = time.monotonic() + 10
            time.sleep(0.5)
    except KeyboardInterrupt:
        interrupted = True
        print("\nStopping PAI...", flush=True)
        return 0
    except OSError as exc:
        print(f"Could not start PAI: {exc}", flush=True)
        return 1
    finally:
        # All children share this console, so Ctrl+C already reaches them.
        # No reload process is used: every service is a direct child.
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            for _, child in processes:
                if child.poll() is None and not interrupted:
                    child.terminate()
            deadline = time.monotonic() + 35
            for _, child in processes:
                try:
                    child.wait(timeout=max(0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        finally:
            signal.signal(signal.SIGINT, previous)


if __name__ == "__main__":
    sys.exit(main())
