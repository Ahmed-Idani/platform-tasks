// Package store is the only code that talks to the tasks table.
package store

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

var ErrNotFound = errors.New("task not found")

type Task struct {
	ID            string          `json:"task_id"`
	TaskType      string          `json:"task_type"`
	Status        string          `json:"status"`
	Parameters    json.RawMessage `json:"parameters,omitempty"` // only loaded by Get: the text can be large
	CallbackURL   *string         `json:"callback_url"`
	Attempt       int             `json:"attempt"`
	WorkerID      *string         `json:"worker_id"`
	Result        json.RawMessage `json:"result"`
	Error         *string         `json:"error"`
	CreatedAt     time.Time       `json:"created_at"`
	QueuedAt      *time.Time      `json:"queued_at"`
	StartedAt     *time.Time      `json:"started_at"`
	CompletedAt   *time.Time      `json:"completed_at"`
	WebhookSentAt *time.Time      `json:"webhook_sent_at"`
	WebhookError  *string         `json:"webhook_error"`
	// Undelivered webhooks: payload parked in object storage, retried by the dispatcher.
	WebhookAttempts int        `json:"webhook_attempts"`
	WebhookNextAt   *time.Time `json:"webhook_next_at"`
	WebhookRef      *string    `json:"webhook_ref"`
}

// Same column order as scan() below.
const columns = `id::text, task_type, status, callback_url, attempt, worker_id, result, error,
	created_at, queued_at, started_at, completed_at, webhook_sent_at, webhook_error,
	webhook_attempts, webhook_next_at, webhook_ref`

func scan(row pgx.Row, t *Task, extra ...any) error {
	var result []byte
	dest := append([]any{
		&t.ID, &t.TaskType, &t.Status, &t.CallbackURL, &t.Attempt, &t.WorkerID, &result, &t.Error,
		&t.CreatedAt, &t.QueuedAt, &t.StartedAt, &t.CompletedAt, &t.WebhookSentAt, &t.WebhookError,
		&t.WebhookAttempts, &t.WebhookNextAt, &t.WebhookRef,
	}, extra...)
	if err := row.Scan(dest...); err != nil {
		return err
	}
	if result != nil {
		t.Result = result
	}
	return nil
}

type Store struct {
	pool *pgxpool.Pool
}

func New(pool *pgxpool.Pool) *Store {
	return &Store{pool: pool}
}

// Create inserts a task in 'pending'. parameters is marshalled to JSONB by pgx.
func (s *Store) Create(ctx context.Context, taskType string, parameters any, callbackURL *string) (Task, error) {
	var t Task
	row := s.pool.QueryRow(ctx,
		`INSERT INTO tasks (task_type, parameters, callback_url)
		 VALUES ($1, $2, $3)
		 RETURNING `+columns,
		taskType, parameters, callbackURL)
	if err := scan(row, &t); err != nil {
		return Task{}, fmt.Errorf("insert task: %w", err)
	}
	return t, nil
}

// MarkQueued moves pending -> queued. Conditional: if a worker already claimed the
// task (it can be that fast), the row is no longer 'pending' and this is a no-op.
// Returns the new queued_at, or nil if the row was not 'pending' anymore.
func (s *Store) MarkQueued(ctx context.Context, id string) (*time.Time, error) {
	var queuedAt time.Time
	err := s.pool.QueryRow(ctx,
		`UPDATE tasks SET status = 'queued', queued_at = now()
		  WHERE id = $1 AND status = 'pending'
		  RETURNING queued_at`, id).Scan(&queuedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("mark queued: %w", err)
	}
	return &queuedAt, nil
}

func (s *Store) Get(ctx context.Context, id string) (Task, error) {
	var t Task
	var params []byte
	row := s.pool.QueryRow(ctx, `SELECT `+columns+`, parameters FROM tasks WHERE id = $1`, id)
	if err := scan(row, &t, &params); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return Task{}, ErrNotFound
		}
		return Task{}, fmt.Errorf("get task: %w", err)
	}
	t.Parameters = params
	return t, nil
}

type ListFilter struct {
	Status   string
	TaskType string
	Limit    int
}

