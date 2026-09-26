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
import webhook

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://app:app@localhost:5672/%2F")
QUEUE = "tasks.llm_inference"
WORKER_ID = os.getenv("WORKER_ID", socket.gethostname())
MAX_CHARS = 24_000        # ≈ 6,000 tokens; same limit as the API
MAX_ATTEMPTS = 3          # a task that keeps killing workers (OOM...) must stop somewhere
HEARTBEAT_EVERY_S = 5     # must be well under db.STALE_AFTER_S
REQUEUE_DELAY_S = 10      # before handing back a message we can't process yet


class PermanentError(Exception):
  """Retrying cannot help: missing text, empty text, text too long."""


def log(msg):
  print(f"[{WORKER_ID}] {msg}", flush=True)


log("loading model...")
_t = time.time()
from summarizer import MODEL_PATH, summarize  # noqa: E402  (the import IS the model load)
log(f"model loaded in {time.time() - _t:.1f}s")
MODEL_NAME = os.getenv("MODEL_NAME") or os.path.splitext(os.path.basename(MODEL_PATH))[0].lower()


def process(params):
  text = params.get("text")
  if not isinstance(text, str):
    raise PermanentError("missing text")
  if not text.strip():
    raise PermanentError("text is empty")
  if len(text) > MAX_CHARS:
    raise PermanentError(f"text too long: {len(text)} chars, max {MAX_CHARS}")
  out = summarize(text, params.get("max_words", 150))
  return {
    "model": MODEL_NAME,
    "summary": out["summary"],
    "input_tokens": out["input_tokens"],
    "output_tokens": out["output_tokens"],
    "seconds": out["seconds"],
    "tokens_per_second": round(out["output_tokens"] / out["seconds"], 2) if out["seconds"] else None,
  }


# --- channel operations: only ever run on the connection's thread -----------------

def ack(channel, delivery_tag):
  if channel.is_open:
    channel.basic_ack(delivery_tag)


def reject(channel, delivery_tag):
  if channel.is_open:
    channel.basic_reject(delivery_tag, requeue=False)


def requeue(channel, delivery_tag):
  if channel.is_open:
    channel.basic_nack(delivery_tag, requeue=True)


# --- one task ---------------------------------------------------------------------

def heartbeat_loop(task_id, stop):
  while not stop.wait(HEARTBEAT_EVERY_S):
    try:
      db.heartbeat(task_id, WORKER_ID)
    except Exception as e:  # a missed beat is not fatal; STALE_AFTER_S covers a few
      log(f"heartbeat failed for {task_id}: {e!r}")


def deliver_webhook(task_id, row):
  if not row["callback_url"]:
    return
  error = webhook.send(row["callback_url"], webhook.payload(task_id, row))
  db.record_webhook(task_id, error)
  log(f"webhook {task_id}: {'sent' if error is None else 'FAILED ' + error}")


def run_task(task_id):
  """Returns what to do with the message: 'ack' or 'requeue'."""
  task = db.claim(task_id, WORKER_ID)

  if task is None:
    # Someone else owns it, it is already finished, or it doesn't exist.
    row = db.get(task_id)
    if row is None:
      log(f"{task_id}: unknown task, dropping message")
      return "ack"
    if row["status"] == "running" and row["alive"]:
      # A redelivery while the previous owner is still heartbeating (e.g. its RabbitMQ
      # connection dropped but the process lives). Hand the message back and look again
      # later: either that worker finishes, or its heartbeat goes stale and we take over.
      log(f"{task_id}: still running elsewhere, requeue in {REQUEUE_DELAY_S}s")
      time.sleep(REQUEUE_DELAY_S)
      return "requeue"
    if row["status"] in ("completed", "failed") and row["callback_url"] and row["webhook_sent_at"] is None:
      # Previous worker finished the task but died before the webhook: send it now.
      log(f"{task_id}: already {row['status']}, sending the missing webhook")
      deliver_webhook(task_id, row)
      return "ack"
    log(f"{task_id}: already {row['status']}, dropping duplicate message")
    return "ack"

  if task["attempt"] > MAX_ATTEMPTS:
    row = db.finish(task_id, WORKER_ID, "failed", error=f"gave up after {MAX_ATTEMPTS} attempts")
    log(f"failed {task_id}: attempt {task['attempt']} > {MAX_ATTEMPTS}")
    if row:
      deliver_webhook(task_id, row)
    return "ack"

  log(f"start {task_id} (attempt {task['attempt']})")
  stop = threading.Event()
  beat = threading.Thread(target=heartbeat_loop, args=(task_id, stop), daemon=True)
  beat.start()
  try:
    result = process(task["parameters"])
    row = db.finish(task_id, WORKER_ID, "completed", result=result)
    log(f"done {task_id} in {result['seconds']}s")
  except PermanentError as e:
    row = db.finish(task_id, WORKER_ID, "failed", error=str(e))
    log(f"failed {task_id}: {e}")
  except Exception as e:
    # Unclassified = transient. No retry topology yet (Stage 4), so for now it is
    # recorded as failed instead of looping forever.
    row = db.finish(task_id, WORKER_ID, "failed", error=f"unexpected: {e!r}")
    log(f"crashed on {task_id}: {e!r}")
  finally:
    stop.set()
    beat.join()

  if row is None:
    log(f"{task_id}: lost ownership while running (heartbeat went stale), result discarded")
    return "ack"
  deliver_webhook(task_id, row)
  return "ack"


def handle(connection, channel, delivery_tag, body):
  """Runs in a worker thread. Never touches the channel directly."""
  def then(fn):
    connection.add_callback_threadsafe(functools.partial(fn, channel, delivery_tag))

  try:
    task_id = uuid.UUID(json.loads(body)["task_id"])
  except (ValueError, KeyError, TypeError):
    log(f"malformed message, dropping it: {body[:200]!r}")
    return then(reject)

  try:
    outcome = run_task(task_id)
  except Exception as e:
    # Postgres unreachable at claim/finish time: don't lose the message, retry later.
    log(f"{task_id}: infrastructure error {e!r}, requeue in {REQUEUE_DELAY_S}s")
    time.sleep(REQUEUE_DELAY_S)
    outcome = "requeue"

  # Everything durable is in Postgres now; only THEN ack.
  then(ack if outcome == "ack" else requeue)


def main():
  db.open_pool()

  connection = pika.BlockingConnection(pika.URLParameters(RABBITMQ_URL))
  channel = connection.channel()
  channel.queue_declare(queue=QUEUE, durable=True)
  channel.basic_qos(prefetch_count=1)  # one task at a time

  threads = []

  def on_message(ch, method, properties, body):
    threads[:] = [t for t in threads if t.is_alive()]  # forget finished threads
    t = threading.Thread(target=handle, args=(connection, ch, method.delivery_tag, body))
    t.start()
    threads.append(t)  # kept so shutdown can join() the task still running

  channel.basic_consume(queue=QUEUE, on_message_callback=on_message, auto_ack=False)

  def shutdown(signum, frame):
    log("SIGTERM/SIGINT received: stop taking new tasks, finishing the current one")
    channel.stop_consuming()
  signal.signal(signal.SIGTERM, shutdown)
  signal.signal(signal.SIGINT, shutdown)

  log(f"waiting for tasks on '{QUEUE}'")
  channel.start_consuming()

  # stop_consuming returned: wait for the in-flight task, then let its ack go out
  for t in threads:
    t.join()
  connection.process_data_events(time_limit=1)
  connection.close()
  db.pool.close()
  log("bye")


if __name__ == "__main__":
  main()
