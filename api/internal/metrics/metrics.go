// Package metrics defines the API's Prometheus metrics, served on GET /metrics.
package metrics

import (
	"context"
	"log/slog"
	"net/http"
	"strconv"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

var (
	httpRequests = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "api_http_requests_total",
		Help: "HTTP requests handled, by route pattern and status code.",
	}, []string{"route", "code"})

	httpDuration = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "api_http_request_duration_seconds",
		Help:    "HTTP request latency, by route pattern.",
		Buckets: []float64{.001, .0025, .005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5},
	}, []string{"route"})

	TasksCreated = promauto.NewCounter(prometheus.CounterOpts{
		Name: "api_tasks_created_total",
		Help: "Tasks accepted by POST /v1/tasks.",
	})

	PublishFailures = promauto.NewCounter(prometheus.CounterOpts{
		Name: "api_publish_failures_total",
		Help: "Publishes to RabbitMQ that failed (the task stays pending for the sweeper).",
	})

	SweeperRepublished = promauto.NewCounter(prometheus.CounterOpts{
		Name: "api_sweeper_republished_total",
		Help: "Tasks stuck in pending that the sweeper published again (publish gap closed).",
	})

	ReaperRequeued = promauto.NewCounter(prometheus.CounterOpts{
		Name: "api_reaper_requeued_total",
		Help: "Running tasks whose heartbeat went stale and were put back in the queue.",
	})

	WebhookDispatch = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "api_webhook_dispatch_total",
		Help: "Parked webhook deliveries attempted by the dispatcher, by result (delivered, retry, gave_up).",
	}, []string{"result"})
)

// RegisterTaskCounts exposes platform_tasks{status}, read from Postgres at scrape time.
// Every replica reports the same value: aggregate with max, not sum.
func RegisterTaskCounts(count func(context.Context) (map[string]int, error)) {
	prometheus.MustRegister(&taskCounts{count: count})
}

var taskCountsDesc = prometheus.NewDesc("platform_tasks", "Tasks currently in each status (from Postgres).", []string{"status"}, nil)

type taskCounts struct {
	count func(context.Context) (map[string]int, error)
}

func (c *taskCounts) Describe(ch chan<- *prometheus.Desc) { ch <- taskCountsDesc }

func (c *taskCounts) Collect(ch chan<- prometheus.Metric) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	counts, err := c.count(ctx)
	if err != nil {
		slog.Warn("metrics: task counts unavailable", "err", err)
		return
	}
	for status, n := range counts {
		ch <- prometheus.MustNewConstMetric(taskCountsDesc, prometheus.GaugeValue, float64(n), status)
	}
}

// Handler serves /metrics.
func Handler() http.Handler { return promhttp.Handler() }

// Instrument labels requests by route pattern, not raw path, to keep label cardinality bounded.
func Instrument(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		rec := &statusRecorder{ResponseWriter: w, code: http.StatusOK}
		next.ServeHTTP(rec, r)
		route := r.Pattern
		if route == "" {
			route = "unmatched"
		}
		httpRequests.WithLabelValues(route, strconv.Itoa(rec.code)).Inc()
		httpDuration.WithLabelValues(route).Observe(time.Since(start).Seconds())
	})
}

type statusRecorder struct {
	http.ResponseWriter
	code int
}

func (r *statusRecorder) WriteHeader(code int) {
	r.code = code
	r.ResponseWriter.WriteHeader(code)
}
