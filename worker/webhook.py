"""POST the finished task to the client's callback_url."""
import json
import time
import urllib.error
import urllib.request

TIMEOUT_S = 10
BACKOFF_S = [1]  # 2 quick tries; after that the payload is parked and the API dispatcher retries


def payload(task_id, row):
  return {
    "event": "task.finished",
    "task_id": str(task_id),
    "task_type": row["task_type"],
    "status": row["status"],
    "attempt": row["attempt"],
    "result": row["result"],
    "error": row["error"],
    "completed_at": row["completed_at"].isoformat(),
  }


def send(url, body):
  """Returns None on success (any 2xx), else the last error as a string."""
  data = json.dumps(body).encode()
  last_error = None
  for wait in [0, *BACKOFF_S]:
    time.sleep(wait)
    req = urllib.request.Request(url, data=data, method="POST", headers={
      "Content-Type": "application/json",
      "User-Agent": "platform-tasks-worker",
      "X-Task-Id": body["task_id"],
    })
    try:
      with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        if 200 <= resp.status < 300:
          return None
        last_error = f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:  # 4xx/5xx
      last_error = f"HTTP {e.code}"
    except Exception as e:  # DNS, refused, timeout...
      last_error = f"{type(e).__name__}: {e}"
  return last_error
