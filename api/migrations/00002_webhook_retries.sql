-- +goose Up
-- Webhook delivery that outlives the worker: the worker tries the callback once; if
-- the client is down, it parks the exact payload in object storage and the API's
-- dispatcher retries from there with backoff. The model never runs twice for it.
ALTER TABLE tasks
  ADD COLUMN webhook_attempts int NOT NULL DEFAULT 0,  -- deliveries tried so far
  ADD COLUMN webhook_next_at  timestamptz,             -- when the dispatcher tries again (NULL = nothing scheduled)
  ADD COLUMN webhook_ref      text;                    -- s3://bucket/webhooks/{id}.json while undelivered

-- The dispatcher's only query: "undelivered webhooks that are due". Partial, so the
-- index holds just the handful of rows waiting, not the whole table.
CREATE INDEX tasks_webhook_due_idx ON tasks (webhook_next_at) WHERE webhook_next_at IS NOT NULL;

-- +goose Down
DROP INDEX tasks_webhook_due_idx;
ALTER TABLE tasks
  DROP COLUMN webhook_ref,
  DROP COLUMN webhook_next_at,
  DROP COLUMN webhook_attempts;
