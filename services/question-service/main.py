"""Question service (gateway): takes a question, retrieves context,
then gets an answer, and returns the answer with sources and a run id.

Runs on port 8001 by default (set PORT to change it).
Endpoints:
  GET  /health  -> {"status": "ok", "service": "question-service"}
  GET  /metrics -> request/error counts, average latency, uptime
  POST /ask     -> {"question": "...", "request_id": "..."} returns
                   {"run_id": ..., "trace_id": ..., "question": ...,
                    "answer": ..., "sources": [...], "snippets": [...],
                    "style": ..., "duplicate": false}
It calls the retrieval service, then the answer service, over HTTP.
Every question received is logged to its own SQLite table (questions.db).

Idempotence: a client-supplied request_id (or Idempotency-Key header)
is stored with the answer. Sending the same request_id again returns
the stored answer and does not call the downstream services again, so
a retried request cannot create a duplicate run.

Tracing: every request gets a trace_id. It is passed to both
downstream services in the JSON body and in the X-Trace-Id header,
written to every log line, and stored with the run record.

Feature flags: flags.json (project root, or next to this file) picks
the answer style ("short" or "detailed"). A canary block in the same
file can roll a new style out to a small share of trace ids first;
see scripts/canary.py. ANSWER_STYLE env var overrides the file.
Python standard library only.
"""

import hashlib
import json
import os
import sqlite3
import time
import urllib.request
import urllib.error
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SERVICE_NAME = "question-service"
PORT = int(os.environ.get("PORT", "8001"))
RETRIEVAL_URL = os.environ.get("RETRIEVAL_URL", "http://127.0.0.1:8002")
ANSWER_URL = os.environ.get("ANSWER_URL", "http://127.0.0.1:8003")
DB_PATH = Path(__file__).parent / "questions.db"

DEFAULT_FLAGS = {
    "answer_style": "short",
    "canary": {"enabled": False, "percent": 10, "new_style": "detailed"},
}

COLUMNS = {
    "run_id": "TEXT",
    "request_id": "TEXT UNIQUE",
    "question": "TEXT",
    "answer": "TEXT",
    "sources": "TEXT",
    "snippets": "TEXT",
    "trace_id": "TEXT",
    "style": "TEXT",
    "created_at": "REAL",
}

STARTED_AT = time.time()
METRICS = {"requests": 0, "errors": 0, "total_latency_ms": 0.0}


def log_json(level, event, trace_id="", **extra):
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "level": level,
        "service": SERVICE_NAME,
        "trace_id": trace_id,
        "event": event,
    }
    record.update(extra)
    print(json.dumps(record), flush=True)


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cols = ", ".join(f"{name} {ctype}" for name, ctype in COLUMNS.items())
    conn.execute(f"CREATE TABLE IF NOT EXISTS questions (id INTEGER PRIMARY KEY, {cols})")
    existing = {row[1] for row in conn.execute("PRAGMA table_info(questions)")}
    for name, ctype in COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE questions ADD COLUMN {name} {ctype}")
    conn.commit()
    conn.close()


