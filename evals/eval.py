"""Eval for the internal AI platform. Pass bar: 12/12.

Starts all 3 services on ports 8001-8003, asks each question in
evals/eval-set.csv through the question service, and checks:
  1. the question service answered (HTTP 200, non-empty answer),
  2. the right doc was retrieved (expected doc in sources, or no
     sources for expected_doc == "none"),
  3. the run was logged (GET /runs on the answer service lists a
     run with this run_id and question).
Results are the true results of the run: they are written to
evals/eval-results.csv and to the result column of eval-set.csv.
A failed case is recorded as failed, never edited to look like a pass.
Services are stopped at the end. Usage: python3 evals/eval.py
"""

import csv
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_SET = ROOT / "evals" / "eval-set.csv"
RESULTS = ROOT / "evals" / "eval-results.csv"

SERVICES = [
    ("question-service", 8001),
    ("retrieval-service", 8002),
    ("answer-service", 8003),
]


def get_json(url):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read())


def post_json(url, payload):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def wait_for_health(port, timeout=15):
    start = time.time()
    while time.time() - start < timeout:
        try:
            data = get_json(f"http://127.0.0.1:{port}/health")
            if data.get("status") == "ok":
                return True
        except Exception:
            time.sleep(0.3)
    return False


def main():
    procs = []
    try:
        for name, port in SERVICES:
            env = dict(os.environ)
            env["PORT"] = str(port)
            proc = subprocess.Popen(
                [sys.executable, "main.py"],
                cwd=ROOT / "services" / name,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            procs.append(proc)
        for _, port in SERVICES:
            if not wait_for_health(port):
                print(f"Service on port {port} did not start")
                return 1

        with open(EVAL_SET, newline="", encoding="utf-8") as f:
            cases = list(csv.DictReader(f))

        results = []
        passed = 0
        for case in cases:
            case_id = case["id"]
            question = case["question"]
            expected = case["expected_doc"]
            notes = []
            try:
                answer = post_json(
                    "http://127.0.0.1:8001/ask", {"question": question}
                )
                run_id = answer.get("run_id", "")
                sources = answer.get("sources", [])
                text = answer.get("answer", "")
                if not text:
                    notes.append("empty answer")
                if expected == "none":
                    if sources:
                        notes.append(f"expected no sources, got {sources}")
                    if "do not have information" not in text:
                        notes.append("expected a no-information answer")
                else:
                    if expected not in sources:
                        notes.append(f"expected {expected} in sources {sources}")
                # Check the run was logged in the answer service.
                runs = get_json("http://127.0.0.1:8003/runs").get("runs", [])
                logged = any(
                    r.get("run_id") == run_id and r.get("question") == question
                    for r in runs
                )
                if not logged:
                    notes.append("run not found in answer service /runs")
            except Exception as exc:  # noqa: BLE001 - record as a failure
                notes.append(f"request failed: {exc}")
                run_id, sources = "", []
            ok = not notes
            if ok:
                passed += 1
            results.append(
                {
                    "id": case_id,
                    "question": question,
                    "expected_doc": expected,
                    "actual_sources": ",".join(sources),
                    "result": "PASS" if ok else "FAIL",
                    "notes": "; ".join(notes),
                }
            )
            print(f"{case_id}: {'PASS' if ok else 'FAIL'} — {question}")

        with open(RESULTS, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "id", "question", "expected_doc",
                    "actual_sources", "result", "notes",
                ],
            )
            writer.writeheader()
            writer.writerows(results)

        # Record the true result in eval-set.csv too.
        result_by_id = {r["id"]: r["result"] for r in results}
        rows = []
        with open(EVAL_SET, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            for row in reader:
                row["result"] = result_by_id.get(row["id"], "")
                rows.append(row)
        with open(EVAL_SET, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        print(f"\nEval result: {passed}/{len(cases)} passed")
        print(f"Wrote {RESULTS}")
        return 0 if passed == len(cases) else 1
    finally:
        for proc in procs:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
