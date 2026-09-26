// Package sweeper closes the "publish gap": the API writes to Postgres, then publishes
// to RabbitMQ. Two systems, no shared transaction. If the process dies (or RabbitMQ is
// down) in between, the task sits in 'pending' forever. The sweeper finds those rows
// and publishes them again.
//
// Safe to run on every API replica at once: a task published twice is claimed only
// once, because the worker's claim is a conditional UPDATE.
package sweeper

import (
	"context"
	"log/slog"
	"time"

	"platform-tasks/api/internal/queue"
	"platform-tasks/api/internal/store"
)

const (
	interval  = 30 * time.Second
	olderThan = 60 * time.Second // leave the normal request path time to publish first
	batch     = 100
)

// Run blocks until ctx is cancelled.
func Run(ctx context.Context, st *store.Store, pub *queue.Publisher) {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			sweep(ctx, st, pub)
		}
	}
}

func sweep(ctx context.Context, st *store.Store, pub *queue.Publisher) {
	ids, err := st.StalePending(ctx, olderThan, batch)
	if err != nil {
		slog.Error("sweeper: query failed", "err", err)
		return
	}
	for _, id := range ids {
		t, err := st.Get(ctx, id)
		if err != nil {
			slog.Error("sweeper: load task", "task_id", id, "err", err)
			continue
		}
		if err := pub.Publish(ctx, t.TaskType, id); err != nil {
			slog.Error("sweeper: republish failed, will retry next tick", "task_id", id, "err", err)
			return // RabbitMQ is probably down; no point hammering it for every row
		}
		if _, err := st.MarkQueued(ctx, id); err != nil {
			slog.Error("sweeper: mark queued", "task_id", id, "err", err)
			continue
		}
		slog.Info("sweeper: republished stranded task", "task_id", id)
	}
}
