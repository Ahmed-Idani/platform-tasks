"""Webhook receiver for local testing: an example of what a client's endpoint looks like.

    python3 scripts/webhook_receiver.py          # started by scripts/dev.sh on :9000

Accepts task.finished callbacks on any path and shows them at http://localhost:9000
(inbox page, auto-refreshing) and http://localhost:9000/deliveries (JSON).

Simulate a broken client to watch the platform's retry path:
    callback_url = http://localhost:9000/fail/503     -> always answers HTTP 503
    callback_url = http://localhost:9000/slow         -> answers after 15 s (> 10 s timeout)

Use host.docker.internal instead of localhost when the worker runs in Docker.
The contract this endpoint implements is documented in docs/webhooks.md.
"""
import html
import http.server
import json
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime

PORT = int(os.getenv("PORT", "9000"))
deliveries = deque(maxlen=100)  # newest first
lock = threading.Lock()


def record(path, headers, body, status):
  with lock:
    deliveries.appendleft({
      "received_at": datetime.now().isoformat(timespec="seconds"),
      "path": path,
      "answered": status,
      "headers": {k: v for k, v in headers.items() if k.lower() in ("content-type", "user-agent", "x-task-id")},
      "body": body,
    })


class Handler(http.server.BaseHTTPRequestHandler):
  def do_POST(self):
    raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
    try:
      body = json.loads(raw or b"{}")
    except ValueError:
      body = {"_invalid_json": raw.decode(errors="replace")}

    status = 204  # any 2xx means "delivered"
    if self.path.startswith("/fail/"):
      status = int(self.path.split("/")[2] or 500)
    elif self.path.startswith("/slow"):
      time.sleep(15)

    record(self.path, self.headers, body, status)
    result = body.get("result") or {}
    summary = (result.get("summary") or body.get("error") or "")[:90]
    print(f"{status} {self.path}  {body.get('status', '?'):>9}  {body.get('task_id')}  {summary!r}", flush=True)
    self.send_response(status)
    self.end_headers()

  def do_GET(self):
    if self.path.startswith("/deliveries"):
      with lock:
        data = json.dumps(list(deliveries), indent=2).encode()
      self._send(200, "application/json", data)
    else:
      self._send(200, "text/html; charset=utf-8", page().encode())

  def _send(self, code, ctype, data):
    self.send_response(code)
    self.send_header("Content-Type", ctype)
    self.send_header("Content-Length", str(len(data)))
    self.end_headers()
    self.wfile.write(data)

  def log_message(self, *args):
    pass


def page():
  with lock:
    items = list(deliveries)
  rows = []
  for d in items:
    b = d["body"]
    ok = 200 <= d["answered"] < 300
    rows.append(f"""
<details class="d" {"open" if not rows else ""}>
  <summary>
    <span class="badge {'ok' if ok else 'bad'}">{d['answered']}</span>
    <span class="st {html.escape(str(b.get('status')))}">{html.escape(str(b.get('status')))}</span>
    <code>{html.escape(str(b.get('task_id')))}</code>
    <span class="muted">attempt {html.escape(str(b.get('attempt')))} · {html.escape(d['path'])} · {d['received_at']}</span>
  </summary>
  <pre>{html.escape(json.dumps(d['headers'], indent=2))}</pre>
  <pre>{html.escape(json.dumps(b, indent=2, ensure_ascii=False))}</pre>
</details>""")
  body = "".join(rows) or '<p class="muted">No delivery yet. Create a task with callback URL <code>http://localhost:9000/hook</code>.</p>'
  return f"""<!doctype html><html><head><meta charset="utf-8"><title>Webhook inbox</title>
<meta http-equiv="refresh" content="3">
<style>
 body{{font:14px/1.5 ui-sans-serif,system-ui;margin:0;background:#0a0a0a;color:#ededed}}
 main{{max-width:980px;margin:0 auto;padding:28px 20px}}
 h1{{font-size:20px;margin:0 0 4px}} .muted{{color:#a1a1a1}} code{{font-family:ui-monospace,monospace;font-size:13px}}
 .d{{border:1px solid #262626;border-radius:8px;margin:10px 0;background:#111}}
 summary{{cursor:pointer;padding:10px 14px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}}
 pre{{margin:0;padding:12px 14px;border-top:1px solid #262626;overflow:auto;font:12.5px/1.5 ui-monospace,monospace;color:#d4d4d4}}
 .badge{{font:600 12px ui-monospace,monospace;padding:1px 7px;border-radius:99px}}
 .ok{{background:#0f2415;color:#62c073}} .bad{{background:#2e1113;color:#ff6166}}
 .st.completed{{color:#62c073}} .st.failed{{color:#ff6166}}
 .help{{border:1px solid #262626;border-radius:8px;padding:12px 14px;margin:16px 0;background:#111}}
</style></head><body><main>
<h1>Webhook inbox</h1>
<p class="muted">Example client endpoint on :{PORT}. Every POST is recorded here with the answer it got, newest first; the page refreshes every 3 s.</p>
<div class="help">
 <b>Use as callback_url</b><br>
 <code>http://localhost:{PORT}/hook</code> · accepted (worker in the venv)<br>
 <code>http://host.docker.internal:{PORT}/hook</code> · accepted (worker in Docker)<br>
 <code>…/fail/503</code> · always answers 503: the platform parks the payload and retries with backoff<br>
 <code>…/slow</code> · answers after 15 s, past the platform's 10 s timeout<br>
 Contract: <code>docs/webhooks.md</code> · raw JSON: <a style="color:#52a8ff" href="/deliveries">/deliveries</a>
</div>
{body}
</main></body></html>"""


if __name__ == "__main__":
  print(f"webhook inbox on http://localhost:{PORT}", flush=True)
  try:
    http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
  except KeyboardInterrupt:
    sys.exit(0)
