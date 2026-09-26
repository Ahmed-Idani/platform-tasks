// Typed client for the Go API. Types mirror api/internal/store/store.go.

export type Status = "pending" | "queued" | "running" | "completed" | "failed" | "cancelled";

export const STATUSES: Status[] = ["pending", "queued", "running", "completed", "failed", "cancelled"];

export const ACTIVE: Status[] = ["pending", "queued", "running"];

export interface TaskResult {
  model: string;
  summary: string;
  input_tokens: number;
  output_tokens: number;
  seconds: number;
  tokens_per_second: number | null;
}

export interface Task {
  task_id: string;
  task_type: string;
  status: Status;
  parameters?: { text: string; max_words: number };
  callback_url: string | null;
  attempt: number;
  worker_id: string | null;
  result: TaskResult | null;
  error: string | null;
  created_at: string;
  queued_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  webhook_sent_at: string | null;
  webhook_error: string | null;
  webhook_attempts: number;
  webhook_next_at: string | null;
  webhook_ref: string | null;
}

export interface QueueDepth {
  ready: number;
  consumers: number;
}

export interface Stats {
  counts: Record<Status, number>;
  total: number;
  // null when RabbitMQ is unreachable
  queues: { main: QueueDepth; retry: QueueDepth; dlq: QueueDepth } | null;
}

export interface CreateTaskInput {
  text: string;
  max_words: number;
  callback_url: string | null;
}

// Must match MaxChars in api/internal/httpapi/tasks.go.
export const MAX_CHARS = 24_000;
export const MAX_WORDS = 1000;
// Must match MAX_ATTEMPTS in worker/worker.py and the retry queue TTL in definitions.json.
export const MAX_ATTEMPTS = 3;
export const RETRY_DELAY_S = 30;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

const BASE = import.meta.env.VITE_API_URL ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError("Can't reach the API. Is it running on :8080?", 0);
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiError(body?.error ?? `HTTP ${res.status}`, res.status);
  }
  return body as T;
}

export const api = {
  health: () => request<{ status: string }>("/healthz"),
  stats: () => request<Stats>("/v1/stats"),
  list: (status?: Status | null, limit = 100) => {
    const q = new URLSearchParams({ limit: String(limit) });
    if (status) q.set("status", status);
    return request<{ tasks: Task[] }>(`/v1/tasks?${q}`).then((r) => r.tasks);
  },
  get: (id: string) => request<Task>(`/v1/tasks/${id}`),
  create: (input: CreateTaskInput) =>
    request<Task>("/v1/tasks", {
      method: "POST",
      body: JSON.stringify({
        task_type: "llm_inference",
        parameters: { text: input.text, max_words: input.max_words },
        callback_url: input.callback_url,
      }),
    }),
  retry: (id: string) => request<Task>(`/v1/tasks/${id}/retry`, { method: "POST" }),
  resendWebhook: (id: string) => request<{ status: string }>(`/v1/tasks/${id}/webhook/resend`, { method: "POST" }),
};

export const isActive = (s: Status) => ACTIVE.includes(s);

/** What the UI shows: a queued task that already failed an attempt is waiting in the retry queue. */
export type DisplayStatus = Status | "retrying";

export const displayStatus = (t: Task): DisplayStatus =>
  t.status === "queued" && t.attempt > 0 && t.error ? "retrying" : t.status;

/** Webhook delivery, as the UI tells it. */
export type WebhookState = "none" | "waiting" | "sending" | "delivered" | "retrying" | "gave_up";

export function webhookState(t: Task): WebhookState {
  if (!t.callback_url) return "none";
  if (t.webhook_sent_at) return "delivered";
  if (t.webhook_ref && t.webhook_next_at) return "retrying"; // parked in object storage
  if (t.webhook_attempts > 0 || t.webhook_error) return "gave_up";
  return isActive(t.status) ? "waiting" : "sending";
}
