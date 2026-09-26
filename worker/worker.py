import functools
import json
import os
import signal
import socket
import threading
import time
import uuid

import pika

import db
import health
import metrics
import storage
import webhook

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://admin:admin@localhost:5672/%2F")
TASK_TYPE = "llm_inference"
QUEUE = f"tasks.{TASK_TYPE}"          # topology comes from infra/rabbitmq/definitions.json
DEAD_EXCHANGE = "tasks.dlx"           # -> tasks.llm_inference.dlq
WORKER_ID = os.getenv("WORKER_ID", socket.gethostname())
MAX_CHARS = 24_000                    # ≈ 6,000 tokens; same limit as the API
MAX_ATTEMPTS = 3                      # then the task is failed and its message dead-lettered
TASK_TIMEOUT_S = int(os.getenv("TASK_TIMEOUT_S", "540"))  # < stop_grace_period (600s)
HEARTBEAT_EVERY_S = 5                 # must be well under db.STALE_AFTER_S


class PermanentError(Exception):
  """Retrying cannot help: missing text, empty text, text too long."""


def log(msg):
  print(f"[{WORKER_ID}] {msg}", flush=True)


health.start()  # probes answer 503 during the model load instead of "connection refused"
log("loading model...")
_t = time.time()
from summarizer import MODEL_PATH, summarize  # noqa: E402  (the import IS the model load)
log(f"model loaded in {time.time() - _t:.1f}s")
metrics.MODEL_LOAD.set(time.time() - _t)
health.model_loaded.set()
MODEL_NAME = os.getenv("MODEL_NAME") or os.path.splitext(os.path.basename(MODEL_PATH))[0].lower()


def process(params):
  text = params.get("text")
  if not isinstance(text, str):
    raise PermanentError("missing text")
  if not text.strip():
    raise PermanentError("text is empty")
  if len(text) > MAX_CHARS:
    raise PermanentError(f"text too long: {len(text)} chars, max {MAX_CHARS}")
  out = summarize(text, params.get("max_words", 150), deadline=time.time() + TASK_TIMEOUT_S)
  metrics.INFERENCE.labels(MODEL_NAME).observe(out["seconds"])
  metrics.TOKENS.labels(MODEL_NAME, "input").inc(out["input_tokens"])
  metrics.TOKENS.labels(MODEL_NAME, "output").inc(out["output_tokens"])
  if out["seconds"]:
    metrics.TOKENS_PER_SECOND.labels(MODEL_NAME).observe(out["output_tokens"] / out["seconds"])
  return {
    "model": MODEL_NAME,
    "summary": out["summary"],
    "input_tokens": out["input_tokens"],
    "output_tokens": out["output_tokens"],
    "seconds": out["seconds"],
    "tokens_per_second": round(out["output_tokens"] / out["seconds"], 2) if out["seconds"] else None,
  }


# --- channel operations: only ever run on the connection's thread -----------------

def ack(channel, delivery_tag, body, error):
  if channel.is_open:
    channel.basic_ack(delivery_tag)


def retry(channel, delivery_tag, body, error):
  """nack without requeue: the main queue dead-letters it to tasks.retry, where it
  waits out the TTL (30s) and is dead-lettered back to the main queue."""
  if channel.is_open:
    channel.basic_nack(delivery_tag, requeue=False)


def dead(channel, delivery_tag, body, error):
  """Keep a copy in the DLQ for inspection, then ack the original."""
  if channel.is_open:
    channel.basic_publish(
      exchange=DEAD_EXCHANGE,
      routing_key=TASK_TYPE,
      body=body,
      properties=pika.BasicProperties(
        delivery_mode=pika.DeliveryMode.Persistent,
        content_type="application/json",
        headers={"x-error": (error or "")[:500], "x-worker": WORKER_ID},
      ),
    )
    channel.basic_ack(delivery_tag)


# --- one task ---------------------------------------------------------------------

def heartbeat_loop(task_id, stop):
  while not stop.wait(HEARTBEAT_EVERY_S):
    try:
      db.heartbeat(task_id, WORKER_ID)
    except Exception as e:  # a missed beat is not fatal; STALE_AFTER_S covers a few
      log(f"heartbeat failed for {task_id}: {e!r}")


WEBHOOK_FIRST_RETRY_S = 60  # the API dispatcher's first retry; its schedule continues from there


