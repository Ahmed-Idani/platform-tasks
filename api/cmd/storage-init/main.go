// storage-init applies the bucket configuration: run once per deploy, like migrations.
// The bucket itself is created by Garage locally (GARAGE_DEFAULT_BUCKET) and by
// infrastructure code in prod.
package main

import (
	"context"
	"log/slog"
	"os"
	"time"

	"platform-tasks/api/internal/storage"
)

// Pending webhook payloads are deleted on delivery; this is the safety net for the
// ones never delivered (client gone for good). A bit longer than the dispatcher's
// retry schedule, so nothing expires while it is still being retried.
var lifecycle = map[string]int32{
	"webhooks/": 7,
}

func main() {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	st, err := storage.New(ctx)
	if err != nil {
		slog.Error("storage config", "err", err)
		os.Exit(1)
	}
	if err := st.ApplyLifecycle(ctx, lifecycle); err != nil {
		slog.Error("apply lifecycle", "bucket", st.Bucket(), "err", err)
		os.Exit(1)
	}
	slog.Info("bucket configured", "bucket", st.Bucket(), "lifecycle", lifecycle)
}
