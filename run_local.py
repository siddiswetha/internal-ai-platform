"""Start all 3 services locally without Docker, on ports 8001-8003.

Usage: python3 run_local.py
Then ask a question in another terminal:
  curl -X POST http://127.0.0.1:8001/ask -H "Content-Type: application/json" \
    -d '{"question": "When should vendor invoices be paid?"}'
Stop with Ctrl+C. Each service keeps its own SQLite database file
(questions.db, retrieval.db, runs.db) in its own folder; those files
are generated locally and are ignored by .gitignore.
"""

import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVICES = [
    ("question-service", 8001, {}),
    ("retrieval-service", 8002, {}),
    ("answer-service", 8003, {}),
]


def wait_for_health(port, timeout=15):
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=2
            ) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def _handle_term(signum, frame):
    # SIGTERM (e.g. kill, or a container stopping) must stop the child
    # services too, not just this script. Raising KeyboardInterrupt
    # runs the finally block below, which terminates each child.
    raise KeyboardInterrupt


def main():
    signal.signal(signal.SIGTERM, _handle_term)
    procs = []
    try:
        for name, port, env_extra in SERVICES:
            env = dict(os.environ)
            env["PORT"] = str(port)
            env.update(env_extra)
            proc = subprocess.Popen(
                [sys.executable, "main.py"],
                cwd=ROOT / "services" / name,
                env=env,
            )
            procs.append((name, proc))
        for name, port, _ in SERVICES:
            ok = wait_for_health(port)
            print(f"{name} on {port}: {'up' if ok else 'did not start'}")
        print("All services running. Press Ctrl+C to stop.")
        while True:
            time.sleep(1)
            for name, proc in procs:
                if proc.poll() is not None:
                    print(f"{name} stopped (exit {proc.returncode})")
                    raise SystemExit(1)
    except KeyboardInterrupt:
        print("Stopping services...")
    finally:
        for _, proc in procs:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    main()
