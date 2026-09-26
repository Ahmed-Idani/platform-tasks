"""Generates platform.json, the Grafana dashboard for the task platform.

    python3 deploy/monitoring/dashboards/platform.py

Rows answer, top to bottom: is it healthy right now? how long do tasks wait? what does a
task cost? what fails? are the workers sized right? what do the logs say?
"""
import json
import pathlib

PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
Q = 'queue="tasks.llm_inference"'
QR = 'queue="tasks.llm_inference.retry"'
QD = 'queue="tasks.llm_inference.dlq"'

panels = []
_id = 0
y = 0


def nid():
    global _id
    _id += 1
    return _id


def row(title):
    global y
    panels.append({"type": "row", "title": title, "id": nid(), "collapsed": False,
                   "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}})
    y += 1


def target(expr, legend="", ref="A"):
    return {"datasource": PROM, "expr": expr, "legendFormat": legend, "refId": ref}


def stat(title, expr, x, w=4, h=4, unit="short", thresholds=None, desc=""):
    steps = thresholds or [{"color": "green", "value": None}]
    panels.append({
        "type": "stat", "title": title, "id": nid(), "datasource": PROM, "description": desc,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [target(expr)],
        "options": {"colorMode": "background", "graphMode": "area", "reduceOptions": {"calcs": ["lastNotNull"]}},
        "fieldConfig": {"defaults": {"unit": unit, "decimals": 0,
                                     "thresholds": {"mode": "absolute", "steps": steps}}, "overrides": []},
    })


def ts(title, targets, x, w=12, h=8, unit="short", desc="", stack=False, overrides=None):
    panels.append({
        "type": "timeseries", "title": title, "id": nid(), "datasource": PROM, "description": desc,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [target(e, l, chr(65 + i)) for i, (e, l) in enumerate(targets)],
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
        "fieldConfig": {"defaults": {"unit": unit, "custom": {
            "lineWidth": 2, "fillOpacity": 12, "showPoints": "never",
            "stacking": {"mode": "normal" if stack else "none"}}}, "overrides": overrides or []},
    })


def color(name, c):
    return {"matcher": {"id": "byName", "options": name},
            "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]}


RED = [{"color": "green", "value": None}, {"color": "red", "value": 1}]
AMBER = [{"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 20}]

# ---- Now ---------------------------------------------------------------------------
row("Now: is the platform healthy?")
stat("Waiting in queue", f"max(rabbitmq_detailed_queue_messages_ready{{{Q}}})", 0, thresholds=AMBER,
     desc="Messages in tasks.llm_inference not yet taken by a worker.")
stat("Being processed", f"max(rabbitmq_detailed_queue_messages_unacked{{{Q}}})", 4,
     desc="Delivered to a worker, not acked yet (= tasks running).")
stat("In retry delay", f"max(rabbitmq_detailed_queue_messages_ready{{{QR}}})", 8,
     thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 1}],
     desc="Transient failures waiting out the 30 s TTL in tasks.llm_inference.retry.")
stat("Dead-lettered", f"max(rabbitmq_detailed_queue_messages_ready{{{QD}}})", 12, thresholds=RED,
     desc="Failed for good (permanent error or 3 attempts). Should be 0.")
stat("Workers consuming", f"max(rabbitmq_detailed_queue_consumers{{{Q}}})", 16,
     thresholds=[{"color": "red", "value": None}, {"color": "green", "value": 1}])
stat("Completed (1 h)", 'sum(increase(worker_task_outcomes_total{outcome="completed"}[1h]))', 20)
y += 4

ts("Queue depth", [
    (f"max(rabbitmq_detailed_queue_messages_ready{{{Q}}})", "waiting"),
    (f"max(rabbitmq_detailed_queue_messages_unacked{{{Q}}})", "running"),
    (f"max(rabbitmq_detailed_queue_messages_ready{{{QR}}})", "retry delay"),
    (f"max(rabbitmq_detailed_queue_messages_ready{{{QD}}})", "dead-lettered"),
], 0, overrides=[color("dead-lettered", "red"), color("retry delay", "orange")],
   desc="RabbitMQ per-queue depth. KEDA scales workers on 'waiting' (Stage 7).")
ts("Tasks by status (Postgres)", [
    ('max by (status) (platform_tasks{status=~"pending|queued|running"})', "{{status}}"),
], 12, desc="From the tasks table at scrape time (every API replica reports it: max, not sum).")
y += 8

# ---- Latency -----------------------------------------------------------------------
row("Latency: how long do tasks wait and run?")
ts("Queue wait (queued → claimed)", [
    ("histogram_quantile(0.50, sum by (le) (rate(task_queue_wait_seconds_bucket[5m])))", "p50"),
    ("histogram_quantile(0.95, sum by (le) (rate(task_queue_wait_seconds_bucket[5m])))", "p95"),
], 0, unit="s", desc="The headline metric: what a task waits for a free worker. "
                      "With scale-to-zero, the cold start (pod scheduling + model load) shows up here.")
ts("Inference time per task", [
    ("histogram_quantile(0.50, sum by (le) (rate(inference_duration_seconds_bucket[5m])))", "p50"),
    ("histogram_quantile(0.95, sum by (le) (rate(inference_duration_seconds_bucket[5m])))", "p95"),
], 12, unit="s", desc="Time inside the model (prefill + generation).")
y += 8

# ---- Cost --------------------------------------------------------------------------
row("Cost: tokens and throughput")
ts("Tasks settled per minute, by outcome", [
    ("sum by (outcome) (rate(worker_task_outcomes_total[5m])) * 60", "{{outcome}}"),
], 0, w=8, unit="short", stack=True,
   overrides=[color("completed", "green"), color("failed", "red"), color("retry", "orange"), color("duplicate", "blue")])
ts("Tokens per second (per task)", [
    ("histogram_quantile(0.50, sum by (le) (rate(inference_tokens_per_second_bucket[10m])))", "p50"),
    ("sum(rate(inference_tokens_total{kind=\"output\"}[5m]))", "platform output tokens/s"),
], 8, w=8, desc="Generation speed: the cost-per-task proxy on CPU.")
ts("Tokens processed per minute", [
    ("sum by (kind) (rate(inference_tokens_total[5m])) * 60", "{{kind}}"),
], 16, w=8, stack=True)
y += 8

# ---- Failures ----------------------------------------------------------------------
row("Failures, retries and webhooks")
ts("Webhook deliveries per hour", [
    ("sum by (result) (increase(worker_webhook_total[1h]))", "worker: {{result}}"),
    ("sum by (result) (increase(api_webhook_dispatch_total[1h]))", "dispatcher: {{result}}"),
], 0, w=8, desc="worker: sent / parked (client down) / failed. dispatcher: delivered / retry / gave_up.")
ts("Repair loops (per hour)", [
    ("sum(increase(api_sweeper_republished_total[1h]))", "sweeper: republished pending"),
    ("sum(increase(api_reaper_requeued_total[1h]))", "reaper: requeued stale running"),
    ("sum(increase(api_publish_failures_total[1h]))", "API publish failures"),
], 8, w=8, desc="Non-zero means something went wrong and was repaired.")
ts("API requests", [
    ('sum by (route) (rate(api_http_requests_total{route!~".*(metrics|healthz)"}[5m]))', "{{route}}"),
], 16, w=8, unit="reqps")
y += 8

# ---- Resources ---------------------------------------------------------------------
row("Resources: are the workers sized right?")
ts("Worker CPU (cores) vs limit", [
    ('sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="platform",container="worker"}[2m]))', "{{pod}}"),
    ('max(kube_pod_container_resource_limits{namespace="platform",container="worker",resource="cpu"})', "limit"),
], 0, w=8, overrides=[color("limit", "red")], desc="Inference saturates its CPU limit; N_THREADS = limit.")
ts("Worker memory vs limit", [
    ('sum by (pod) (container_memory_working_set_bytes{namespace="platform",container="worker"})', "{{pod}}"),
    ('max(kube_pod_container_resource_limits{namespace="platform",container="worker",resource="memory"})', "limit"),
    ('max(kube_pod_container_resource_requests{namespace="platform",container="worker",resource="memory"})', "request"),
], 8, w=8, unit="bytes", overrides=[color("limit", "red"), color("request", "orange")],
   desc="Model resident (~1 GiB) + KV cache. Above the limit = OOMKilled.")
ts("Worker pods and restarts", [
    ('max(kube_deployment_status_replicas_available{namespace="platform",deployment="worker"})', "available replicas"),
    ('sum(increase(kube_pod_container_status_restarts_total{namespace="platform"}[15m]))', "restarts (15 m, all platform pods)"),
], 16, w=8)
y += 8

# ---- Logs --------------------------------------------------------------------------
row("Logs")
panels.append({
    "type": "logs", "title": "API and worker logs", "id": nid(), "datasource": LOKI,
    "gridPos": {"h": 12, "w": 24, "x": 0, "y": y},
    "targets": [{"datasource": LOKI, "refId": "A",
                 "expr": '{namespace="platform", app=~"api|worker"} != "GET /healthz"'}],
    "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True},
})

dashboard = {
    "uid": "platform-tasks",
    "title": "Platform tasks",
    "tags": ["platform-tasks"],
    "timezone": "browser",
    "refresh": "10s",
    "time": {"from": "now-1h", "to": "now"},
    "schemaVersion": 39,
    "panels": panels,
}
out = pathlib.Path(__file__).with_suffix(".json")
out.write_text(json.dumps(dashboard, indent=2))
print(f"wrote {out} ({len(panels)} panels)")