def deliver_webhook(task_id, row):
  """One direct delivery attempt (2 quick tries). If the client is down, park the exact
  payload in object storage and let the API's dispatcher retry with backoff: the
  worker is never held up by a slow client, and the model never runs twice for it."""
  if not row["callback_url"]:
    return
  body = webhook.payload(task_id, row)
  error = webhook.send(row["callback_url"], body)
  try:
    if error is None:
      db.persistent(db.webhook_sent, task_id)
      metrics.WEBHOOK_DIRECT.labels("sent").inc()
      log(f"webhook {task_id}: sent")
      return
    try:
      ref = storage.put_json(f"webhooks/{task_id}.json", body)
    except Exception as e:
      db.persistent(db.webhook_failed, task_id, f"{error}; could not park payload: {e!r}")
      metrics.WEBHOOK_DIRECT.labels("failed").inc()
      log(f"webhook {task_id}: FAILED ({error}) and could not park it: {e!r}")
      return
    db.persistent(db.webhook_parked, task_id, ref, error, WEBHOOK_FIRST_RETRY_S)
    metrics.WEBHOOK_DIRECT.labels("parked").inc()
    log(f"webhook {task_id}: client unreachable ({error}); parked at {ref}, API retries in {WEBHOOK_FIRST_RETRY_S}s")
  except Exception as e:
    log(f"webhook {task_id}: could not record delivery state: {e!r}")


def fail(task_id, error):
  """Terminal failure: mark failed, notify, dead-letter the message."""
  row = db.persistent(db.finish, task_id, WORKER_ID, "failed", error=error)
  if row is None:
    log(f"{task_id}: lost ownership, not failing it")
    return "ack", None
  metrics.OUTCOMES.labels("failed").inc()
  deliver_webhook(task_id, row)
  return "dead", error


def run_task(task_id):
  """Returns (outcome, error): outcome is 'ack', 'retry' or 'dead'."""
  task = db.claim(task_id, WORKER_ID)

  if task is None:
    # Someone else owns it, it is already finished, or it doesn't exist.
    row = db.get(task_id)
    if row is None:
      log(f"{task_id}: unknown task, dropping message")
      metrics.OUTCOMES.labels("duplicate").inc()
      return "ack", None
    if row["status"] == "running" and row["alive"]:
      # A redelivery while the previous owner is still heartbeating (its RabbitMQ
      # connection dropped but the process lives). Look again after the retry delay:
      # by then that worker has finished, or its heartbeat is stale and we take over.
      log(f"{task_id}: still running elsewhere, checking again after the retry delay")
      metrics.OUTCOMES.labels("retry").inc()
      return "retry", None
    if (row["status"] in ("completed", "failed") and row["callback_url"]
        and row["webhook_sent_at"] is None and row["webhook_attempts"] == 0):
      # Previous worker finished the task but died before trying the webhook: send it now.
      # (If it had tried, delivery is already the API dispatcher's job.)
      log(f"{task_id}: already {row['status']}, sending the missing webhook")
      deliver_webhook(task_id, row)
      return "ack", None
    log(f"{task_id}: already {row['status']}, dropping duplicate message")
    metrics.OUTCOMES.labels("duplicate").inc()
    return "ack", None

  attempt = task["attempt"]
  if attempt > MAX_ATTEMPTS:
    # Only reachable when attempts crashed the worker itself (OOM, kill -9) or its
    # heartbeat was reaped: a handled error never claims beyond MAX_ATTEMPTS.
    log(f"failed {task_id}: attempt {attempt} > {MAX_ATTEMPTS}")
    return fail(task_id, f"gave up after {MAX_ATTEMPTS} attempts; last: {task.get('error') or 'worker lost'}")

  log(f"start {task_id} (attempt {attempt}/{MAX_ATTEMPTS})")
  if task["queue_wait_s"] is not None:
    metrics.QUEUE_WAIT.observe(max(task["queue_wait_s"], 0))
  metrics.BUSY.set(1)
  stop = threading.Event()
  beat = threading.Thread(target=heartbeat_loop, args=(task_id, stop), daemon=True)
  beat.start()
  try:
    result = process(task["parameters"])
  except PermanentError as e:
    log(f"failed {task_id}: {e} (permanent, no retry)")
    return fail(task_id, str(e))
  except Exception as e:
    # Transient, including anything unclassified (TimeoutError, OOM, bugs...).
    error = f"attempt {attempt}: {type(e).__name__}: {e}"
    if attempt >= MAX_ATTEMPTS:
      log(f"failed {task_id}: {error} (no attempts left)")
      return fail(task_id, error)
    log(f"retry {task_id}: {error} (next attempt in ~30s)")
    if not db.persistent(db.requeue_for_retry, task_id, WORKER_ID, error):
      log(f"{task_id}: lost ownership, dropping")
      return "ack", None
    metrics.OUTCOMES.labels("retry").inc()
    return "retry", error
  finally:
    stop.set()
    beat.join()
    metrics.BUSY.set(0)

  # Rides out a Postgres outage: the summary cost minutes of CPU, don't throw it away.
  row = db.persistent(db.finish, task_id, WORKER_ID, "completed", result=result)
  if row is None:
    log(f"{task_id}: lost ownership while running (heartbeat went stale), result discarded")
    return "ack", None
  log(f"done {task_id} in {result['seconds']}s")
  metrics.OUTCOMES.labels("completed").inc()
  deliver_webhook(task_id, row)
  return "ack", None


