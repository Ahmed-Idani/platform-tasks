"""Every query the worker runs against the tasks table."""
import os
import time

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://admin:admin@localhost:5433/tasks")

# A 'running' task whose heartbeat is older than this is considered abandoned
# (its worker died) and may be claimed again.
STALE_AFTER_S = 15

# 2 connections: one for the task itself, one for the heartbeat thread.
pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=2, open=False, timeout=5,
                      kwargs={"row_factory": dict_row})

# How long to keep retrying a write that must not be lost (a finished result) while
# Postgres is unreachable. Longer than a restart or a failover; shorter than the
# reaper's staleness threshold would matter, since the heartbeat resumes with Postgres.
PERSIST_FOR_S = 120


def persistent(fn, *args, **kwargs):
  """Run a DB call, retrying through a Postgres outage for up to PERSIST_FOR_S.

  Used for writes whose loss would throw away real work (the generated summary).
  Raises the last error if Postgres doesn't come back in time.
  """
  give_up = time.time() + PERSIST_FOR_S
  wait = 1
  while True:
    try:
      return fn(*args, **kwargs)
    except (psycopg.OperationalError, psycopg.errors.AdminShutdown, TimeoutError) as e:
      if time.time() + wait > give_up:
        raise
      print(f"postgres unavailable ({type(e).__name__}), retrying in {wait}s", flush=True)
      time.sleep(wait)
      wait = min(wait * 2, 10)


def open_pool():
  pool.open(wait=True, timeout=30)


def claim(task_id, worker_id):
  """The ownership claim. Returns the task row if we now own it, else None.

  Conditional, never blind: it only succeeds if the task is waiting (pending/queued),
  or 'running' but abandoned (heartbeat stale = its worker died mid-task).
  """
  with pool.connection() as conn:
    return conn.execute(
      """
      UPDATE tasks
         SET status = 'running', worker_id = %(worker)s, attempt = attempt + 1,
             started_at = now(), heartbeat_at = now(),   -- error kept: "attempt 1 failed: ..." stays visible
             -- the worker can win the race against the API's 'mark queued'
             queued_at = COALESCE(queued_at, now())
       WHERE id = %(id)s
         AND (status IN ('pending', 'queued')
              OR (status = 'running' AND heartbeat_at < now() - make_interval(secs => %(stale)s)))
      RETURNING task_type, parameters, callback_url, attempt, error,
                extract(epoch FROM now() - queued_at)::float AS queue_wait_s
      """,
      {"id": task_id, "worker": worker_id, "stale": STALE_AFTER_S},
    ).fetchone()


def get(task_id):
  """Current state, used when a claim fails to decide what to do with the message."""
  with pool.connection() as conn:
    return conn.execute(
      """
      SELECT status, task_type, callback_url, attempt, result, error, completed_at,
             webhook_sent_at, webhook_ref, webhook_attempts,
             heartbeat_at > now() - make_interval(secs => %(stale)s) AS alive
        FROM tasks WHERE id = %(id)s
      """,
      {"id": task_id, "stale": STALE_AFTER_S},
    ).fetchone()


def heartbeat(task_id, worker_id):
  with pool.connection() as conn:
    conn.execute(
      "UPDATE tasks SET heartbeat_at = now() WHERE id = %s AND worker_id = %s AND status = 'running'",
      (task_id, worker_id),
    )


def finish(task_id, worker_id, status, result=None, error=None):
  """running -> completed/failed. Returns the finished row, or None if we no longer
  own the task (our heartbeat went stale and another worker reclaimed it)."""
  with pool.connection() as conn:
    return conn.execute(
      """
      UPDATE tasks
         SET status = %(status)s, result = %(result)s, error = %(error)s,
             completed_at = now(), heartbeat_at = now()
       WHERE id = %(id)s AND worker_id = %(worker)s AND status = 'running'
      RETURNING status, task_type, callback_url, attempt, result, error, completed_at
      """,
      {"id": task_id, "worker": worker_id, "status": status,
       "result": Jsonb(result) if result is not None else None, "error": error},
    ).fetchone()


def requeue_for_retry(task_id, worker_id, error):
  """running -> queued after a transient failure. The message then waits in the
  retry queue (TTL) before coming back. Returns False if we no longer own the task."""
  with pool.connection() as conn:
    return conn.execute(
      """
      UPDATE tasks
         SET status = 'queued', queued_at = now(), heartbeat_at = NULL, error = %(error)s
       WHERE id = %(id)s AND worker_id = %(worker)s AND status = 'running'
      RETURNING id
      """,
      {"id": task_id, "worker": worker_id, "error": error},
    ).fetchone() is not None


def webhook_sent(task_id):
  with pool.connection() as conn:
    conn.execute(
      """UPDATE tasks SET webhook_sent_at = now(), webhook_error = NULL,
                          webhook_attempts = webhook_attempts + 1
          WHERE id = %s""",
      (task_id,),
    )


def webhook_parked(task_id, ref, error, retry_in_s):
  """Direct delivery failed: payload is in object storage, the API dispatcher takes over."""
  with pool.connection() as conn:
    conn.execute(
      """UPDATE tasks SET webhook_attempts = webhook_attempts + 1, webhook_error = %(error)s,
                          webhook_ref = %(ref)s, webhook_next_at = now() + make_interval(secs => %(in)s)
          WHERE id = %(id)s""",
      {"id": task_id, "ref": ref, "error": error, "in": retry_in_s},
    )


def webhook_failed(task_id, error):
  """Delivery failed AND the payload couldn't be parked: nothing will retry it."""
  with pool.connection() as conn:
    conn.execute(
      "UPDATE tasks SET webhook_attempts = webhook_attempts + 1, webhook_error = %s WHERE id = %s",
      (error, task_id),
    )
