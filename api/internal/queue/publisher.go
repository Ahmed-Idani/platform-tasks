// Package queue publishes task messages to RabbitMQ.
//
// The topology (exchanges, queues, dead-letter wiring) is infrastructure, declared in
// infra/rabbitmq/definitions.json. This package never declares anything: it publishes
// to the "tasks" exchange with the task type as routing key.
package queue

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sync"

	amqp "github.com/rabbitmq/amqp091-go"
)

const Exchange = "tasks"

// Queue names per task type. Must match definitions.json.
func MainQueue(taskType string) string  { return "tasks." + taskType }
func RetryQueue(taskType string) string { return "tasks." + taskType + ".retry" }
func DeadQueue(taskType string) string  { return "tasks." + taskType + ".dlq" }

// Message is the contract with the worker: only the id. Everything else is read
// from Postgres when the worker claims the task, so Postgres stays the single
// source of truth.
type Message struct {
	TaskID string `json:"task_id"`
}

// Publisher holds one connection + one channel in confirm mode. A mutex serializes
// publishes: simple, and more than fast enough for task submission rates.
type Publisher struct {
	url string

	mu      sync.Mutex
	conn    *amqp.Connection
	ch      *amqp.Channel
	returns chan amqp.Return
}

func NewPublisher(url string) *Publisher {
	return &Publisher{url: url}
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
	p.returns = ch.NotifyReturn(make(chan amqp.Return, 1))
	return nil
}

func (p *Publisher) ensureLocked() error {
	if p.ch == nil || p.ch.IsClosed() {
		return p.connectLocked()
	}
	return nil
}

// Publish returns only once RabbitMQ has confirmed the message is stored in a queue
// (persistent, durable). A nil error means: this message survives a broker restart.
func (p *Publisher) Publish(ctx context.Context, taskType, taskID string) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	if err := p.ensureLocked(); err != nil {
		return err
	}

	body, err := json.Marshal(Message{TaskID: taskID})
	if err != nil {
		return err
	}
	// mandatory: if no queue is bound for this routing key, the broker hands the
	// message back (basic.return) instead of silently dropping it.
	confirm, err := p.ch.PublishWithDeferredConfirmWithContext(ctx, Exchange, taskType, true, false, amqp.Publishing{
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
	// The broker sends basic.return before the confirm, so if it was returned it is
	// already waiting here.
	select {
	case ret := <-p.returns:
		return fmt.Errorf("unroutable: no queue bound to %s/%s (%s)", Exchange, ret.RoutingKey, ret.ReplyText)
	default:
		return nil
	}
}

type QueueDepth struct {
	Ready     int `json:"ready"`     // waiting for a worker
	Consumers int `json:"consumers"` // workers attached
}

// Depths reads the message count of the main, retry and dead-letter queues with a
// passive declare (read-only: it fails if the queue doesn't exist, never creates it).
func (p *Publisher) Depths(taskType string) (map[string]QueueDepth, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if err := p.ensureLocked(); err != nil {
		return nil, err
	}
	// Separate channel: a failed passive declare closes its channel, and that must
	// not take down the publishing channel.
	ch, err := p.conn.Channel()
	if err != nil {
		return nil, err
	}
	defer ch.Close()

	out := map[string]QueueDepth{}
	for key, name := range map[string]string{
		"main":  MainQueue(taskType),
		"retry": RetryQueue(taskType),
		"dlq":   DeadQueue(taskType),
	} {
		q, err := ch.QueueDeclarePassive(name, true, false, false, false, nil)
		if err != nil {
			return nil, fmt.Errorf("inspect %s: %w", name, err)
		}
		out[key] = QueueDepth{Ready: q.Messages, Consumers: q.Consumers}
	}
	return out, nil
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
