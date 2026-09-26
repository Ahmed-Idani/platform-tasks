import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ChevronDown, CircleAlert, FileText, RotateCw, Webhook, X } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { toast } from "sonner";
import { ApiError, MAX_ATTEMPTS, RETRY_DELAY_S, type Task, api, displayStatus, isActive, webhookState } from "../lib/api";
import { between, duration, num, timestamp, useNow } from "../lib/format";
import { Button, CopyButton, Kbd, Skeleton, Spinner, StatusBadge } from "./ui";

// ---- layout helpers --------------------------------------------------------------

function Section({ title, right, children }: { title: string; right?: ReactNode; children: ReactNode }) {
  return (
    <section className="border-b border-border px-5 py-5 last:border-b-0">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h3 className="text-xs font-medium tracking-wide text-muted uppercase">{title}</h3>
        {right}
      </div>
      {children}
    </section>
  );
}

function Metric({ label, value, mono = true, hint }: { label: string; value: ReactNode; mono?: boolean; hint?: string }) {
  return (
    <div className="min-w-0 bg-surface px-3.5 py-3" title={hint}>
      <div className="mb-1 text-xs text-muted">{label}</div>
      <div className={clsx("truncate text-sm text-fg", mono && "tabular font-mono")} title={typeof value === "string" ? value : undefined}>
        {value}
      </div>
    </div>
  );
}

// ---- timeline --------------------------------------------------------------------

type Step = {
  label: string;
  at: string | null;
  state: "done" | "current" | "todo" | "error";
  detail?: ReactNode;
  gap?: number | null; // ms since the previous step
};

function steps(task: Task, now: number): Step[] {
  const s = task.status;
  const finished = s === "completed" || s === "failed";
  const list: Step[] = [
    { label: "Created", at: task.created_at, state: "done", detail: "Stored in Postgres as pending" },
    {
      label: "Queued",
      at: task.queued_at,
      state: s !== "pending" ? "done" : "current",
      detail:
        displayStatus(task) === "retrying"
          ? `Back in the queue after attempt ${task.attempt} failed (retry delay ${RETRY_DELAY_S}s)`
          : s !== "pending"
            ? "Published to RabbitMQ"
            : "Waiting for publish (sweeper retries every 30s)",
      gap: between(task.created_at, task.queued_at),
    },
    {
      label: "Running",
      at: task.started_at,
      state: task.started_at && s !== "queued" ? "done" : s === "queued" ? "current" : "todo",
      detail: task.started_at ? (
        <>
          Claimed by <span className="font-mono text-fg">{task.worker_id}</span> · attempt {task.attempt} of {MAX_ATTEMPTS}
        </>
      ) : (
        "Waiting for a free worker"
      ),
      gap: between(task.queued_at, s === "queued" ? null : task.started_at, s === "queued" ? now : undefined),
    },
    {
      label: s === "failed" ? "Failed" : "Completed",
      at: task.completed_at,
      state: s === "failed" ? "error" : finished ? "done" : s === "running" ? "current" : "todo",
      detail: s === "running" ? "Generating…" : finished ? "Result written to Postgres" : undefined,
      gap: between(task.started_at, task.completed_at, s === "running" ? now : undefined),
    },
  ];
  const wh = webhookState(task);
  if (wh !== "none") {
    list.push({
      label: { delivered: "Webhook delivered", retrying: "Webhook retrying", gave_up: "Webhook failed" }[wh as string] ?? "Webhook",
      at: task.webhook_sent_at,
      state: wh === "delivered" ? "done" : wh === "gave_up" ? "error" : wh === "retrying" || wh === "sending" ? "current" : "todo",
      detail:
        wh === "delivered"
          ? task.webhook_attempts > 1
            ? `POST callback_url, delivered on attempt ${task.webhook_attempts} from object storage`
            : "POST callback_url"
          : wh === "retrying"
            ? `Client unreachable. Payload parked in object storage, attempt ${task.webhook_attempts + 1} at ${timestamp(task.webhook_next_at)}`
            : wh === "gave_up"
              ? task.webhook_error
              : "POST callback_url",
      gap: between(task.completed_at, task.webhook_sent_at),
    });
  }
  return list;
}

