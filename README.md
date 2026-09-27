# platform-tasks

Async compute-task platform: a Go API accepts tasks, RabbitMQ queues them, Python workers run LLM inference on CPU, Postgres tracks state, and the client gets a webhook when its task finishes.

## Architecture

```mermaid
flowchart LR
    client([Client / Web UI])

    subgraph platform [platform-tasks]
        api[Go API<br/>+ sweeper, reaper,<br/>webhook dispatcher]
        pg[(PostgreSQL<br/>tasks table)]
        mq[[RabbitMQ<br/>tasks exchange<br/>main / retry / DLQ]]
        worker[Python worker<br/>llama.cpp + Qwen3]
        s3[(Garage S3<br/>undelivered webhooks)]
    end

    client -- "POST /v1/tasks<br/>GET /v1/tasks/{id}" --> api
    api -- "INSERT / SELECT" --> pg
    api -- "publish {task_id}" --> mq
    mq -- "deliver (prefetch 1)" --> worker
    worker -- "claim, status, result" --> pg
    worker -. "POST callback_url" .-> client
    worker -- "client down: park payload" --> s3
    s3 -- "retry with backoff" --> api
    api -. "POST callback_url (later)" .-> client
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

## Diagrams

Hand-drawn with [Excalidraw](https://excalidraw.com). Each diagram has an editable `.excalidraw` source and `.svg`/`.png` exports in [`docs/report/diagrams/`](docs/report/diagrams/), generated from the Python scripts in [`docs/report/diagrams/src/`](docs/report/diagrams/src/) (`python3 docs/report/diagrams/render.py <name>`).

### Architecture

[![Architecture](docs/report/diagrams/architecture.png)](docs/report/diagrams/architecture.excalidraw)

### Deployment on Kubernetes

[![Deployment on Kubernetes](docs/report/diagrams/k8s-architecture.png)](docs/report/diagrams/k8s-architecture.excalidraw)

### Submitting and processing a task

[![Submitting and processing a task](docs/report/diagrams/submit-sequence.png)](docs/report/diagrams/submit-sequence.excalidraw)

### Task lifecycle (state machine)

[![Task lifecycle (state machine)](docs/report/diagrams/task-state-machine.png)](docs/report/diagrams/task-state-machine.excalidraw)

### A worker dies mid-task: redelivery and heartbeat takeover

[![A worker dies mid-task: redelivery and heartbeat takeover](docs/report/diagrams/worker-crash-takeover.png)](docs/report/diagrams/worker-crash-takeover.excalidraw)

### The publish gap and the sweeper

[![The publish gap and the sweeper](docs/report/diagrams/publish-gap-sweeper.png)](docs/report/diagrams/publish-gap-sweeper.excalidraw)

### RabbitMQ topology: retries and dead-lettering

[![RabbitMQ topology: retries and dead-lettering](docs/report/diagrams/rabbitmq-topology.png)](docs/report/diagrams/rabbitmq-topology.excalidraw)

### Worker: one message, three outcomes (ack / retry / dead)

[![Worker: one message, three outcomes (ack / retry / dead)](docs/report/diagrams/worker-decision-flow.png)](docs/report/diagrams/worker-decision-flow.excalidraw)

### Webhook delivery: park it, retry later, never recompute

[![Webhook delivery: park it, retry later, never recompute](docs/report/diagrams/webhook-delivery.png)](docs/report/diagrams/webhook-delivery.excalidraw)

### Data model

[![Data model](docs/report/diagrams/data-model.png)](docs/report/diagrams/data-model.excalidraw)

### Use cases

[![Use cases](docs/report/diagrams/use-case.png)](docs/report/diagrams/use-case.excalidraw)

### Development environment (scripts/dev.sh)

[![Development environment (scripts/dev.sh)](docs/report/diagrams/dev-environment.png)](docs/report/diagrams/dev-environment.excalidraw)

### Sprint planning

[![Sprint planning](docs/report/diagrams/sprints-planning.png)](docs/report/diagrams/sprints-planning.excalidraw)

## Layout

| Path | What |
|---|---|
| `api/` | Go REST API (`cmd/api` entrypoint, `internal/` packages) |
| `api/migrations/` | goose SQL migrations for Postgres |
| `worker/` | Python worker: consumes the queue, runs the model |
| `worker/models/` | GGUF model files (gitignored, copied into the image) |
| `web/` | React dashboard (Vite + TypeScript + Tailwind) |
| `deploy/k8s/` | Kubernetes manifests (Kustomize): StatefulSets, Deployments, Jobs, KEDA ScaledObject, Ingress |
| `deploy/monitoring/` | Prometheus/Grafana/Loki values, PodMonitors, alert rules, dashboard generator |
| `infra/` | Config for third-party services: RabbitMQ topology (`definitions.json`), Garage (`garage.toml`) |
| `scripts/` | `dev.sh` (whole stack), `webhook_receiver.py` (example client endpoint with an inbox page on :9000) |
| `docs/webhooks.md` | **Webhook contract**: what a client endpoint receives and must answer |
| `docs/report/` | Final report material: diagrams, screenshots, code images, French notes (see `INVENTORY.md`) |
| `testdata/` | Sample inputs |
| `docs/plan/` | Project context, build plan, TODO / decision log |
| `docs/guides/` | Step-by-step guides |

## Run on Kubernetes (k3s via k3d)

```bash
scripts/k8s.sh all          # cluster + images + platform + monitoring (first run: ~10 min)
scripts/k8s.sh build web    # rebuild one component, then: scripts/k8s.sh deploy
scripts/k8s.sh status
scripts/k8s.sh down
```

Everything is behind one address, **http://localhost:8088**, login **admin / admin**:

| Service | URL |
|---|---|
| Web UI | http://localhost:8088/ |
| Webhook inbox | http://localhost:8088/inbox/ (callback_url from the cluster: `http://inbox.platform:9000/hook`) |
| RabbitMQ | http://localhost:8088/rabbitmq/ |
| Grafana, "Platform tasks" dashboard | http://localhost:8088/grafana/d/platform-tasks/platform-tasks |
| Grafana logs (Loki) | http://localhost:8088/grafana/explore |
| Prometheus | http://localhost:8088/prometheus/ |
| Alertmanager | http://localhost:8088/alertmanager/ |

Workers scale on queue depth with KEDA, from 0 to 4.

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
| RabbitMQ UI | http://localhost:15672 (`admin` / `admin`) |
| Postgres | `localhost:5433` (`admin` / `admin`, db `tasks`) |
| Garage (S3) | http://localhost:3900 (bucket `platform-tasks-dev`) |
| API | http://localhost:8080 |
| Web UI | http://localhost:5173 |
| Webhook inbox (example client endpoint) | http://localhost:9000 |
