"""Every query the worker runs against the tasks table."""
import os

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://app:app@localhost:5433/tasks")

# A 'running' task whose heartbeat is older than this is considered abandoned
# (its worker died) and may be claimed again.
STALE_AFTER_S = 15

# 2 connections: one for the task itself, one for the heartbeat thread.
pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=2, open=False, kwargs={"row_factory": dict_row})


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
             started_at = now(), heartbeat_at = now(), error = NULL,
             -- the worker can win the race against the API's 'mark queued'
             queued_at = COALESCE(queued_at, now())
       WHERE id = %(id)s
         AND (status IN ('pending', 'queued')
              OR (status = 'running' AND heartbeat_at < now() - make_interval(secs => %(stale)s)))
      RETURNING task_type, parameters, callback_url, attempt
      """,
      {"id": task_id, "worker": worker_id, "stale": STALE_AFTER_S},
    ).fetchone()


def get(task_id):
  """Current state, used when a claim fails to decide what to do with the message."""
  with pool.connection() as conn:
    return conn.execute(
      """
      SELECT status, task_type, callback_url, attempt, result, error, completed_at, webhook_sent_at,
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


def record_webhook(task_id, error=None):
  with pool.connection() as conn:
    if error is None:
      conn.execute("UPDATE tasks SET webhook_sent_at = now(), webhook_error = NULL WHERE id = %s", (task_id,))
    else:
      conn.execute("UPDATE tasks SET webhook_error = %s WHERE id = %s", (error, task_id))
