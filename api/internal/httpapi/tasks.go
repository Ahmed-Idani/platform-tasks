package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"net/url"
	"strconv"
	"time"

	"platform-tasks/api/internal/store"
)

const (
	maxBodyBytes    = 256 << 10 // 256 KiB: MaxChars of UTF-8 text plus JSON overhead
	MaxChars        = 24_000    // ≈ 6,000 tokens; must match the worker's MAX_CHARS
	defaultMaxWords = 150
	maxMaxWords     = 1000
	defaultLimit    = 50
	maxLimit        = 200
)

// Only task type for now. Adding one = a new queue + a new worker deployment.
var taskTypes = map[string]bool{"llm_inference": true}

var statuses = map[string]bool{
	"pending": true, "queued": true, "running": true,
	"completed": true, "failed": true, "cancelled": true,
}

type llmInferenceParams struct {
	Text     string `json:"text"`
	MaxWords int    `json:"max_words"`
}

type createTaskRequest struct {
	TaskType    string             `json:"task_type"`
	Parameters  llmInferenceParams `json:"parameters"`
	CallbackURL *string            `json:"callback_url"`
}

func (req *createTaskRequest) validate() error {
	if !taskTypes[req.TaskType] {
		return fmt.Errorf("unknown task_type %q", req.TaskType)
	}
	p := &req.Parameters
	if len([]rune(p.Text)) == 0 {
		return errors.New("parameters.text is required")
	}
	if n := len([]rune(p.Text)); n > MaxChars {
		return fmt.Errorf("parameters.text is too long: %d chars, max %d", n, MaxChars)
	}
	if p.MaxWords == 0 {
		p.MaxWords = defaultMaxWords
	}
	if p.MaxWords < 1 || p.MaxWords > maxMaxWords {
		return fmt.Errorf("parameters.max_words must be between 1 and %d", maxMaxWords)
	}
	if req.CallbackURL != nil {
		if *req.CallbackURL == "" {
			req.CallbackURL = nil
		} else if u, err := url.Parse(*req.CallbackURL); err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
			return errors.New("callback_url must be an absolute http(s) URL")
		}
	}
	return nil
}

// POST /v1/tasks
//
//  1. validate
//  2. INSERT            -> pending
//  3. publish + confirm -> the message is durably in RabbitMQ
//  4. UPDATE            -> queued
//
// If 3 fails, the task stays pending and the sweeper republishes it later. The
// client still gets 202: the task is accepted, it is just not queued yet.
func (s *Server) createTask(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, maxBodyBytes)
	dec := json.NewDecoder(r.Body)
	dec.DisallowUnknownFields() // a typo like "max_word" is an error, not silently ignored

	var req createTaskRequest
	if err := dec.Decode(&req); err != nil {
		var tooBig *http.MaxBytesError
		if errors.As(err, &tooBig) {
			writeError(w, http.StatusRequestEntityTooLarge, "request body too large")
			return
		}
		writeError(w, http.StatusBadRequest, "invalid JSON: "+err.Error())
		return
	}
	if err := req.validate(); err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}

	task, err := s.store.Create(r.Context(), req.TaskType, req.Parameters, req.CallbackURL)
	if err != nil {
		internalError(w, r, err)
		return
	}
	task = s.enqueue(r.Context(), task)
	slog.Info("task created", "task_id", task.ID, "status", task.Status)
	writeJSON(w, http.StatusAccepted, task)
}

// enqueue publishes a 'pending' task and marks it 'queued'. It never fails the
// request: if the publish fails, the task stays pending and the sweeper retries it.
func (s *Server) enqueue(ctx context.Context, task store.Task) store.Task {
	// Own timeout: don't let a slow broker hang the request forever.
	pubCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	if err := s.pub.Publish(pubCtx, task.TaskType, task.ID); err != nil {
		slog.Warn("publish failed, sweeper will retry", "task_id", task.ID, "err", err)
	} else if queuedAt, err := s.store.MarkQueued(ctx, task.ID); err != nil {
		slog.Warn("mark queued failed", "task_id", task.ID, "err", err)
	} else if queuedAt != nil {
		task.Status, task.QueuedAt = "queued", queuedAt
	} else if fresh, err := s.store.Get(ctx, task.ID); err == nil {
		// Not 'pending' anymore: a worker claimed it before we could mark it queued.
		task = fresh
		task.Parameters = nil
	}
	return task
}

// POST /v1/tasks/{id}/retry: start a failed task over from attempt 0.
func (s *Server) retryTask(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if !isUUID(id) {
		writeError(w, http.StatusBadRequest, "task id must be a UUID")
		return
	}
	task, err := s.store.ResetForRetry(r.Context(), id)
	switch {
	case errors.Is(err, store.ErrNotFound):
		writeError(w, http.StatusNotFound, "task not found")
		return
	case errors.Is(err, store.ErrNotRetryable):
		writeError(w, http.StatusConflict, "only failed or cancelled tasks can be retried")
		return
	case err != nil:
		internalError(w, r, err)
		return
	}
	task = s.enqueue(r.Context(), task)
	slog.Info("task retried by hand", "task_id", task.ID, "status", task.Status)
	writeJSON(w, http.StatusAccepted, task)
}

// GET /v1/tasks/{id}
func (s *Server) getTask(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if !isUUID(id) {
		writeError(w, http.StatusBadRequest, "task id must be a UUID")
		return
	}
	task, err := s.store.Get(r.Context(), id)
	if errors.Is(err, store.ErrNotFound) {
		writeError(w, http.StatusNotFound, "task not found")
		return
	}
	if err != nil {
		internalError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, task)
}

// GET /v1/tasks?status=failed&task_type=llm_inference&limit=50
func (s *Server) listTasks(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	status := q.Get("status")
	if status != "" && !statuses[status] {
		writeError(w, http.StatusBadRequest, fmt.Sprintf("unknown status %q", status))
		return
	}
	taskType := q.Get("task_type")
	if taskType != "" && !taskTypes[taskType] {
		writeError(w, http.StatusBadRequest, fmt.Sprintf("unknown task_type %q", taskType))
		return
	}
	limit := defaultLimit
	if v := q.Get("limit"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 1 || n > maxLimit {
			writeError(w, http.StatusBadRequest, fmt.Sprintf("limit must be between 1 and %d", maxLimit))
			return
		}
		limit = n
	}

	tasks, err := s.store.List(r.Context(), store.ListFilter{Status: status, TaskType: taskType, Limit: limit})
	if err != nil {
		internalError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"tasks": tasks})
}

// isUUID checks the 8-4-4-4-12 hex shape, so a bad id is a 400 instead of a
// Postgres cast error turned into a 500.
func isUUID(s string) bool {
	if len(s) != 36 {
		return false
	}
	for i, c := range s {
		switch i {
		case 8, 13, 18, 23:
			if c != '-' {
				return false
			}
		default:
			if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f' || c >= 'A' && c <= 'F') {
				return false
			}
		}
	}
	return true
}

// GET /v1/stats
func (s *Server) stats(w http.ResponseWriter, r *http.Request) {
	counts, err := s.store.CountByStatus(r.Context())
	if err != nil {
		internalError(w, r, err)
		return
	}
	total := 0
	for _, n := range counts {
		total += n
	}
	// Queue depths come from RabbitMQ; if it's down, still answer with the counts.
	queues, err := s.pub.Depths("llm_inference")
	if err != nil {
		slog.Warn("stats: queue depths unavailable", "err", err)
	}
	writeJSON(w, http.StatusOK, map[string]any{"counts": counts, "total": total, "queues": queues})
}
