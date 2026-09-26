-- +goose Up
CREATE TABLE tasks (
  id               uuid PRIMARY KEY DEFAULT uuidv7(),  -- time-ordered: new rows append to the index instead of landing randomly
  task_type        text NOT NULL,
  status           text NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'queued', 'running', 'completed', 'failed', 'cancelled')),
  parameters       jsonb NOT NULL,
  callback_url     text,

  result           jsonb,
  error            text,

  attempt          int NOT NULL DEFAULT 0,   -- incremented by the worker's claim; source of truth for retries
  worker_id        text,                     -- who claimed it
  heartbeat_at     timestamptz,              -- touched during long generations; stale = stranded task

  created_at       timestamptz NOT NULL DEFAULT now(),
  queued_at        timestamptz,              -- queued_at -> started_at = task_queue_wait_seconds
  started_at       timestamptz,
  completed_at     timestamptz,              -- set for completed AND failed (= "finished at")

  webhook_sent_at  timestamptz,              -- client acknowledged the callback (2xx)
  webhook_error    text                      -- last delivery error, if any
);

-- List endpoint (GET /v1/tasks?status=...) and the pending sweeper, both "by status, oldest first".
CREATE INDEX tasks_status_created_at_idx ON tasks (status, created_at);

-- Unfiltered list, newest first.
CREATE INDEX tasks_created_at_idx ON tasks (created_at);

-- +goose Down
DROP TABLE tasks;