function Timeline({ task, now }: { task: Task; now: number }) {
  const list = steps(task, now);
  return (
    <ol className="relative">
      {list.map((step, i) => (
        <li key={step.label} className="relative flex gap-3 pb-5 last:pb-0">
          {i < list.length - 1 && (
            <span
              className={clsx(
                "absolute top-4 bottom-0 left-[5px] w-px",
                step.state === "done" ? "bg-border-strong" : "border-l border-dashed border-border-strong bg-transparent",
              )}
            />
          )}
          <span className="relative mt-1 flex size-[11px] shrink-0 items-center justify-center">
            <span
              className={clsx(
                "size-[11px] rounded-full border-2",
                step.state === "done" && "border-fg bg-fg",
                step.state === "error" && "border-red bg-red",
                step.state === "current" && "animate-pulse-dot border-blue bg-blue",
                step.state === "todo" && "border-border-strong bg-surface",
              )}
            />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline justify-between gap-3">
              <span className={clsx("text-sm font-medium", step.state === "todo" ? "text-faint" : step.state === "error" ? "text-red" : "text-fg")}>
                {step.label}
              </span>
              <span className="tabular shrink-0 font-mono text-xs text-muted">{step.at ? timestamp(step.at) : ""}</span>
            </div>
            <div className="mt-0.5 flex items-baseline justify-between gap-3">
              {step.detail && <span className={clsx("min-w-0 text-xs break-words", step.state === "error" ? "text-red" : "text-muted")}>{step.detail}</span>}
              {step.gap != null && step.state !== "todo" && (
                <span
                  className={clsx(
                    "tabular shrink-0 rounded px-1.5 font-mono text-[11px] leading-[18px]",
                    step.state === "current" ? "bg-blue-soft text-blue" : "bg-hover text-muted",
                  )}
                >
                  +{duration(step.gap)}
                </span>
              )}
            </div>
          </div>
        </li>
      ))}
    </ol>
  );
}

// ---- input text ------------------------------------------------------------------

function InputText({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > 480;
  return (
    <div className="rounded-md border border-border bg-subtle">
      <div className="relative">
        <pre
          className={clsx(
            "px-3.5 py-3 font-sans text-[13px] leading-relaxed whitespace-pre-wrap text-muted",
            !open && long && "max-h-40 overflow-hidden",
          )}
        >
          {text}
        </pre>
        {!open && long && (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 rounded-b-md bg-gradient-to-t from-subtle to-transparent" />
        )}
      </div>
      {long && (
        <button
          onClick={() => setOpen((o) => !o)}
          className="flex w-full items-center justify-center gap-1 border-t border-border py-1.5 text-xs text-muted hover:text-fg"
        >
          {open ? "Show less" : "Show full text"}
          <ChevronDown className={clsx("size-3.5 transition-transform", open && "rotate-180")} />
        </button>
      )}
    </div>
  );
}

// ---- panel -----------------------------------------------------------------------

function PanelSkeleton() {
  return (
    <div className="space-y-6 px-5 py-5">
      <div className="space-y-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="flex gap-3">
            <Skeleton className="size-3 rounded-full" />
            <div className="flex-1 space-y-2">
              <Skeleton className="h-3.5 w-24" />
              <Skeleton className="h-3 w-48" />
            </div>
          </div>
        ))}
      </div>
      <Skeleton className="h-28 w-full" />
      <div className="grid grid-cols-3 gap-2">
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="h-14 w-full" />
        ))}
      </div>
    </div>
  );
}

