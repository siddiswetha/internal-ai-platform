# Design document — Internal AI Platform (small)

## Problem

One tool that answers questions is easy. A platform that many people
can use, check, and trust is harder: the question has to travel
through several services, the answer has to come from real documents,
a failed service must not produce a made-up answer, and a bad change
must not reach everyone at once. This project is a small version of
that platform, built so each of those problems is visible and tested.

## Architecture

```
            POST /ask
  user ──────────────► question service (8001, gateway)
                          │  POST /retrieve (trace id)
                          ▼
                       retrieval service (8002)
                          │  top 3 snippets
                          ▼
                       answer service (8003)
                          │  POST /answer (trace id, style)
                          ▼
  user ◄────────────── answer + sources + run id + trace id
```

Each service is its own process, its own folder, its own Dockerfile,
and its own SQLite database file. The question service is the only
one the user talks to. It calls the other two over HTTP.

- Question service: gateway, feature flags, canary choice,
  idempotence (request id), trace id creation.
- Retrieval service: keyword search over the local docs in `docs/`,
  returns the top 3 paragraphs with document names.
- Answer service: drafts the answer only from the snippets, through
  the `LLMClient` interface, and logs every run.

A trace id is made for each request in the question service. It
travels in the JSON body and in the `X-Trace-Id` header to both
downstream services, appears in every JSON log line, and is stored
in each service's database row, so one request can be followed
across all three services.

## Data model

Each service owns one table in its own SQLite file. No service
reads another service's database; they only talk over HTTP.

- `questions.db`, table `questions`: id, run_id, request_id (unique),
  question, answer, sources (JSON), snippets (JSON), trace_id,
  created_at. One row per accepted question.
- `retrieval.db`, table `retrievals`: id, question, docs_returned,
  trace_id, created_at. One row per retrieval call.
- `runs.db`, table `runs`: id, run_id (unique), question,
  snippets_used (JSON), answer, trace_id, style, created_at. One row
  per answer. The unique run_id is what makes a retried answer
  idempotent: the same run_id returns the stored answer.

SQLite is the demo store. It is one file per service, it is local
only, and it is ignored by `.gitignore`. A production platform would
use a managed relational database with backups; the table shapes
above are the part that would carry over.

## Trade-offs

- **Latency.** The question service calls two services one after the
  other, so a request takes as long as both calls together. That is
  slower than answering in one place, but it keeps each service
  small and replaceable. The `/metrics` endpoint on each service
  reports average latency, so a slow service can be found.
- **Fault tolerance.** If the retrieval or answer service is down,
  the question service returns HTTP 502 with the trace id and logs
  the failure. It never invents an answer from nothing. There is no
  automatic retry here: a retry could double-log a run, and the
  request id is the safer way to retry — sending the same request
  id again returns the stored answer.
- **Concurrency.** Each service is a `ThreadingHTTPServer`, so it
  handles several requests at once. SQLite writes are short and
  one connection per call, so two writes do not share a connection.
  This is enough for a demo; a production service would use a
  connection pool and a database built for concurrent writes.
- **Security.** There is no authentication and no user data in this
  demo, on purpose. All docs are synthetic examples. The mock
  client sends nothing to any outside service. A real deployment
  would add authentication, access control, and a review before any
  real document is loaded. See "Data governance" in the README.
- **Consistency.** The three databases are not in one transaction.
  A question can be logged in the question service while its run is
  logged in the answer service. The trace id and run id are what tie
  the rows together afterwards, instead of a shared transaction.

## Safe deployment

- **Feature flags** live in `flags.json`: `answer_style` picks
  "short" or "detailed". The question service reads the file on
  each request, so changing the flag needs no restart. The
  `ANSWER_STYLE` environment variable overrides the file.
- **Canary:** the same file has a `canary` block (enabled, percent,
  new_style). When it is on, a share of trace ids — chosen by
  hashing the trace id — gets the new style, and the rest get the
  old style. `scripts/canary.py` turns the canary on for a small
  share, sends probe questions, checks the error rate, and either
  promotes the new style to everyone or rolls it back.
- **Testing:** unit tests cover retrieval ranking, answer drafting,
  and flag/canary choice. An integration test starts all three
  services and asks a question end to end, including a duplicate
  request id. The 12-case eval in `evals/` re-checks the whole
  platform. CI (`.github/workflows/ci.yml`) runs the unit tests and
  the eval on every push.

## What happens when a service is down

- Retrieval service down: the question service cannot get snippets,
  so it returns 502 "downstream service failed" with the trace id.
  Nothing is logged as an answer.
- Answer service down: same 502, no answer is invented, and the
  question is not logged as answered.
- Question service down: the user gets a connection error from the
  gateway. The other two services keep running, but nothing calls
  them, so no new runs are logged.
- One service restarts: its in-memory metrics reset to zero (the
  database rows do not), and it is healthy again as soon as
  `/health` answers.

## Future work (not done here)

Authentication and per-user limits; a managed database instead of
SQLite files; retries with backoff; a real language model behind
the `LLMClient` interface; more canary signals (latency and cost,
not just errors); and an on-call runbook for the 502 case.
