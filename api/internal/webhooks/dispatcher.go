// Package webhooks delivers the callbacks the worker couldn't: when a client's endpoint
// is down, the worker parks the exact payload in object storage (webhooks/{id}.json)
// and moves on. This loop retries from there with backoff, so a finished result is
// delivered late rather than recomputed, and a slow client never holds a CPU worker.
package webhooks

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"time"

	"platform-tasks/api/internal/storage"
	"platform-tasks/api/internal/store"
)

const (
	tick  = 15 * time.Second
	lease = 2 * time.Minute // > one delivery attempt (timeout below); see ClaimDueWebhooks
	batch = 20
)

// Delay before the next attempt, indexed by how many attempts have been made. The
// worker makes attempt 1 and schedules attempt 2 one minute later. After attempt 7
// (~21h in total) we give up; the bucket's lifecycle rule removes the payload after 7d.
var backoff = map[int]time.Duration{
	2: 5 * time.Minute,
	3: 30 * time.Minute,
	4: 2 * time.Hour,
	5: 6 * time.Hour,
	6: 12 * time.Hour,
}

var client = &http.Client{Timeout: 10 * time.Second}

// Run blocks until ctx is cancelled.
func Run(ctx context.Context, st *store.Store, objects *storage.Storage) {
	t := time.NewTicker(tick)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			due, err := st.ClaimDueWebhooks(ctx, lease, batch)
			if err != nil {
				slog.Error("webhooks: claim failed", "err", err)
				continue
			}
			for _, w := range due {
				deliver(ctx, st, objects, w)
			}
		}
	}
}

func deliver(ctx context.Context, st *store.Store, objects *storage.Storage, w store.DueWebhook) {
	attempt := w.Attempts + 1
	log := slog.With("task_id", w.ID, "attempt", attempt)

	body, err := objects.Get(ctx, w.Ref)
	if errors.Is(err, storage.ErrNotFound) {
		log.Error("webhooks: payload gone from the bucket (expired?), giving up", "ref", w.Ref)
		record(ctx, st, w.ID, "payload no longer in object storage", nil)
		return
	}
	if err != nil {
		// Storage hiccup: not the client's fault, try again soon without counting it.
		log.Warn("webhooks: read payload failed, retrying next tick", "err", err)
		return
	}

	if err := post(ctx, w.CallbackURL, w.ID, body); err != nil {
		msg := fmt.Sprintf("attempt %d: %v", attempt, err)
		if next, ok := backoff[attempt]; ok {
			log.Warn("webhooks: delivery failed, will retry", "err", err, "in", next)
			record(ctx, st, w.ID, msg, &next)
		} else {
			log.Error("webhooks: delivery failed, giving up", "err", err)
			record(ctx, st, w.ID, msg+" (gave up)", nil)
		}
		return
	}

	if err := st.WebhookDelivered(ctx, w.ID); err != nil {
		// Delivered but not recorded: the lease expires and the client may get it twice.
		// Webhook consumers must be idempotent (X-Task-Id) for exactly this reason.
		log.Error("webhooks: delivered but could not record it", "err", err)
		return
	}
	// Delivered: the parked copy has no purpose anymore.
	if err := objects.Delete(ctx, w.Ref); err != nil {
		log.Warn("webhooks: could not delete parked payload (lifecycle will)", "err", err)
	}
	log.Info("webhooks: delivered from object storage")
}

func record(ctx context.Context, st *store.Store, id, msg string, retryIn *time.Duration) {
	if err := st.WebhookFailed(ctx, id, msg, retryIn); err != nil {
		slog.Error("webhooks: could not record failure", "task_id", id, "err", err)
	}
}

func post(ctx context.Context, url, taskID string, body []byte) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", "platform-tasks-api")
	req.Header.Set("X-Task-Id", taskID)
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<16))
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	return nil
}