def log_question(run_id, request_id, question, answer, sources, snippets, trace_id, style="short"):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT INTO questions (run_id, request_id, question, answer, "
            "sources, snippets, trace_id, style, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                request_id,
                question,
                answer,
                json.dumps(sources),
                json.dumps(snippets),
                trace_id,
                style,
                time.time(),
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # Same request_id already logged (a retry raced us): keep the first.
        pass
    finally:
        conn.close()


def get_cached(request_id):
    if not request_id:
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT run_id, question, answer, sources, snippets, trace_id, style "
        "FROM questions WHERE request_id = ?",
        (request_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "run_id": row["run_id"],
        "question": row["question"],
        "answer": row["answer"],
        "sources": json.loads(row["sources"] or "[]"),
        "snippets": json.loads(row["snippets"] or "[]"),
        "trace_id": row["trace_id"],
        "style": row["style"] or "short",
    }


def find_flags_file():
    if os.environ.get("FLAGS_PATH"):
        return Path(os.environ["FLAGS_PATH"])
    here = Path(__file__).resolve()
    for candidate in [here.parent / "flags.json", *[
        parent / "flags.json" for parent in here.parents
    ]]:
        if candidate.is_file():
            return candidate
    return None


def load_flags():
    path = find_flags_file()
    if path is None:
        return dict(DEFAULT_FLAGS)
    try:
        with open(path, encoding="utf-8") as f:
            flags = json.load(f)
        merged = dict(DEFAULT_FLAGS)
        merged.update(flags)
        return merged
    except (OSError, ValueError):
        return dict(DEFAULT_FLAGS)


def choose_style(trace_id, flags):
    """Pick the answer style. Env var wins; then a canary share of
    trace ids gets the new style; otherwise the configured style."""
    if os.environ.get("ANSWER_STYLE"):
        return os.environ["ANSWER_STYLE"]
    canary = flags.get("canary") or {}
    if canary.get("enabled"):
        percent = int(canary.get("percent", 0))
        bucket = int(hashlib.sha256(trace_id.encode()).hexdigest(), 16) % 100
        if bucket < percent:
            return str(canary.get("new_style", "detailed"))
    return str(flags.get("answer_style", "short"))


def post_json(url, payload, trace_id):
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "X-Trace-Id": trace_id},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, payload, trace_id=""):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        latency_ms = (time.time() - getattr(self, "_start", time.time())) * 1000
        METRICS["requests"] += 1
        METRICS["total_latency_ms"] += latency_ms
        if status >= 400:
            METRICS["errors"] += 1
        log_json(
            "ERROR" if status >= 400 else "INFO",
            "request",
            trace_id=trace_id,
            method=self.command,
            path=self.path,
            status=status,
            latency_ms=round(latency_ms, 2),
        )

    def do_GET(self):
        self._start = time.time()
        if self.path == "/health":
            self._send(200, {"status": "ok", "service": SERVICE_NAME})
        elif self.path == "/metrics":
            avg = (
                METRICS["total_latency_ms"] / METRICS["requests"]
                if METRICS["requests"]
                else 0.0
            )
            self._send(
                200,
                {
                    "service": SERVICE_NAME,
                    "requests_total": METRICS["requests"],
                    "errors_total": METRICS["errors"],
                    "avg_latency_ms": round(avg, 2),
                    "uptime_seconds": round(time.time() - STARTED_AT, 1),
                },
            )
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        self._start = time.time()
        if self.path != "/ask":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid JSON"})
            return
        question = str(data.get("question", "")).strip()
        if not question:
            self._send(400, {"error": "question is required"})
            return
        request_id = data.get("request_id") or self.headers.get("Idempotency-Key") or None
        if request_id:
            cached = get_cached(str(request_id))
            if cached is not None:
                log_json(
                    "INFO", "duplicate_request_id",
                    trace_id=cached.get("trace_id", ""),
                    request_id=str(request_id),
                )
                self._send(
                    200,
                    {
                        "run_id": cached["run_id"],
                        "trace_id": cached["trace_id"],
                        "question": cached["question"],
                        "answer": cached["answer"],
                        "sources": cached["sources"],
                        "snippets": cached["snippets"],
                        "style": cached.get("style", "short"),
                        "duplicate": True,
                    },
                    trace_id=cached.get("trace_id", ""),
                )
                return
        run_id = uuid.uuid4().hex[:8]
        trace_id = uuid.uuid4().hex
        style = choose_style(trace_id, load_flags())
        try:
            retrieved = post_json(
                RETRIEVAL_URL + "/retrieve",
                {"question": question, "trace_id": trace_id},
                trace_id,
            )
            snippets = retrieved.get("snippets", [])
            answered = post_json(
                ANSWER_URL + "/answer",
                {
                    "question": question,
                    "snippets": snippets,
                    "run_id": run_id,
                    "trace_id": trace_id,
                    "style": style,
                },
                trace_id,
            )
        except (urllib.error.URLError, urllib.error.HTTPError) as exc:
            # Fault tolerance: report the failure, never invent an answer.
            log_json(
                "ERROR", "downstream_failed", trace_id=trace_id,
                error=str(exc),
            )
            self._send(
                502,
                {"error": f"downstream service failed: {exc}", "trace_id": trace_id},
                trace_id=trace_id,
            )
            return
        answer = answered.get("answer", "")
        sources = answered.get("sources", [])
        log_question(run_id, request_id, question, answer, sources, snippets, trace_id, style)
        self._send(
            200,
            {
                "run_id": run_id,
                "trace_id": trace_id,
                "question": question,
                "answer": answer,
                "sources": sources,
                "snippets": snippets,
                "style": style,
                "duplicate": False,
            },
            trace_id=trace_id,
        )

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    init_db()
    print(f"{SERVICE_NAME} listening on port {PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
