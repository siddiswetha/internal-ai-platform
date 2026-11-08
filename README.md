# Internal AI Platform (small)

A small internal AI platform, built from three small services that talk to each other. It is not one tool. It is a platform in small: one service takes the question, one finds the right local document, and one writes the answer and logs the run.

Ask a question in plain English. Get back an answer written only from the documents it found, with the names of those documents, and a run id. Every run is logged, so the platform can be checked later.

Built from scratch. Python standard library only — no external packages, no paid API key, no real model call. All data is synthetic and invented.

## What it is, in plain words

Think of a help desk with three people in a row.

1. **The front desk** (question service) takes your question. It does not answer it itself.
2. **The librarian** (retrieval service) searches the local documents and brings back the pages that match your question.
3. **The writer** (answer service) writes a short answer using only those pages, names them as sources, and writes the whole run down in a log.

If no document matches, the writer says so plainly instead of guessing.

## How it works

- **Question service, port 8001.** `POST /ask` with `{"question": "..."}`. It calls the retrieval service, then the answer service, and returns the answer, the sources, a run id, and a trace id. `GET /health` checks it is up. `GET /metrics` shows request and error counts and average latency. It keeps its own table of questions received.
- **Retrieval service, port 8002.** `POST /retrieve` with `{"question": "..."}`. It searches the plain-text documents in `docs/` by keyword overlap and returns the top 3 matching pieces, with the document name for each. `GET /health` checks it is up. `GET /metrics` shows request and error counts and average latency. It keeps its own table of retrieval requests.
- **Answer service, port 8003.** `POST /answer` with a question and snippets. It drafts a plain-English answer only from those snippets, with source citations. `GET /runs` lists the logged runs. `GET /health` checks it is up. `GET /metrics` shows request and error counts and average latency. It keeps its own table of runs: question, snippets used, answer, trace id, and style.

Each service is a separate folder, with its own code, its own database file, and its own Dockerfile. If one service is down, the question service reports that instead of making up an answer.

The answer is written through a small language-model interface (`LLMClient`, see `docs/prompts.md`). The default client is a deterministic rule-based mock. No real language model is called, and nothing here claims one was. A real model would plug into the same interface.

## Observability: logging, metrics, tracing

Every service writes one structured JSON log line per request, to
standard output. Each line has the time, the service name, the trace
id, the method, the path, the status, and the latency in milliseconds.
Example:

```
{"ts": "2026-11-08T05:00:00Z", "level": "INFO", "service": "question-service", "trace_id": "a1b2c3d4...", "event": "request", "method": "POST", "path": "/ask", "status": 200, "latency_ms": 12.4}
```

A trace id is made for each request in the question service. It
travels in the JSON body and in the `X-Trace-Id` header to both
downstream services, appears in every log line, and is stored in
each service's database row and in the run record, so one request
can be followed across all three services. The CLI prints the trace
id for every answer.

Each service also has a `GET /metrics` endpoint with request count,
error count, average latency, and uptime in seconds:

```
curl http://127.0.0.1:8001/metrics
curl http://127.0.0.1:8002/metrics
curl http://127.0.0.1:8003/metrics
```

## Idempotence and fault tolerance

- **Idempotence.** Send a `request_id` with `/ask` (or an
  `Idempotency-Key` header). The question service stores the answer
  with that id. Sending the same id again returns the stored answer
  with `"duplicate": true` and does not call the downstream services
  again, so a retried request cannot create a second run. The
  answer service does the same for `run_id`: the same run id
  returns the stored answer and is logged only once.
- **Fault tolerance.** If the retrieval or answer service is down,
  the question service returns HTTP 502 with the trace id and logs
  the failure. It never invents an answer from nothing. See
  `docs/design.md` for what happens in each failure case.

## Feature flags and canary releases

`flags.json` picks the answer style (`"short"` or `"detailed"`).
The question service reads the file on each request, so changing
the flag needs no restart. The `ANSWER_STYLE` environment variable
overrides the file.

The same file has a `canary` block. When it is on, a share of
requests — chosen by hashing the trace id — gets the new style, and
the rest get the old style. `scripts/canary.py` runs a real canary:
it turns the canary on for a small share, sends probe questions,
checks the error rate, and either promotes the new style to
everyone or rolls it back.

```
python3 scripts/canary.py --percent 10 --probes 10 --new-style detailed
```

## Testing and CI

Unit tests (Python `unittest`, standard library only) cover
retrieval ranking, answer drafting, the language-model interface,
and flag/canary choice. An integration test starts all three
services and asks a question end to end, including a duplicate
request id and a downstream service going down.

```
python3 -m unittest discover -s tests -v
```

`.github/workflows/ci.yml` runs the same tests, plus the 12-case
eval, on every push. It needs no secrets and no outside services.

## Developer tooling: the CLI

`cli.py` is the small command-line tool for the platform:

```
python3 cli.py ask "When should vendor invoices be paid?"
python3 cli.py ask "What is the close checklist?" --request-id my-run-1
python3 cli.py health
python3 cli.py runs
python3 cli.py metrics
```

The services must be running first (`python3 run_local.py`, or
`docker compose up`).

## Kubernetes

`k8s/` has plain yaml manifests for the 3 services: one Deployment
per service (with liveness and readiness probes on `/health`) and
one Service per service, so they reach each other by name inside
the cluster. They are not applied to a real cluster here — there is
no cluster in this project. To use them, build the 3 images from
the Dockerfiles, push them to a registry, replace the image names,
then run `kubectl apply -f k8s/`.

## Data governance

- All data is synthetic and invented. The docs in `docs/` describe
  no real company, vendor, person, or transaction.
- No real member data, no sensitive data, and no personal data is
  ever loaded, stored, or sent anywhere.
