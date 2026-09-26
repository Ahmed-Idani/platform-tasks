// Package httpapi is the REST layer: routing, validation, JSON in/out, error mapping.
package httpapi

import (
	"encoding/json"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"platform-tasks/api/internal/metrics"
	"platform-tasks/api/internal/queue"
	"platform-tasks/api/internal/store"
)

type Server struct {
	store       *store.Store
	pub         *queue.Publisher
	corsOrigins map[string]bool
}

func New(st *store.Store, pub *queue.Publisher, corsOrigins []string) *Server {
	origins := map[string]bool{}
	for _, o := range corsOrigins {
		if o = strings.TrimSpace(o); o != "" {
			origins[o] = true
		}
	}
	return &Server{store: st, pub: pub, corsOrigins: origins}
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /v1/tasks", s.createTask)
	mux.HandleFunc("GET /v1/tasks", s.listTasks)
	mux.HandleFunc("GET /v1/tasks/{id}", s.getTask)
	mux.HandleFunc("POST /v1/tasks/{id}/retry", s.retryTask)
	mux.HandleFunc("POST /v1/tasks/{id}/webhook/resend", s.resendWebhook)
	mux.HandleFunc("GET /v1/stats", s.stats)
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	})
	mux.Handle("GET /metrics", metrics.Handler()) // not routed by the Ingress: in-cluster scraping only
	return s.cors(logRequests(metrics.Instrument(mux)))
}

// cors lets the web UI (a different origin in dev: localhost:5173) call the API.
func (s *Server) cors(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if origin := r.Header.Get("Origin"); s.corsOrigins[origin] || s.corsOrigins["*"] {
			w.Header().Set("Access-Control-Allow-Origin", origin)
			w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
			w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
			w.Header().Set("Vary", "Origin")
		}
		if r.Method == http.MethodOptions { // preflight
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (r *statusRecorder) WriteHeader(code int) {
	r.status = code
	r.ResponseWriter.WriteHeader(code)
}

func logRequests(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(rec, r)
		if r.URL.Path == "/healthz" || r.URL.Path == "/metrics" {
			return // probes and scrapes every few seconds would drown the real requests
		}
		slog.Info("http", "method", r.Method, "path", r.URL.Path, "status", rec.status,
			"ms", time.Since(start).Milliseconds())
	})
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

// writeError: the client gets a short message, never an internal error string.
func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}

func internalError(w http.ResponseWriter, r *http.Request, err error) {
	slog.Error("internal error", "method", r.Method, "path", r.URL.Path, "err", err)
	writeError(w, http.StatusInternalServerError, "internal error")
}
