// Package queue publishes task messages to RabbitMQ.
package queue

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sync"

	amqp "github.com/rabbitmq/amqp091-go"
)

// Queue name per task type. Must match what the worker consumes.
func QueueFor(taskType string) string {
	return "tasks." + taskType
}

// Message is the contract with the worker: only the id. Everything else is read
// from Postgres when the worker claims the task, so Postgres stays the single
// source of truth.
type Message struct {
	TaskID string `json:"task_id"`
}

// Publisher holds one connection + one channel in confirm mode. A mutex serializes
// publishes: simple, and more than fast enough for task submission rates.
type Publisher struct {
	url      string
	declared map[string]bool

	mu   sync.Mutex
	conn *amqp.Connection
	ch   *amqp.Channel
}

func NewPublisher(url string) *Publisher {
	return &Publisher{url: url, declared: map[string]bool{}}
}

// Connect opens the connection now, so a bad URL fails at startup, not on the first request.
func (p *Publisher) Connect() error {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.connectLocked()
}

func (p *Publisher) connectLocked() error {
	p.closeLocked()
	conn, err := amqp.Dial(p.url)
	if err != nil {
		return fmt.Errorf("dial rabbitmq: %w", err)
	}
	ch, err := conn.Channel()
	if err != nil {
		conn.Close()
		return fmt.Errorf("open channel: %w", err)
	}
	// Confirm mode: the broker tells us when a message is safely stored.
	if err := ch.Confirm(false); err != nil {
		conn.Close()
		return fmt.Errorf("enable confirms: %w", err)
	}
	p.conn, p.ch = conn, ch
	p.declared = map[string]bool{}
	return nil
}

// Publish returns only once RabbitMQ has confirmed the message is stored (persistent,
// durable queue). A nil error means: this message survives a broker restart.
func (p *Publisher) Publish(ctx context.Context, taskType, taskID string) error {
	p.mu.Lock()
	defer p.mu.Unlock()

	if p.ch == nil || p.ch.IsClosed() {
		if err := p.connectLocked(); err != nil {
			return err
		}
	}

	queue := QueueFor(taskType)
	if !p.declared[queue] {
		// Same arguments as the worker's declare, or RabbitMQ rejects it (PRECONDITION_FAILED).
		if _, err := p.ch.QueueDeclare(queue, true, false, false, false, nil); err != nil {
			return fmt.Errorf("declare %s: %w", queue, err)
		}
		p.declared[queue] = true
	}

	body, err := json.Marshal(Message{TaskID: taskID})
	if err != nil {
		return err
	}
	confirm, err := p.ch.PublishWithDeferredConfirmWithContext(ctx, "", queue, false, false, amqp.Publishing{
		ContentType:  "application/json",
		DeliveryMode: amqp.Persistent,
		MessageId:    taskID,
		Body:         body,
	})
	if err != nil {
		return fmt.Errorf("publish: %w", err)
	}
	acked, err := confirm.WaitContext(ctx)
	if err != nil {
		return fmt.Errorf("wait for confirm: %w", err)
	}
	if !acked {
		return errors.New("broker nacked the message")
	}
	return nil
}

func (p *Publisher) Close() {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.closeLocked()
}

func (p *Publisher) closeLocked() {
	if p.ch != nil {
		p.ch.Close()
		p.ch = nil
	}
	if p.conn != nil {
		p.conn.Close()
		p.conn = nil
	}
}
