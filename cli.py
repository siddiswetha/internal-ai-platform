"""Command-line tool for the internal AI platform (developer tooling).

Usage:
  python3 cli.py ask "When should vendor invoices be paid?"
  python3 cli.py ask "What is the close checklist?" --request-id my-run-1
  python3 cli.py health
  python3 cli.py runs
  python3 cli.py metrics

The services must be running first (python3 run_local.py, or
docker compose up). Python standard library only.
"""

import argparse
import json
import sys
import urllib.request
import urllib.error

BASES = {
    "question-service": "http://127.0.0.1:8001",
    "retrieval-service": "http://127.0.0.1:8002",
    "answer-service": "http://127.0.0.1:8003",
}


def get_json(url):
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def post_json(url, payload):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def cmd_ask(args):
    payload = {"question": args.question}
    if args.request_id:
        payload["request_id"] = args.request_id
    try:
        result = post_json(BASES["question-service"] + "/ask", payload)
    except urllib.error.URLError as exc:
        print(f"Could not reach the question service: {exc}")
        return 1
    print(f"Answer: {result.get('answer', '')}")
    print(f"Sources: {', '.join(result.get('sources', []))}")
    print(f"Run id: {result.get('run_id', '')}")
    print(f"Trace id: {result.get('trace_id', '')}")
    print(f"Style: {result.get('style', '')}")
    if result.get("duplicate"):
        print("(Duplicate request id — this is the stored answer, not a new run.)")
    return 0


def cmd_health(_args):
    ok = True
    for name, base in BASES.items():
        try:
            data = get_json(base + "/health")
            print(f"{name}: {data.get('status', '?')}")
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"{name}: DOWN ({exc})")
            ok = False
    return 0 if ok else 1


def cmd_runs(args):
    try:
        data = get_json(BASES["answer-service"] + "/runs")
    except urllib.error.URLError as exc:
        print(f"Could not reach the answer service: {exc}")
        return 1
    runs = data.get("runs", [])[: args.limit]
    if not runs:
        print("No runs logged yet.")
        return 0
    for run in runs:
        print(
            f"{run.get('run_id', '')} | {run.get('question', '')} | "
            f"trace {run.get('trace_id', '')} | style {run.get('style', '')}"
        )
        print(f"  {run.get('answer', '')}")
    return 0


def cmd_metrics(_args):
    for name, base in BASES.items():
        try:
            data = get_json(base + "/metrics")
            print(
                f"{name}: {data.get('requests_total', 0)} requests, "
                f"{data.get('errors_total', 0)} errors, "
                f"avg latency {data.get('avg_latency_ms', 0)} ms, "
                f"uptime {data.get('uptime_seconds', 0)} s"
            )
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"{name}: metrics unavailable ({exc})")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Internal AI platform CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    p_ask = sub.add_parser("ask", help="Ask a question")
    p_ask.add_argument("question")
    p_ask.add_argument("--request-id", default=None,
                       help="Idempotency key: reuse it to get the stored answer")
    p_ask.set_defaults(func=cmd_ask)
    sub.add_parser("health", help="Check all 3 services").set_defaults(func=cmd_health)
    p_runs = sub.add_parser("runs", help="List recent logged runs")
    p_runs.add_argument("--limit", type=int, default=10)
    p_runs.set_defaults(func=cmd_runs)
    sub.add_parser("metrics", help="Show metrics for all 3 services").set_defaults(func=cmd_metrics)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
