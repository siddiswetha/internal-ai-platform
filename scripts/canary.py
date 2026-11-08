"""Canary rollout for the answer style flag.

Turns a canary on for a small share of requests, sends probe
questions, checks the error rate, and either promotes the new style
to everyone or rolls it back.

Usage:
  python3 scripts/canary.py --percent 10 --probes 10 --new-style detailed
  python3 scripts/canary.py --percent 10 --probes 10 --max-error-rate 0.1

The services must be running first (python3 run_local.py, or
docker compose up). flags.json is written atomically (write to a
temporary file, then replace). Python standard library only.
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLAGS_PATH = ROOT / "flags.json"
QUESTION_URL = "http://127.0.0.1:8001"

PROBE_QUESTIONS = [
    "When should vendor invoices be paid?",
    "What are the steps to close the month?",
    "What should a monthly report include?",
    "What receipts are needed for travel expenses?",
    "How do we reconcile the ledger against the bank statement?",
    "What is the limit for travel expenses without extra approval?",
]


def read_flags():
    with open(FLAGS_PATH, encoding="utf-8") as f:
        return json.load(f)


def write_flags(flags):
    tmp = FLAGS_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(flags, f, indent=2)
    tmp.replace(FLAGS_PATH)


def post_ask(question, request_id):
    body = json.dumps({"question": question, "request_id": request_id}).encode("utf-8")
    req = urllib.request.Request(
        QUESTION_URL + "/ask",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read())


def main(argv=None):
    parser = argparse.ArgumentParser(description="Canary rollout for the answer style")
    parser.add_argument("--percent", type=int, default=10,
                        help="Share of requests (0-100) that get the new style")
    parser.add_argument("--probes", type=int, default=10,
                        help="How many probe questions to send")
    parser.add_argument("--new-style", default="detailed",
                        help="Style to roll out (short or detailed)")
    parser.add_argument("--max-error-rate", type=float, default=0.1,
                        help="Roll back if the probe error rate is above this")
    args = parser.parse_args(argv)
    if not 0 <= args.percent <= 100:
        print("percent must be between 0 and 100")
        return 2

    before = read_flags()
    old_style = before.get("answer_style", "short")
    print(f"Current style: {old_style}. Canary: {args.percent}% -> {args.new_style}")

    # Step 1: turn the canary on.
    flags = dict(before)
    flags["canary"] = {
        "enabled": True,
        "percent": args.percent,
        "new_style": args.new_style,
    }
    write_flags(flags)
    print("Canary on. Sending probes...")

    # Step 2: probe. Each probe uses a fresh request id.
    failures = 0
    styles_seen = set()
    for i in range(args.probes):
        question = PROBE_QUESTIONS[i % len(PROBE_QUESTIONS)]
        try:
            status, result = post_ask(question, f"canary-{uuid.uuid4().hex[:8]}")
            styles_seen.add(result.get("style", ""))
            if status != 200 or not result.get("answer"):
                failures += 1
        except Exception as exc:  # noqa: BLE001 - a failed probe counts
            failures += 1
            print(f"  probe {i + 1} failed: {exc}")
        time.sleep(0.1)
    error_rate = failures / args.probes if args.probes else 1.0
    print(f"Probes: {args.probes}, failures: {failures}, "
          f"error rate: {error_rate:.2f}, styles seen: {sorted(styles_seen)}")

    # Step 3: promote or roll back.
    flags = read_flags()
    if error_rate <= args.max_error_rate:
        flags["answer_style"] = args.new_style
        flags["canary"] = {"enabled": False, "percent": args.percent,
                           "new_style": args.new_style}
        write_flags(flags)
        print(f"Promoted: answer_style is now {args.new_style}, canary off.")
        return 0
    flags["answer_style"] = old_style
    flags["canary"] = {"enabled": False, "percent": args.percent,
                       "new_style": args.new_style}
    write_flags(flags)
    print(f"Rolled back: answer_style stays {old_style}, canary off.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