def handle(connection, channel, delivery_tag, body):
  """Runs in a worker thread. Never touches the channel directly."""
  def then(fn, error=None):
    try:
      connection.add_callback_threadsafe(functools.partial(fn, channel, delivery_tag, body, error))
    except pika.exceptions.AMQPError:
      # Connection died while we worked. RabbitMQ will redeliver; Postgres already has
      # the outcome, so the redelivery is recognized as a duplicate.
      log(f"connection gone, could not {fn.__name__} the message; it will be redelivered")

  try:
    task_id = uuid.UUID(json.loads(body)["task_id"])
  except (ValueError, KeyError, TypeError):
    log(f"malformed message, dead-lettering it: {body[:200]!r}")
    return then(dead, "malformed message")

  try:
    outcome, error = run_task(task_id)
  except Exception as e:
    # Postgres unreachable at claim time (or for longer than db.PERSIST_FOR_S):
    # don't lose the message, try again after the retry delay.
    log(f"{task_id}: infrastructure error {e!r}, retry in ~30s")
    metrics.OUTCOMES.labels("retry").inc()
    outcome, error = "retry", repr(e)

  # Everything durable is in Postgres now; only THEN settle the message.
  then({"ack": ack, "retry": retry, "dead": dead}[outcome], error)


def consume(stopping, current):
  """One connection's lifetime: consume until asked to stop or the connection drops.
  Always waits for the in-flight task before returning, so a reconnect never runs two
  generations at once."""
  connection = pika.BlockingConnection(pika.URLParameters(RABBITMQ_URL))
  channel = connection.channel()
  # passive: only check it exists. The topology is infrastructure (definitions.json);
  # a missing queue means a misconfigured broker, and we'd rather fail loudly.
  channel.queue_declare(queue=QUEUE, passive=True)
  channel.basic_qos(prefetch_count=1)  # one task at a time

  threads = []

  def on_message(ch, method, properties, body):
    threads[:] = [t for t in threads if t.is_alive()]  # forget finished threads
    t = threading.Thread(target=handle, args=(connection, ch, method.delivery_tag, body))
    t.start()
    threads.append(t)  # kept so we can join() the task still running

  channel.basic_consume(queue=QUEUE, on_message_callback=on_message, auto_ack=False)
  current["channel"] = channel
  if stopping.is_set():  # signal arrived while we were connecting
    return
  log(f"waiting for tasks on '{QUEUE}'")
  health.consuming.set()
  try:
    channel.start_consuming()
  finally:
    health.consuming.clear()
    current["channel"] = None
    for t in threads:  # the in-flight task always finishes (its result lands in Postgres)
      t.join()
    if connection.is_open:
      connection.process_data_events(time_limit=1)  # let its ack go out
      connection.close()


def main():
  db.open_pool()
  stopping = threading.Event()
  current = {"channel": None}

  def shutdown(signum, frame):
    log("SIGTERM/SIGINT received: stop taking new tasks, finishing the current one")
    stopping.set()
    if current["channel"] is not None:
      current["channel"].stop_consuming()
  signal.signal(signal.SIGTERM, shutdown)
  signal.signal(signal.SIGINT, shutdown)

  backoff = 1
  while not stopping.is_set():
    try:
      consume(stopping, current)
      backoff = 1
    except pika.exceptions.AMQPError as e:
      # Broker restarted, network blip... The unacked message (if any) is redelivered
      # by RabbitMQ; its task was already finished in Postgres, so the redelivery is
      # dropped as a duplicate.
      log(f"RabbitMQ connection lost ({type(e).__name__}), reconnecting in {backoff}s")
      stopping.wait(backoff)
      backoff = min(backoff * 2, 30)

  db.pool.close()
  log("bye")


if __name__ == "__main__":
  main()