function Body({ task, now }: { task: Task; now: number }) {
  const r = task.result;
  const words = r?.summary ? r.summary.trim().split(/\s+/).length : 0;
  const maxWords = task.parameters?.max_words;

  return (
    <>
      <Section title="Timeline">
        <Timeline task={task} now={now} />
      </Section>

      {task.error && task.status === "failed" && (
        <Section title="Error">
          <div className="flex gap-2.5 rounded-md border border-red/25 bg-red-soft px-3.5 py-3 text-[13px] text-red">
            <CircleAlert className="mt-px size-4 shrink-0" />
            <div className="min-w-0">
              <span className="font-mono break-words">{task.error}</span>
              <p className="mt-1.5 font-sans text-xs opacity-80">
                The message was dead-lettered to <span className="font-mono">tasks.llm_inference.dlq</span>.
              </p>
            </div>
          </div>
        </Section>
      )}
      {task.error && (task.status === "queued" || task.status === "running") && (
        <Section title={task.status === "running" ? "Previous attempt" : "Retrying"}>
          <div className="flex gap-2.5 rounded-md border border-amber/25 bg-amber-soft px-3.5 py-3 text-[13px] text-amber">
            <RotateCw className="mt-px size-4 shrink-0" />
            <div className="min-w-0">
              <span className="font-mono break-words">{task.error}</span>
              <p className="mt-1.5 font-sans text-xs opacity-80">
                {task.status === "running"
                  ? `Now on attempt ${task.attempt} of ${MAX_ATTEMPTS}.`
                  : `Waiting ${RETRY_DELAY_S}s in tasks.llm_inference.retry, then attempt ${task.attempt + 1} of ${MAX_ATTEMPTS}.`}
              </p>
            </div>
          </div>
        </Section>
      )}

      <Section
        title="Summary"
        right={
          r?.summary && (
            <div className="flex items-center gap-2 text-xs text-muted">
              <span className="tabular">
                {words}
                {maxWords ? ` / ${maxWords}` : ""} words
              </span>
              <CopyButton value={r.summary} label="Copy summary" />
            </div>
          )
        }
      >
        {r?.summary ? (
          <p className="text-[15px] leading-relaxed text-fg">{r.summary}</p>
        ) : task.status === "running" ? (
          <div className="space-y-2">
            <div className="mb-3 flex items-center gap-2 text-[13px] text-blue">
              <Spinner className="size-3.5 text-blue" /> The model is generating. This takes a while on CPU.
            </div>
            <Skeleton className="h-3.5 w-full" />
            <Skeleton className="h-3.5 w-11/12" />
            <Skeleton className="h-3.5 w-4/6" />
          </div>
        ) : (
          <p className="text-[13px] text-faint">
            {isActive(task.status) ? "Available once the task completes." : "No summary was produced."}
          </p>
        )}
      </Section>

      <Section title="Details">
        <div className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-border bg-border sm:grid-cols-3">
          <Metric label="Input tokens" value={num(r?.input_tokens)} />
          <Metric label="Output tokens" value={num(r?.output_tokens)} />
          <Metric label="Tokens / sec" value={r?.tokens_per_second?.toFixed(2) ?? "–"} hint="output tokens ÷ inference time" />
          <Metric label="Inference" value={r ? duration(r.seconds * 1000) : "–"} hint="time inside the model" />
          <Metric
            label="Queue wait"
            value={duration(between(task.queued_at, task.status === "queued" ? null : task.started_at, task.status === "queued" ? now : undefined))}
            hint="queued → running"
          />
          <Metric label="Attempt" value={task.attempt ? `${task.attempt} of ${MAX_ATTEMPTS}` : "–"} />
          <Metric label="Model" value={r?.model ?? "–"} />
          <Metric label="Worker" value={task.worker_id ?? "–"} />
          <Metric label="Max words" value={maxWords ?? "–"} />
        </div>
      </Section>

      <Section title="Webhook">
        <WebhookSection task={task} now={now} />
      </Section>

      {task.parameters && (
        <Section
          title="Input"
          right={<span className="tabular text-xs text-muted">{num(task.parameters.text.length)} chars</span>}
        >
          <InputText text={task.parameters.text} />
        </Section>
      )}

      <RawJson task={task} />
    </>
  );
}

