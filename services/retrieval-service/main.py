"""Retrieval service: searches local plain-text docs by keyword overlap.

Runs on port 8002 by default (set PORT to change it).
Endpoints:
  GET  /health    -> {"status": "ok", "service": "retrieval-service"}
  GET  /metrics   -> request/error counts, average latency, uptime
  POST /retrieve  -> {"question": "...", "trace_id": "..."} returns
                     top snippets with doc names, plus the trace_id.
Every retrieval request is logged to its own SQLite table (retrieval.db),
with the trace_id so one request can be followed across all 3 services.
Every request is written as one structured JSON log line.
Python standard library only.
"""

import json
import os
import re
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SERVICE_NAME = "retrieval-service"
PORT = int(os.environ.get("PORT", "8002"))
DB_PATH = Path(__file__).parent / "retrieval.db"

STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "what", "when", "where",
    "who", "how", "do", "does", "did", "to", "of", "for", "in", "on", "and",
    "or", "it", "its", "i", "we", "you", "me", "my", "should", "can", "could",
    "with", "after", "before", "by", "from", "that", "this", "these", "those",
    "be", "been", "have", "has", "had", "as", "at", "if", "then", "than",
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


def find_docs_dir():
    """Docs live in <project>/docs, two levels up from this file."""
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / "docs"
        if candidate.is_dir():
            return candidate
    return here.parent / "docs"


def load_chunks():
    """Split each doc into paragraphs; each paragraph is one chunk."""
    chunks = []
    docs_dir = find_docs_dir()
    for path in sorted(docs_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for para in text.split("\n\n"):
            para = para.strip()
            if para:
                chunks.append({"doc": path.name, "text": para})
    return chunks


CHUNKS = load_chunks()


def tokens(text):
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 2}


def retrieve(question, top_n=3):
    q_tokens = tokens(question)
    scored = []
    for chunk in CHUNKS:
        overlap = q_tokens & tokens(chunk["text"])
        if overlap:
            scored.append((len(overlap), chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [chunk for _, chunk in scored[:top_n]]


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS retrievals "
        "(id INTEGER PRIMARY KEY, question TEXT, docs_returned TEXT, "
        "trace_id TEXT, created_at REAL)"
    )
    existing = {row[1] for row in conn.execute("PRAGMA table_info(retrievals)")}
    if "trace_id" not in existing:
        conn.execute("ALTER TABLE retrievals ADD COLUMN trace_id TEXT")
    conn.commit()
    conn.close()


def log_retrieval(question, docs, trace_id=""):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO retrievals (question, docs_returned, trace_id, created_at) "
        "VALUES (?, ?, ?, ?)",
        (question, ",".join(docs), trace_id, time.time()),
    )
    conn.commit()
    conn.close()


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
        if self.path != "/retrieve":
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
        trace_id = str(data.get("trace_id") or self.headers.get("X-Trace-Id") or "")
        snippets = retrieve(question)
        log_retrieval(question, [s["doc"] for s in snippets], trace_id)
        self._send(
            200,
            {"question": question, "snippets": snippets, "trace_id": trace_id},
            trace_id=trace_id,
        )

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    init_db()
    print(f"{SERVICE_NAME} listening on port {PORT}, docs: {len(CHUNKS)} chunks")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
