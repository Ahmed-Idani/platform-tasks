# platform-tasks

Async compute-task platform: a Go API accepts tasks, RabbitMQ queues them, Python workers run LLM inference on CPU, Postgres tracks state, and the client gets a webhook when its task finishes.

## Architecture

```mermaid
flowchart LR
    client([Client / Web UI])

    subgraph platform [platform-tasks]
        api[Go API]
        pg[(PostgreSQL<br/>tasks table)]
        mq[[RabbitMQ<br/>tasks.llm_inference]]
        worker[Python worker<br/>llama.cpp + Qwen3]
    end

    client -- "POST /v1/tasks<br/>GET /v1/tasks/{id}" --> api
    api -- "INSERT / SELECT" --> pg
    api -- "publish {task_id}" --> mq
    mq -- "deliver (prefetch 1)" --> worker
    worker -- "claim, status, result" --> pg
    worker -. "POST callback_url" .-> client
```

## Task lifecycle

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant A as Go API
    participant P as PostgreSQL
    participant R as RabbitMQ
    participant W as Worker

    C->>A: POST /v1/tasks
    A->>P: INSERT (status = pending)
    A->>R: publish {task_id} (wait for confirm)
    A->>P: UPDATE status = queued
    A-->>C: 202 {task_id, status}

    R->>W: deliver message
    W->>P: conditional claim → running
    Note over W: summarize (minutes)
    W->>P: UPDATE completed / failed + result
    W->>C: POST callback_url (webhook)
    W->>R: ack
```

## States

```mermaid
stateDiagram-v2
    [*] --> pending: API insert
    pending --> queued: published
    pending --> queued: sweeper republish
    queued --> running: worker claim
    running --> completed
    running --> failed
    running --> queued: retry (attempt < 3)
    pending --> cancelled
    queued --> cancelled
    completed --> [*]
    failed --> [*]
    cancelled --> [*]
```

## Layout

| Path | What |
|---|---|
| `api/` | Go REST API (`cmd/api` entrypoint, `internal/` packages) |
| `api/migrations/` | goose SQL migrations for Postgres |
| `worker/` | Python worker: consumes the queue, runs the model |
| `worker/models/` | GGUF model files (gitignored, copied into the image) |
| `web/` | React dashboard (Vite + TypeScript + Tailwind) |
| `infra/` | Config for third-party services (`rabbitmq.conf`, …) |
| `scripts/` | `dev.sh` (whole stack), `webhook_receiver.py` (prints callbacks) |
| `testdata/` | Sample inputs |
| `docs/plan/` | Project context, build plan, TODO / decision log |
| `docs/guides/` | Step-by-step guides |

## Run locally

```bash
scripts/dev.sh                  # everything: infra, migrations, API (hot reload), web, webhook receiver, worker
scripts/dev.sh --worker local   # worker from worker/.venv instead of Docker (N_THREADS=4)
scripts/dev.sh --worker none    # no worker, tasks stay queued
scripts/dev.sh --keep-infra     # Ctrl+C leaves the Docker containers running
scripts/dev.sh down             # stop everything
```

**Ctrl+C stops everything** (processes and containers). Data is kept in Docker volumes.

| Service | URL |
|---|---|
| RabbitMQ UI | http://localhost:15672 (`app` / `app`) |
| Postgres | `localhost:5433` (`app` / `app`, db `tasks`) |
| API | http://localhost:8080 |
| Web UI | http://localhost:5173 |
