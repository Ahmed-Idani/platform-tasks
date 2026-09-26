"""Tiny HTTP server for Kubernetes probes (and /metrics later), on HEALTH_PORT (8081).

  /livez   503 while the model loads, then 200 while the process runs.
           Used by the startupProbe (generous: the model takes seconds to load) and the
           livenessProbe (restart a process that stopped answering).
  /readyz  200 only while connected to RabbitMQ and consuming. Used by the readinessProbe:
           it gates rolling deploys; nothing routes traffic to a queue consumer anyway.

Started before the model loads, so the kubelet gets an honest 503 instead of a refused
connection during startup.
"""
import http.server
import os
import threading

PORT = int(os.getenv("HEALTH_PORT", "8081"))

model_loaded = threading.Event()
consuming = threading.Event()


class _Handler(http.server.BaseHTTPRequestHandler):
  def do_GET(self):
    if self.path == "/livez":
      ok = model_loaded.is_set()
    elif self.path == "/readyz":
      ok = model_loaded.is_set() and consuming.is_set()
    else:
      self.send_response(404)
      self.end_headers()
      return
    self.send_response(200 if ok else 503)
    self.send_header("Content-Type", "text/plain")
    self.end_headers()
    self.wfile.write(b"ok\n" if ok else b"not yet\n")

  def log_message(self, *args):
    pass  # probes every few seconds would flood the logs


def start():
  server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), _Handler)
  threading.Thread(target=server.serve_forever, daemon=True, name="health").start()
