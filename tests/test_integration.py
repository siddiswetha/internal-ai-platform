"""Integration test: start all 3 services and ask a question end to end.

Covers: the full ask path, the trace id reaching the run record,
idempotence (same request id returns the stored answer and logs one
run, not two), the /metrics endpoints, and fault tolerance (a
downstream service down gives a 502 instead of an invented answer).

Run: python3 -m unittest discover -s tests -v
"""

import json
import os
import subprocess
import sys
import time
import unittest
import urllib.request
import urllib.error
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = [
    ("question-service", 8001),
    ("retrieval-service", 8002),
    ("answer-service", 8003),
]


def get_json(url):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read())


def post_json(url, payload, headers=None):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read())


def wait_for_health(port, timeout=15):
    start = time.time()
    while time.time() - start < timeout:
        try:
            if get_json(f"http://127.0.0.1:{port}/health").get("status") == "ok":
                return True
        except Exception:
            time.sleep(0.3)
    return False


class IntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.procs = {}
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
            cls.procs[name] = proc
        for _, port in SERVICES:
            if not wait_for_health(port):
                raise RuntimeError(f"Service on port {port} did not start")

    @classmethod
    def tearDownClass(cls):
        for proc in cls.procs.values():
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    def test_ask_end_to_end_and_trace_id_reaches_run(self):
        _, result = post_json(
            "http://127.0.0.1:8001/ask",
            {"question": "When should vendor invoices be paid?"},
        )
        self.assertTrue(result.get("answer"))
        self.assertIn("vendor-payment-policy.md", result.get("sources", []))
        trace_id = result.get("trace_id")
        self.assertTrue(trace_id)
        runs = get_json("http://127.0.0.1:8003/runs").get("runs", [])
        logged = [r for r in runs if r.get("run_id") == result.get("run_id")]
        self.assertEqual(len(logged), 1)
        self.assertEqual(logged[0].get("trace_id"), trace_id)

    def test_duplicate_request_id_logs_one_run(self):
        request_id = f"integ-{uuid.uuid4().hex[:8]}"
        _, first = post_json(
            "http://127.0.0.1:8001/ask",
            {"question": "What should a monthly report include?",
             "request_id": request_id},
        )
        _, second = post_json(
            "http://127.0.0.1:8001/ask",
            {"question": "What should a monthly report include?",
             "request_id": request_id},
        )
        self.assertEqual(first.get("run_id"), second.get("run_id"))
        self.assertTrue(second.get("duplicate"))
        runs = get_json("http://127.0.0.1:8003/runs").get("runs", [])
        matching = [r for r in runs if r.get("run_id") == first.get("run_id")]
        self.assertEqual(len(matching), 1)

    def test_fault_tolerance_downstream_down_gives_502(self):
        proc = self.procs["retrieval-service"]
        proc.terminate()
        proc.wait(timeout=5)
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                post_json(
                    "http://127.0.0.1:8001/ask",
                    {"question": "When should vendor invoices be paid?"},
                )
            self.assertEqual(ctx.exception.code, 502)
        finally:
            env = dict(os.environ)
            env["PORT"] = "8002"
            new_proc = subprocess.Popen(
                [sys.executable, "main.py"],
                cwd=ROOT / "services" / "retrieval-service",
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.procs["retrieval-service"] = new_proc
            self.assertTrue(wait_for_health(8002))

    def test_metrics_endpoints(self):
        for port in (8001, 8002, 8003):
            data = get_json(f"http://127.0.0.1:{port}/metrics")
            self.assertIn("requests_total", data)
            self.assertIn("errors_total", data)
            self.assertIn("avg_latency_ms", data)
            self.assertGreaterEqual(data["requests_total"], 1)


if __name__ == "__main__":
    unittest.main()
