// Package sweeper runs the two background repairs, every 30s:
//
//   - Pending sweep: the API writes to Postgres, then publishes to RabbitMQ. Two
//     systems, no shared transaction. If the process dies (or RabbitMQ is down) in
//     between, the task sits in 'pending' forever. Republish those rows.
//
//   - Stale reaper: a task can be stranded in 'running' if its worker vanished in a
//     way that produced no redelivery (e.g. the process hung with its connection
//     open). Once its heartbeat is older than staleAfter, put it back in the queue.
//
// Both are safe to run on every API replica at once: a task published twice is
// claimed only once, because the worker's claim is a conditional UPDATE.
package sweeper

import (
	"context"
	"log/slog"
	"time"

	"platform-tasks/api/internal/metrics"
	"platform-tasks/api/internal/queue"
	"platform-tasks/api/internal/store"
)

const (
	interval     = 30 * time.Second
	pendingAfter = 60 * time.Second // leave the normal request path time to publish first
	staleAfter   = 60 * time.Second // workers beat every 5s; 60s of silence = gone
	pendingBatch = 100
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
			sweepPending(ctx, st, pub)
			reapStale(ctx, st, pub)
		}
	}
}

func sweepPending(ctx context.Context, st *store.Store, pub *queue.Publisher) {
	refs, err := st.StalePending(ctx, pendingAfter, pendingBatch)
	if err != nil {
		slog.Error("sweeper: query failed", "err", err)
		return
	}
	for _, ref := range refs {
		if err := pub.Publish(ctx, ref.TaskType, ref.ID); err != nil {
			slog.Error("sweeper: republish failed, will retry next tick", "task_id", ref.ID, "err", err)
			return // RabbitMQ is probably down; no point hammering it for every row
		}
		if _, err := st.MarkQueued(ctx, ref.ID); err != nil {
			slog.Error("sweeper: mark queued", "task_id", ref.ID, "err", err)
			continue
		}
		metrics.SweeperRepublished.Inc()
		slog.Info("sweeper: republished stranded task", "task_id", ref.ID)
	}
}

func reapStale(ctx context.Context, st *store.Store, pub *queue.Publisher) {
	refs, err := st.ReapStale(ctx, staleAfter)
	if err != nil {
		slog.Error("reaper: query failed", "err", err)
		return
	}
	for _, ref := range refs {
		metrics.ReaperRequeued.Inc()
		slog.Warn("reaper: task's worker stopped heartbeating, requeueing", "task_id", ref.ID)
		// Back in 'pending': if this publish fails, sweepPending picks it up later.
		if err := pub.Publish(ctx, ref.TaskType, ref.ID); err != nil {
			slog.Error("reaper: publish failed, pending sweep will retry", "task_id", ref.ID, "err", err)
			continue
		}
		if _, err := st.MarkQueued(ctx, ref.ID); err != nil {
			slog.Error("reaper: mark queued", "task_id", ref.ID, "err", err)
		}
	}
}
