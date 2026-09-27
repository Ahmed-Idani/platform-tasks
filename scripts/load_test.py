#!/usr/bin/env python3
"""Load test: submit N tasks to the platform and record how it absorbs them.

    python3 scripts/load_test.py --count 1000 --out docs/report/material/load-1000

Submits the tasks concurrently (short texts, so the run takes minutes, not hours; a few use a
callback that fails, to exercise webhook parking), then samples every --every seconds until all
of them are finished:
    <out>.csv       t, workers (ready replicas), waiting, running, retry, dlq, completed, failed
    <out>.log       summary: submit rate, time to max workers, throughput, queue wait / duration percentiles
Needs the cluster from scripts/k8s.sh; kubectl is always called with --context k3d-platform.
"""
import argparse
import concurrent.futures as cf
import csv
import http.client
import json
import pathlib
import random
import subprocess
import time
import urllib.request

TEXTS = [
    "A message broker decouples the service that accepts work from the processes that execute it. "
    "The producer publishes and returns immediately, and consumers take messages at their own pace, "
    "so a burst of requests becomes a queue instead of a wall of timeouts.",
    "At-least-once delivery means a message is never lost but can arrive twice, for example after a "
    "consumer crashes before acknowledging it. The consumer must therefore be idempotent: processing "
    "the same message twice has to leave the system in the same state as processing it once.",
    "Kubernetes restarts a container whose liveness probe fails, removes a pod whose readiness probe "
    "fails from its service, and protects slow starters with a startup probe. A worker that loads a "
    "language model needs that startup probe, or it is killed while still loading.",
    "Scaling to zero saves resources when there is nothing to do, but the first request after an idle "
    "period pays a cold start: the pod has to be scheduled, the container started and the model "
    "loaded before the first task can run.",
    "A dead-letter queue collects messages that failed for good, so that a poison message stops "
    "looping and stays available for inspection. Retries with a delay, bounded to a few attempts, "
    "handle the transient failures before that.",
    "Prometheus scrapes metrics from every service at a fixed interval and evaluates alert rules on "
    "them. Grafana shows the same series on dashboards, next to the logs collected by Loki, so an "
    "operator can go from a symptom to its cause in one place.",
    "Webhooks push the result to the client as soon as it is ready, instead of making the client poll. "
    "When the client is down, the payload is kept in object storage and delivered again later with "
    "an increasing delay, without running the model a second time.",
    "PostgreSQL is the source of truth for every task. Each state change is a conditional update that "
    "only applies if the task is still in the expected state, so two workers racing for the same task "
    "cannot both win.",
]

CTX = ["--context", "k3d-platform", "-n", "platform"]


def retry(fn, tries=5):
  """A dropped connection (proxy keep-alive, pod restart) should not end a 45-minute run."""
  for i in range(tries):
    try:
      return fn()
    except (OSError, http.client.HTTPException):
      if i == tries - 1:
        raise
      time.sleep(2)


