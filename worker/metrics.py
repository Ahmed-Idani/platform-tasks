"""Worker metrics (Prometheus), served on /metrics by health.py.

Each worker exposes its own counters; PromQL sums them across pods.
"""
from prometheus_client import Counter, Gauge, Histogram

# From queued_at to the claim: what a task waits for a free worker. With scale-to-zero
# (Stage 7) this is where the cold start shows up.
QUEUE_WAIT = Histogram(
  "task_queue_wait_seconds", "Time from queued to claimed by a worker.",
  buckets=(0.1, 0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300, 600, 1800),
)
INFERENCE = Histogram(
  "inference_duration_seconds", "Time spent inside the model per task (prefill + generation).",
  ["model"], buckets=(1, 2, 5, 10, 20, 30, 60, 90, 120, 180, 300, 450, 600),
)
TOKENS = Counter("inference_tokens_total", "Tokens processed, input (prompt) and output (generated).", ["model", "kind"])
TOKENS_PER_SECOND = Histogram(
  "inference_tokens_per_second", "Generated tokens per second of inference, per task.",
  ["model"], buckets=(0.25, 0.5, 1, 2, 4, 8, 16, 32, 64),
)
# One increment per processed message, by how it was settled.
OUTCOMES = Counter(
  "worker_task_outcomes_total",
  "Messages settled by the worker: completed, failed (permanent or out of attempts), retry, duplicate.",
  ["outcome"],
)
BUSY = Gauge("worker_busy", "1 while this worker is running a task.")
MODEL_LOAD = Gauge("worker_model_load_seconds", "How long this worker took to load the model at startup.")
WEBHOOK_DIRECT = Counter(
  "worker_webhook_total", "Direct webhook attempts by the worker, by result (sent, parked, failed).", ["result"],
)
