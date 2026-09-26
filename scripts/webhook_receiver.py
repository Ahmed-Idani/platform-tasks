"""Tiny webhook receiver for local testing: prints every task.finished callback.

Use http://host.docker.internal:9000/hook as callback_url when the worker runs in
Docker, or http://localhost:9000/hook when it runs from the venv.
"""
import http.server
import json
import os
import sys

PORT = int(os.getenv("PORT", "9000"))


class Handler(http.server.BaseHTTPRequestHandler):
  def do_POST(self):
    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
    result = body.get("result") or {}
    summary = (result.get("summary") or body.get("error") or "")[:100]
    print(f"{body.get('status', '?'):>9}  {body.get('task_id')}  attempt={body.get('attempt')}  {summary!r}", flush=True)
    self.send_response(204)
    self.end_headers()

  def log_message(self, *args):
    pass  # the line above is enough


if __name__ == "__main__":
  print(f"listening on :{PORT}", flush=True)
  try:
    http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
  except KeyboardInterrupt:
    sys.exit(0)