- The mock answer client sends nothing to any outside service. A
  real language model plugged in later would still have to follow
  the same guardrails: only the retrieved snippets, sources named,
  and every run logged.
- The database files are local only, ignored by `.gitignore`, and
  must not be uploaded to a shared repository.
- A human should check the logged runs before trusting the answers
  in a real setting. The platform does not treat an answer as
  correct on its own.

## How to run

### With Docker (one command)

```
docker compose up --build
```

Then ask a question:

```
curl -X POST http://127.0.0.1:8001/ask -H "Content-Type: application/json" \
  -d '{"question": "When should vendor invoices be paid?"}'
```

Stop with Ctrl+C, then `docker compose down`.

### Without Docker

```
python3 run_local.py
```

That starts all three services on ports 8001, 8002, and 8003. Ask a question in another terminal with the same curl command as above. Stop with Ctrl+C.

You can also start each service on its own:

```
PORT=8001 python3 services/question-service/main.py
PORT=8002 python3 services/retrieval-service/main.py
PORT=8003 python3 services/answer-service/main.py
```

Requirements: Python 3.10+ only. Docker only if you use the Docker steps.

Check each service is up:

```
curl http://127.0.0.1:8001/health
curl http://127.0.0.1:8002/health
curl http://127.0.0.1:8003/health
```

See the logged runs:

```
curl http://127.0.0.1:8003/runs
```

## Project layout

```
internal-ai-platform/
├── README.md
├── docker-compose.yml
├── flags.json                   # answer style + canary settings
├── .gitignore
├── run_local.py                 # starts all 3 services without Docker
├── cli.py                       # ask / health / runs / metrics
├── docs/
│   ├── month-end-close-checklist.md
│   ├── vendor-payment-policy.md
│   ├── reporting-guide.md
│   ├── expense-policy.md        # all synthetic, invented examples
│   ├── prompts.md               # prompt templates + guardrails
│   └── design.md                # technical design document
├── scripts/
│   └── canary.py                # canary rollout for the answer style
├── tests/
│   ├── test_retrieval.py        # unit tests: retrieval ranking
│   ├── test_answer.py           # unit tests: answer drafting + LLM interface
│   ├── test_flags.py            # unit tests: flags + canary choice
│   └── test_integration.py      # integration test: end-to-end ask
├── .github/workflows/ci.yml     # unit tests + eval on every push
├── k8s/
│   ├── deployment.yaml          # 3 Deployments, health probes
│   └── service.yaml             # 3 Services
├── services/
│   ├── question-service/
│   │   ├── main.py
│   │   └── Dockerfile
│   ├── retrieval-service/
│   │   ├── main.py
│   │   └── Dockerfile
│   └── answer-service/
│       ├── main.py
│       └── Dockerfile
├── evals/
│   ├── eval-set.csv             # 12 test cases
│   ├── eval.py                  # runs the eval set against the real services
│   └── eval-results.csv         # true results from the last eval run
└── output/                      # eval output, if any
```

The design document is `docs/design.md`: problem, architecture,
data model, trade-offs (latency, fault tolerance, concurrency,
security), safe deployment, and what happens when a service is down.

The database files (`questions.db`, `retrieval.db`, `runs.db`) are generated when the services run. They are local only, ignored by `.gitignore`, and must not be uploaded to a shared repository. Rebuild them any time by deleting them and restarting the services.

## The documents (synthetic)

All four documents in `docs/` are invented examples, written for this project. They describe no real company, vendor, person, or transaction.

- Month-end close checklist: reconcile the ledger, check invoices, get a second review, then lock the numbers.
- Vendor payment policy: submit an invoice with a purchase order, paid within 30 days, approval over $1,000.
- Monthly reporting guide: what a monthly report includes, and how dashboards are built from the same numbers.
- Travel expense policy: keep receipts, $500 limit for team-lead approval, submit within 14 days.

## Eval pass bar

**Pass bar: 12/12 tests pass.**

Run it with:

```
python3 evals/eval.py
```

The eval starts all three services, asks each of the 12 questions through the question service, and checks three things for each one: the right service answered, the right document was retrieved, and the run was logged in the answer service. The last question asks about something in none of the documents, and the correct answer is to say so.

`evals/eval.py` writes the true result of each test into `evals/eval-results.csv` and into the `result` column of `evals/eval-set.csv`. A failed test is recorded as failed, never edited to look like a pass.

## Risks and guardrails

- **A wrong document could lead to a wrong answer.** Guardrail: the answer uses only the retrieved snippets and names them, so a human can check the source.
- **The answer could invent detail.** Guardrail: the mock writer only repeats what is in the snippets. If there are no snippets, it says it does not have the information.
- **A service could be down.** Guardrail: the question service reports the failure instead of answering from nothing.
- **Real data could leak.** Guardrail: all data here is synthetic and labelled as such. Never point this demo at real documents without access control and a review first.
- **This is a demo, not production software.** It has no authentication, no user accounts, and no handling for many users at once. Those are listed as future work, not silently assumed.

## Human check

The answer service logs every run, with the question, the snippets used, and the answer. A human should check the logged runs before trusting the answers in a real setting. The platform does not treat an answer as correct on its own — the sources and the log are there so a person can check it.

## How this fits Swetha's actual background

Author: Venkata Swetha Siddi. MS Computer Science, Campbellsville University, May 2024. Data Scientist, Cognizant, Dec 2019–Apr 2022, with end-to-end machine learning, evaluation, and dashboards. Network Engineer, Relic Solutions, Oct 2025–Feb 2026. Skills include Python, SQL, Docker, Kubernetes, prompt engineering, and LLM fine-tuning with evaluation harnesses.