function WebhookSection({ task, now }: { task: Task; now: number }) {
  const qc = useQueryClient();
  const resend = useMutation({
    mutationFn: () => api.resendWebhook(task.task_id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["task", task.task_id] });
      toast.success("Webhook rescheduled", { description: "The API dispatcher delivers it within 15s." });
    },
    onError: (e) => toast.error("Couldn't resend", { description: e.message }),
  });
  const state = webhookState(task);
  if (state === "none") {
    return <p className="text-[13px] text-faint">No callback URL. Poll GET /v1/tasks/{"{id}"} for the result.</p>;
  }
  const nextIn = task.webhook_next_at ? new Date(task.webhook_next_at).getTime() - now : null;

  return (
    <div className="space-y-2.5">
      <div className="flex items-center gap-2 rounded-md border border-border bg-subtle px-3 py-2">
        <Webhook className="size-4 shrink-0 text-muted" />
        <span className="min-w-0 flex-1 truncate font-mono text-[13px]" title={task.callback_url!}>
          {task.callback_url}
        </span>
        <CopyButton value={task.callback_url!} label="Copy URL" />
      </div>

      {state === "delivered" && (
        <p className="text-[13px] text-green">
          Delivered {timestamp(task.webhook_sent_at)}
          {task.webhook_attempts > 1 && <span className="text-muted"> · attempt {task.webhook_attempts}, sent from object storage</span>}
        </p>
      )}
      {state === "waiting" && <p className="text-[13px] text-muted">The worker POSTs the result here when the task finishes.</p>}
      {state === "sending" && (
        <p className="inline-flex items-center gap-1.5 text-[13px] text-muted">
          <Spinner className="size-3.5" /> Sending…
        </p>
      )}

      {(state === "retrying" || state === "gave_up") && (
        <div
          className={clsx(
            "rounded-md border px-3.5 py-3 text-[13px]",
            state === "retrying" ? "border-amber/25 bg-amber-soft text-amber" : "border-red/25 bg-red-soft text-red",
          )}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 space-y-1">
              <p className="font-medium">
                {state === "retrying"
                  ? `Client unreachable. Next attempt ${nextIn != null && nextIn > 0 ? `in ${duration(nextIn)}` : "any moment"}`
                  : `Gave up after ${task.webhook_attempts} attempts`}
              </p>
              <p className="font-mono text-xs break-words opacity-90">{task.webhook_error}</p>
              {task.webhook_ref && (
                <p className="text-xs opacity-80">
                  Payload parked at <span className="font-mono">{task.webhook_ref}</span>. The result is not recomputed.
                </p>
              )}
            </div>
            {task.webhook_ref && (
              <Button size="sm" onClick={() => resend.mutate()} loading={resend.isPending} className="shrink-0">
                Resend now
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function RawJson({ task }: { task: Task }) {
  const [open, setOpen] = useState(false);
  const json = JSON.stringify(task, null, 2);
  return (
    <section className="px-5 py-4">
      <div className="flex items-center justify-between">
        <button onClick={() => setOpen((o) => !o)} className="flex items-center gap-1.5 text-xs font-medium tracking-wide text-muted uppercase hover:text-fg">
          <ChevronDown className={clsx("size-3.5 transition-transform", !open && "-rotate-90")} />
          Raw JSON
        </button>
        {open && <CopyButton value={json} label="Copy JSON" />}
      </div>
      {open && (
        <pre className="mt-3 max-h-96 overflow-auto rounded-md border border-border bg-subtle p-3.5 font-mono text-xs leading-relaxed text-muted">
          {json}
        </pre>
      )}
    </section>
  );
}

export function TaskPanel({ id, onClose }: { id: string; onClose: () => void }) {
  const query = useQuery({
    queryKey: ["task", id],
    queryFn: () => api.get(id),
    // Poll while something can still change: the status, or the webhook after it.
    refetchInterval: (q) => {
      const t = q.state.data;
      if (!t) return false;
      if (isActive(t.status)) return 1000;
      const wh = webhookState(t);
      if (wh === "sending") return 1000;
      if (wh === "retrying") return 5000; // retries are minutes to hours apart
      return false;
    },
    retry: (n, err) => !(err instanceof ApiError && err.status === 404) && n < 2,
  });
  const task = query.data;
  const now = useNow(250, !!task && (isActive(task.status) || webhookState(task) === "retrying"));
  const qc = useQueryClient();
  const retry = useMutation({
    mutationFn: () => api.retry(id),
    onSuccess: (t) => {
      qc.setQueryData(["task", id], t);
      qc.invalidateQueries({ queryKey: ["tasks"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      toast.success("Task restarted", { description: "Back to attempt 1, published to RabbitMQ." });
    },
    onError: (e) => toast.error("Couldn't retry", { description: e.message }),
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <div className="fixed inset-0 z-30 animate-fade-in bg-black/20 dark:bg-black/50" onClick={onClose} />
      <aside
        role="dialog"
        aria-label="Task details"
        className="fixed inset-y-0 right-0 z-40 flex w-full max-w-[600px] animate-slide-in flex-col border-l border-border bg-surface shadow-2xl"
      >
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-5">
          {task ? <StatusBadge status={displayStatus(task)} pill /> : <Skeleton className="h-6 w-24 rounded-full" />}
          <div className="flex min-w-0 items-center gap-1">
            <FileText className="size-4 shrink-0 text-faint" />
            <span className="truncate font-mono text-[13px]" title={id}>
              {id}
            </span>
            <CopyButton value={id} label="Copy task ID" />
          </div>
          <div className="ml-auto flex items-center gap-2">
            {query.isFetching && task && <Spinner className="size-3.5" />}
            {task && (task.status === "failed" || task.status === "cancelled") && (
              <Button size="sm" onClick={() => retry.mutate()} loading={retry.isPending}>
                {!retry.isPending && <RotateCw className="size-3.5" />}
                Retry
              </Button>
            )}
            <Kbd>Esc</Kbd>
            <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close" className="px-1.5">
              <X className="size-4" />
            </Button>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto">
          {query.isPending ? (
            <PanelSkeleton />
          ) : query.isError ? (
            <div className="flex flex-col items-center gap-2 px-6 py-20 text-center">
              <CircleAlert className="size-6 text-red" />
              <p className="text-sm font-medium">
                {query.error instanceof ApiError && query.error.status === 404 ? "Task not found" : "Couldn't load this task"}
              </p>
              <p className="text-[13px] text-muted">{query.error.message}</p>
              <Button size="sm" className="mt-2" onClick={() => query.refetch()}>
                Try again
              </Button>
            </div>
          ) : (
            <Body task={task!} now={now} />
          )}
        </div>
      </aside>
    </>
  );
}