def post(base, body):
  req = urllib.request.Request(base + "/v1/tasks", data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"}, method="POST")
  with urllib.request.urlopen(req, timeout=30) as r:  # not retried: a lost response would create a duplicate
    return json.load(r)["task_id"]


def stats(base):
  def once():
    with urllib.request.urlopen(base + "/v1/stats", timeout=10) as r:
      return json.load(r)
  return retry(once)


def ready_workers():
  out = subprocess.run(["kubectl", *CTX, "get", "deploy", "worker", "-o", "jsonpath={.status.readyReplicas}"],
                       capture_output=True, text=True).stdout.strip()
  return int(out or 0)


def psql(sql):
  return subprocess.run(["kubectl", *CTX, "exec", "postgres-0", "--", "psql", "-U", "admin", "-d", "tasks",
                         "-At", "-F", " ", "-c", sql], capture_output=True, text=True).stdout.strip()


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--base", default="http://localhost:8088")
  ap.add_argument("--count", type=int, default=1000)
  ap.add_argument("--concurrency", type=int, default=20)
  ap.add_argument("--fail-every", type=int, default=100, help="every Nth task gets a callback that answers 503")
  ap.add_argument("--every", type=int, default=10, help="sampling interval in seconds")
  ap.add_argument("--out", default="load")
  a = ap.parse_args()
  out = pathlib.Path(a.out)
  out.parent.mkdir(parents=True, exist_ok=True)
  rnd = random.Random(42)

  while True:  # start from an idle platform: nothing in flight and 0 workers
    c = stats(a.base)["counts"]
    busy, n = sum(c.get(k, 0) for k in ("pending", "queued", "running")), ready_workers()
    if busy == 0 and n == 0:
      break
    print(f"waiting for an idle platform: {busy} tasks in flight, {n} workers", flush=True)
    time.sleep(15)
  base_done = sum(stats(a.base)["counts"].get(k, 0) for k in ("completed", "failed"))
  t0 = time.time()
  pathlib.Path("/tmp/claude-1000").mkdir(parents=True, exist_ok=True)
  pathlib.Path("/tmp/claude-1000/burst.start").write_text(str(int(t0)))  # Grafana screenshot window
  pathlib.Path("/tmp/claude-1000/burst.end").unlink(missing_ok=True)

  def body(i):
    hook = "http://inbox.platform:9000/" + ("fail/503" if a.fail_every and i % a.fail_every == 0 else "hook")
    return {"task_type": "llm_inference", "callback_url": hook,
            "parameters": {"text": rnd.choice(TEXTS), "max_words": rnd.choice([15, 20, 25, 30])}}

  bodies = [body(i) for i in range(1, a.count + 1)]
  with cf.ThreadPoolExecutor(a.concurrency) as ex:
    ids = list(ex.map(lambda b: post(a.base, b), bodies))
  submit_s = time.time() - t0

  rows, max_workers, t_max = [], 0, None
  with open(out.with_suffix(".csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["t", "workers", "waiting", "running", "retry", "dlq", "completed", "failed"])
    while True:
      s, n = stats(a.base), ready_workers()
      c, q = s["counts"], s["queues"]
      done = c.get("completed", 0) + c.get("failed", 0) - base_done
      row = [round(time.time() - t0), n, q["main"]["ready"], c.get("running", 0), q["retry"]["ready"],
             q["dlq"]["ready"], c.get("completed", 0), c.get("failed", 0)]
      w.writerow(row); f.flush(); rows.append(row)
      if n > max_workers:
        max_workers, t_max = n, row[0]
      print(f"t={row[0]:>5}s workers={n} waiting={row[2]:>4} running={row[3]} done={done}/{a.count}", flush=True)
      if done >= a.count and n == 0:
        break
      time.sleep(a.every)
  pathlib.Path("/tmp/claude-1000/burst.end").write_text(str(int(time.time())))

  total = time.time() - t0
  last_done = next(r[0] for r in rows if r[6] + r[7] - base_done >= a.count)
  idlist = ",".join(f"'{i}'" for i in ids)
  pct = psql(f"""SELECT round(percentile_cont(0.5) WITHIN GROUP (ORDER BY w)::numeric,1),
                        round(percentile_cont(0.95) WITHIN GROUP (ORDER BY w)::numeric,1),
                        round(max(w)::numeric,1),
                        round(percentile_cont(0.5) WITHIN GROUP (ORDER BY d)::numeric,1),
                        round(percentile_cont(0.95) WITHIN GROUP (ORDER BY d)::numeric,1),
                        count(*) FILTER (WHERE status='completed'), count(*) FILTER (WHERE status='failed'),
                        count(*) FILTER (WHERE attempt > 1), count(*) FILTER (WHERE webhook_sent_at IS NOT NULL),
                        count(*) FILTER (WHERE webhook_ref IS NOT NULL)
                   FROM (SELECT status, attempt, webhook_sent_at, webhook_ref,
                                extract(epoch FROM started_at - queued_at) w,
                                extract(epoch FROM completed_at - started_at) d
                           FROM tasks WHERE id IN ({idlist})) x""").split()
  summary = (f"tasks {a.count}, submitted in {submit_s:.1f} s ({a.count / submit_s:.0f} req/s)\n"
             f"max workers {max_workers}, reached at +{t_max} s\n"
             f"all tasks finished at +{last_done} s ({a.count / last_done * 60:.1f} tasks/min); back to 0 workers at +{round(total)} s\n"
             f"queue wait p50 {pct[0]} s, p95 {pct[1]} s, max {pct[2]} s\n"
             f"inference time p50 {pct[3]} s, p95 {pct[4]} s\n"
             f"completed {pct[5]}, failed {pct[6]}, retried (attempt > 1) {pct[7]}, "
             f"webhooks delivered {pct[8]}, webhooks parked {pct[9]}\n")
  out.with_suffix(".log").write_text(summary)
  print(summary)


if __name__ == "__main__":
  main()
