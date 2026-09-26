package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"platform-tasks/api/internal/httpapi"
	"platform-tasks/api/internal/metrics"
	"platform-tasks/api/internal/queue"
	"platform-tasks/api/internal/storage"
	"platform-tasks/api/internal/store"
	"platform-tasks/api/internal/sweeper"
	"platform-tasks/api/internal/webhooks"
)

func main() {
	slog.SetDefault(slog.New(slog.NewTextHandler(os.Stdout, nil)))
	if err := run(); err != nil {
		slog.Error("fatal", "err", err)
		os.Exit(1)
	}
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func run() error {
	addr := getenv("HTTP_ADDR", ":8080")
	databaseURL := getenv("DATABASE_URL", "postgres://admin:admin@localhost:5433/tasks?sslmode=disable")
	rabbitURL := getenv("RABBITMQ_URL", "amqp://admin:admin@localhost:5672/%2F")
	corsOrigins := strings.Split(getenv("CORS_ORIGINS", "http://localhost:5173"), ",")

	// ctx is cancelled on the first SIGINT/SIGTERM. That is the shutdown trigger.
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	pool, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		return fmt.Errorf("postgres config: %w", err)
	}
	defer pool.Close()
	if err := pool.Ping(ctx); err != nil {
		return fmt.Errorf("postgres ping: %w", err)
	}

	pub := queue.NewPublisher(rabbitURL)
	if err := pub.Connect(); err != nil {
		return err
	}
	defer pub.Close()

	objects, err := storage.New(ctx)
	if err != nil {
		return err
	}

	st := store.New(pool)
	metrics.RegisterTaskCounts(st.CountByStatus)

	var wg sync.WaitGroup
	wg.Go(func() { sweeper.Run(ctx, st, pub) })
	wg.Go(func() { webhooks.Run(ctx, st, objects) })

	srv := &http.Server{
		Addr:              addr,
		Handler:           httpapi.New(st, pub, corsOrigins).Handler(),
		ReadHeaderTimeout: 5 * time.Second,
	}
	serveErr := make(chan error, 1)
	go func() { serveErr <- srv.ListenAndServe() }()
	slog.Info("api listening", "addr", addr)

	select {
	case err := <-serveErr:
		return err // e.g. port already in use
	case <-ctx.Done():
	}

	// Graceful shutdown:
	//   1. Shutdown closes the listener: no new connections.
	//   2. It waits for in-flight requests to finish (up to 30s).
	//   3. The sweeper and webhook dispatcher have seen ctx.Done() and exit; wg.Wait makes sure.
	//   4. Deferred calls close RabbitMQ, then the Postgres pool, after nothing uses them.
	slog.Info("shutting down: draining in-flight requests")
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	err = srv.Shutdown(shutdownCtx)
	wg.Wait()
	if err != nil && !errors.Is(err, http.ErrServerClosed) {
		return fmt.Errorf("shutdown: %w", err)
	}
	slog.Info("bye")
	return nil
}