func (s *Store) List(ctx context.Context, f ListFilter) ([]Task, error) {
	var where []string
	var args []any
	if f.Status != "" {
		args = append(args, f.Status)
		where = append(where, fmt.Sprintf("status = $%d", len(args)))
	}
	if f.TaskType != "" {
		args = append(args, f.TaskType)
		where = append(where, fmt.Sprintf("task_type = $%d", len(args)))
	}
	query := `SELECT ` + columns + ` FROM tasks`
	if len(where) > 0 {
		query += ` WHERE ` + strings.Join(where, " AND ")
	}
	args = append(args, f.Limit)
	query += fmt.Sprintf(` ORDER BY created_at DESC LIMIT $%d`, len(args))

	rows, err := s.pool.Query(ctx, query, args...)
	if err != nil {
		return nil, fmt.Errorf("list tasks: %w", err)
	}
	defer rows.Close()

	tasks := []Task{}
	for rows.Next() {
		var t Task
		if err := scan(rows, &t); err != nil {
			return nil, fmt.Errorf("scan task: %w", err)
		}
		tasks = append(tasks, t)
	}
	return tasks, rows.Err()
}

// Ref identifies a task to (re)publish.
type Ref struct {
	ID       string
	TaskType string
}

// StalePending returns tasks stuck in 'pending': inserted, but the publish never
// happened (API crashed or RabbitMQ was down in between).
func (s *Store) StalePending(ctx context.Context, olderThan time.Duration, limit int) ([]Ref, error) {
	rows, err := s.pool.Query(ctx,
		`SELECT id::text, task_type FROM tasks
		  WHERE status = 'pending' AND created_at < now() - make_interval(secs => $1)
		  ORDER BY created_at
		  LIMIT $2`, olderThan.Seconds(), limit)
	if err != nil {
		return nil, fmt.Errorf("stale pending: %w", err)
	}
	return pgx.CollectRows(rows, pgx.RowToStructByPos[Ref])
}

// ReapStale puts back in 'pending' the tasks whose worker vanished: 'running' with a
// heartbeat older than staleAfter, and no redelivery to rescue them (e.g. the worker
// hung with its connection still open). 'pending', not 'queued': the caller then
// publishes like a new task, and if that publish fails the pending sweep retries it.
// The worker's claim counts the attempt and fails the task once attempts run out.
func (s *Store) ReapStale(ctx context.Context, staleAfter time.Duration) ([]Ref, error) {
	rows, err := s.pool.Query(ctx,
		`UPDATE tasks
		    SET status = 'pending', queued_at = NULL, heartbeat_at = NULL,
		        error = format('worker %s lost: no heartbeat for %ss (attempt %s)', worker_id, $1::int, attempt)
		  WHERE status = 'running' AND heartbeat_at < now() - make_interval(secs => $1)
		  RETURNING id::text, task_type`, int(staleAfter.Seconds()))
	if err != nil {
		return nil, fmt.Errorf("reap stale: %w", err)
	}
	return pgx.CollectRows(rows, pgx.RowToStructByPos[Ref])
}

// ErrNotRetryable: only failed or cancelled tasks can be retried by hand.
var ErrNotRetryable = errors.New("task is not failed or cancelled")

// ResetForRetry starts a failed task over: back to 'pending' with a clean slate.
// Conditional, so retrying a task that is already running is refused.
func (s *Store) ResetForRetry(ctx context.Context, id string) (Task, error) {
	var t Task
	row := s.pool.QueryRow(ctx,
		`UPDATE tasks
		    SET status = 'pending', attempt = 0, error = NULL, result = NULL, worker_id = NULL,
		        heartbeat_at = NULL, queued_at = NULL, started_at = NULL, completed_at = NULL,
		        webhook_sent_at = NULL, webhook_error = NULL,
		        webhook_attempts = 0, webhook_next_at = NULL, webhook_ref = NULL
		  WHERE id = $1 AND status IN ('failed', 'cancelled')
		  RETURNING `+columns, id)
	if err := scan(row, &t); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			if _, getErr := s.Get(ctx, id); errors.Is(getErr, ErrNotFound) {
				return Task{}, ErrNotFound
			}
			return Task{}, ErrNotRetryable
		}
		return Task{}, fmt.Errorf("reset for retry: %w", err)
	}
	return t, nil
}

