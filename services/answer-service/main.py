"""Answer and logging service: drafts an answer only from retrieved snippets.

Runs on port 8003 by default (set PORT to change it).
Endpoints:
  GET  /health -> {"status": "ok", "service": "answer-service"}
  GET  /metrics -> request/error counts, average latency, uptime
  GET  /runs   -> list of logged runs, newest first
  POST /answer -> {"question": "...", "snippets": [...], "run_id": "...",
                   "trace_id": "...", "style": "short"|"detailed"}
                 returns {"run_id": ..., "trace_id": ..., "answer": ...,
                          "sources": [...], "style": ...}
Every run is logged to its own SQLite table (runs.db), with the
trace_id and the answer style, so one request can be followed across
all 3 services. Every request is one structured JSON log line.

Idempotence: run_id is unique. Sending the same run_id again returns
the stored answer and does not log a second run.

The answer is written through a small language-model interface
(LLMClient). The default client is MockLLMClient: a local,
deterministic, rule-based client that only repeats what is in the
snippets, using the prompt templates in docs/prompts.md. A real model
would plug into the same interface (RealLLMClient is a documented
stub that refuses to run until a real model is configured). No real
language model is called, and nothing here claims one was.
Python standard library only.
"""

import json
import os
import sqlite3
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SERVICE_NAME = "answer-service"
PORT = int(os.environ.get("PORT", "8003"))
DB_PATH = Path(__file__).parent / "runs.db"

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


# Prompt templates (same wording as docs/prompts.md). The mock client
# fills them from the retrieved snippets only; it never adds facts
# that are not in the snippets.
PROMPT_TEMPLATES = {
    "short": (
        "Answer the question using only the snippets below. "
        "Use the first sentence of the first snippet as the direct answer. "
        "Name the source documents. If the snippets do not cover the "
        "question, say you do not have the information."
    ),
    "detailed": (
        "Answer the question using only the snippets below. "
        "Give the first two sentences of the first snippet as the direct "
        "answer, then list every source document used. "
        "Name the source documents. If the snippets do not cover the "
        "question, say you do not have the information."
    ),
}

NO_INFO_ANSWER = (
    "I do not have information on that in the provided docs. "
    "Please ask about the month-end close, vendor payments, "
    "monthly reporting, or travel expenses."
)


class LLMClient:
    """Pluggable answer interface. draft() takes a question, the
    retrieved snippets, and an answer style, and returns answer text."""

    def draft(self, question, snippets, style="short"):
        raise NotImplementedError


class MockLLMClient(LLMClient):
    """Deterministic rule-based client. Only repeats snippet text."""

    def draft(self, question, snippets, style="short"):
        return draft_answer(question, snippets, style=style)


class RealLLMClient(LLMClient):
    """Stub for a real model. It is never called by default: it raises
    until someone configures a real model endpoint and API key through
    environment variables, and even then the same validation, human
    check, and logging around it still apply."""

    def draft(self, question, snippets, style="short"):
        raise RuntimeError(
            "No real language model is configured. Set LLM_ENDPOINT and "
            "LLM_API_KEY to plug one in; the mock client is used until then."
        )


# The client in use. Swap MockLLMClient for RealLLMClient only after a
# real model is configured; the tests and eval run against the mock.
LLM = MockLLMClient()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS runs "
        "(id INTEGER PRIMARY KEY, run_id TEXT UNIQUE, question TEXT, "
        "snippets_used TEXT, answer TEXT, trace_id TEXT, style TEXT, "
        "created_at REAL)"
    )
    existing = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    for name in ("trace_id", "style"):
        if name not in existing:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {name} TEXT")
    # Older rows were created without a unique run_id; enforce it now
    # only for new inserts (handled in log_run with INSERT OR IGNORE).
    conn.commit()
    conn.close()


def get_run(run_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT run_id, question, snippets_used, answer, trace_id, style "
        "FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "run_id": row["run_id"],
        "question": row["question"],
        "snippets_used": json.loads(row["snippets_used"] or "[]"),
        "answer": row["answer"],
        "trace_id": row["trace_id"],
        "style": row["style"],
    }


def log_run(run_id, question, snippets, answer, trace_id="", style="short"):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR IGNORE INTO runs (run_id, question, snippets_used, "
        "answer, trace_id, style, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            question,
            json.dumps(snippets),
            answer,
            trace_id,
            style,
            time.time(),
        ),
    )
    conn.commit()
    conn.close()


def list_runs(limit=50):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT run_id, question, snippets_used, answer, trace_id, style, "
        "created_at FROM runs ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [
        {
            "run_id": r["run_id"],
            "question": r["question"],
            "snippets_used": json.loads(r["snippets_used"]),
            "answer": r["answer"],
            "trace_id": r["trace_id"],
            "style": r["style"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def _sentences(text):
    return [s.strip() for s in text.split(".") if s.strip()]


def draft_answer(question, snippets, style="short"):
    if not snippets:
        return NO_INFO_ANSWER
    sources = []
    for snippet in snippets:
        doc = snippet.get("doc", "unknown")
        if doc not in sources:
            sources.append(doc)
    first = snippets[0].get("text", "")
    sentences = _sentences(first)
    if style == "detailed":
        picked = ". ".join(sentences[:2])
    else:
        picked = ". ".join(sentences[:1])
    if picked:
        picked += "."
    if style == "detailed":
        return (
            f"Based on {', '.join(sources)}: {picked} "
            f"Sources used: {', '.join(sources)}. "
            f"See {', '.join(sources)} for the full detail."
        )
    return (
        f"Based on {', '.join(sources)}: {picked} "
        f"See {', '.join(sources)} for the full detail."
    )


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
        elif self.path == "/runs":
            self._send(200, {"runs": list_runs()})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        self._start = time.time()
        if self.path != "/answer":
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
        snippets = data.get("snippets", [])
        if not isinstance(snippets, list):
            snippets = []
        run_id = str(data.get("run_id") or uuid.uuid4().hex[:8])
        trace_id = str(data.get("trace_id") or "")
        style = str(data.get("style") or "short")
        if style not in PROMPT_TEMPLATES:
            style = "short"
        # Idempotence: the same run_id returns the stored answer.
        existing = get_run(run_id)
        if existing is not None:
            log_json(
                "INFO", "duplicate_run_id", trace_id=trace_id, run_id=run_id
            )
            self._send(
                200,
                {
                    "run_id": existing["run_id"],
                    "trace_id": existing["trace_id"],
                    "answer": existing["answer"],
                    "sources": [
                        s.get("doc", "unknown")
                        for s in existing["snippets_used"]
                    ],
                    "style": existing["style"],
                    "duplicate": True,
                },
                trace_id=trace_id,
            )
            return
        answer = LLM.draft(question, snippets, style=style)
        sources = []
        for snippet in snippets:
            doc = snippet.get("doc", "unknown")
            if doc not in sources:
                sources.append(doc)
        log_run(run_id, question, snippets, answer, trace_id, style)
        self._send(
            200,
            {
                "run_id": run_id,
                "trace_id": trace_id,
                "answer": answer,
                "sources": sources,
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