// CountByStatus returns how many tasks are in each status (0 for empty ones).
func (s *Store) CountByStatus(ctx context.Context) (map[string]int, error) {
	counts := map[string]int{
		"pending": 0, "queued": 0, "running": 0, "completed": 0, "failed": 0, "cancelled": 0,
	}
	rows, err := s.pool.Query(ctx, `SELECT status, count(*) FROM tasks GROUP BY status`)
	if err != nil {
		return nil, fmt.Errorf("count by status: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var status string
		var n int
		if err := rows.Scan(&status, &n); err != nil {
			return nil, err
		}
		counts[status] = n
	}
	return counts, rows.Err()
}

// ---- webhook dispatcher ----------------------------------------------------------

type DueWebhook struct {
	ID          string
	CallbackURL string
	Ref         string
	Attempts    int
}

// ClaimDueWebhooks leases up to limit undelivered webhooks whose retry time has come.
// "Leases": it pushes their next_at forward by `lease` in the same statement, so another
// API replica running the same loop won't pick them up while we deliver. SKIP LOCKED
// makes two replicas split the batch instead of waiting on each other. If this replica
// dies mid-delivery, the lease simply expires and someone retries.
func (s *Store) ClaimDueWebhooks(ctx context.Context, lease time.Duration, limit int) ([]DueWebhook, error) {
	rows, err := s.pool.Query(ctx,
		`UPDATE tasks SET webhook_next_at = now() + make_interval(secs => $1)
		  WHERE id IN (SELECT id FROM tasks
		                WHERE webhook_next_at <= now() AND webhook_sent_at IS NULL AND webhook_ref IS NOT NULL
		                ORDER BY webhook_next_at
		                LIMIT $2
		                FOR UPDATE SKIP LOCKED)
		  RETURNING id::text, callback_url, webhook_ref, webhook_attempts`, lease.Seconds(), limit)
	if err != nil {
		return nil, fmt.Errorf("claim due webhooks: %w", err)
	}
	return pgx.CollectRows(rows, pgx.RowToStructByPos[DueWebhook])
}

func (s *Store) WebhookDelivered(ctx context.Context, id string) error {
	_, err := s.pool.Exec(ctx,
		`UPDATE tasks SET webhook_sent_at = now(), webhook_error = NULL, webhook_attempts = webhook_attempts + 1,
		                  webhook_next_at = NULL, webhook_ref = NULL
		  WHERE id = $1`, id)
	return err
}

// WebhookFailed records a failed attempt. retryIn nil = give up (the parked payload
// is left for the bucket's lifecycle rule to expire).
func (s *Store) WebhookFailed(ctx context.Context, id, errMsg string, retryIn *time.Duration) error {
	var next any // NULL
	if retryIn != nil {
		next = retryIn.Seconds()
	}
	_, err := s.pool.Exec(ctx,
		`UPDATE tasks SET webhook_attempts = webhook_attempts + 1, webhook_error = $2,
		                  webhook_next_at = CASE WHEN $3::float8 IS NULL THEN NULL
		                                         ELSE now() + make_interval(secs => $3::float8) END
		  WHERE id = $1`, id, errMsg, next)
	return err
}

var ErrNoPendingWebhook = errors.New("no undelivered webhook for this task")

// ResendWebhookNow makes a parked webhook due immediately (also revives one that was
// given up on, as long as its payload is still in the bucket).
func (s *Store) ResendWebhookNow(ctx context.Context, id string) error {
	tag, err := s.pool.Exec(ctx,
		`UPDATE tasks SET webhook_next_at = now()
		  WHERE id = $1 AND webhook_ref IS NOT NULL AND webhook_sent_at IS NULL`, id)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		if _, getErr := s.Get(ctx, id); errors.Is(getErr, ErrNotFound) {
			return ErrNotFound
		}
		return ErrNoPendingWebhook
	}
	return nil
}
