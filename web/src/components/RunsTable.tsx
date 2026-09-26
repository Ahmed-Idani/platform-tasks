import clsx from "clsx";
import { ArrowRight, CircleAlert, Clock3, Inbox, RefreshCw, Webhook } from "lucide-react";
import type { ReactNode } from "react";
import { MAX_ATTEMPTS, type Status, type Task, displayStatus, isActive } from "../lib/api";
import { between, duration, num, relative, shortId, timestamp, useNow } from "../lib/format";
import { Button, CopyButton, Skeleton, StatusBadge, statusMeta } from "./ui";

const COLS = [
  { label: "Status", className: "w-[132px]" },
  { label: "Task", className: "w-[170px]" },
  { label: "Created", className: "w-[110px]" },
  { label: "Queue wait", className: "w-[100px] text-right", title: "queued → running" },
  { label: "Duration", className: "w-[100px] text-right", title: "running → finished" },
  { label: "Tokens", className: "w-[120px] text-right", title: "input → output" },
  { label: "Attempt", className: "w-[76px] text-right" },
  { label: "Worker", className: "w-[140px]" },
  { label: "Webhook", className: "w-[88px] text-center" },
];

function WebhookCell({ task }: { task: Task }) {
  if (!task.callback_url) return <span className="text-faint">–</span>;
  if (task.webhook_sent_at)
    return (
      <span title={`Delivered ${timestamp(task.webhook_sent_at)}`} className="inline-flex text-green">
        <Webhook className="size-4" />
      </span>
    );
  if (task.webhook_error)
    return (
      <span title={task.webhook_error} className="inline-flex text-red">
        <CircleAlert className="size-4" />
      </span>
    );
  return (
    <span title="Sent when the task finishes" className="inline-flex text-faint">
      <Clock3 className="size-4" />
    </span>
  );
}

function Row({ task, now, selected, onSelect }: { task: Task; now: number; selected: boolean; onSelect: () => void }) {
  const live = isActive(task.status);
  // While queued (incl. retrying), started_at may belong to a previous attempt: measure to now.
  const queued = task.status === "queued";
  const wait = between(task.queued_at, queued ? null : task.started_at, queued ? now : undefined);
  const run = between(task.started_at, task.completed_at, task.status === "running" ? now : undefined);

  return (
    <tr
      onClick={onSelect}
      aria-selected={selected}
      className={clsx(
        "group cursor-pointer border-b border-border text-[13px] transition-colors last:border-b-0",
        selected ? "bg-hover" : "hover:bg-subtle",
      )}
    >
      <td className="relative py-2.5 pr-3 pl-4">
        {selected && <span className="absolute inset-y-0 left-0 w-0.5 bg-fg" />}
        <StatusBadge status={displayStatus(task)} />
      </td>
      <td className="py-2.5 pr-3">
        <span className="flex items-center gap-1">
          <span className="font-mono text-fg" title={task.task_id}>
            {shortId(task.task_id)}
          </span>
          <CopyButton value={task.task_id} label="Copy task ID" className="opacity-0 group-hover:opacity-100" />
        </span>
      </td>
      <td className="py-2.5 pr-3 text-muted" title={timestamp(task.created_at)}>
        {relative(task.created_at, now)}
      </td>
      <td className={clsx("tabular py-2.5 pr-3 text-right font-mono", task.status === "queued" ? "text-amber" : "text-muted")}>
        {duration(wait)}
      </td>
      <td className={clsx("tabular py-2.5 pr-3 text-right font-mono", task.status === "running" ? "text-blue" : "text-fg")}>
        {duration(run)}
      </td>
      <td className="tabular py-2.5 pr-3 text-right font-mono text-muted">
        {task.result ? (
          <span className="inline-flex items-center gap-1">
            {num(task.result.input_tokens)}
            <ArrowRight className="size-3 text-faint" />
            <span className="text-fg">{num(task.result.output_tokens)}</span>
          </span>
        ) : (
          "–"
        )}
      </td>
      <td className="tabular py-2.5 pr-3 text-right font-mono">
        <span
          title={task.attempt ? `attempt ${task.attempt} of ${MAX_ATTEMPTS}` : undefined}
          className={task.attempt > 1 ? "rounded bg-amber-soft px-1.5 py-0.5 text-amber" : "text-muted"}
        >
          {task.attempt === 0 ? "–" : `${task.attempt}/${MAX_ATTEMPTS}`}
        </span>
      </td>
      <td className="max-w-[140px] truncate py-2.5 pr-3 font-mono text-muted" title={task.worker_id ?? undefined}>
        {task.worker_id ?? "–"}
      </td>
      <td className="py-2.5 pr-4 text-center">
        <WebhookCell task={task} />
        {live && <span className="sr-only">in progress</span>}
      </td>
    </tr>
  );
}

function SkeletonRows() {
  const widths = ["w-20", "w-28", "w-14", "w-10", "w-12", "w-20", "w-6", "w-24", "w-4"];
  return (
    <>
      {Array.from({ length: 8 }, (_, i) => (
        <tr key={i} className="border-b border-border last:border-b-0">
          {widths.map((w, j) => (
            <td key={j} className={clsx("py-3.5 pr-3", j === 0 && "pl-4")}>
              <Skeleton className={clsx("h-3.5", w, [3, 4, 5, 6].includes(j) && "ml-auto", j === 8 && "mx-auto")} />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

function Message({ icon, title, body, action }: { icon: ReactNode; title: string; body: string; action?: ReactNode }) {
  return (
    <tr>
      <td colSpan={COLS.length}>
        <div className="flex flex-col items-center justify-center gap-2 px-6 py-20 text-center">
          <div className="mb-1 flex size-10 items-center justify-center rounded-full border border-border bg-subtle text-muted">
            {icon}
          </div>
          <p className="text-sm font-medium">{title}</p>
          <p className="max-w-sm text-[13px] text-muted">{body}</p>
          {action && <div className="mt-3">{action}</div>}
        </div>
      </td>
    </tr>
  );
}

export function RunsTable({
  tasks,
  isPending,
  error,
  filter,
  selectedId,
  onSelect,
  onRetry,
  onNewTask,
}: {
  tasks: Task[] | undefined;
  isPending: boolean;
  error: Error | null;
  filter: Status | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onRetry: () => void;
  onNewTask: () => void;
}) {
  const anyLive = !!tasks?.some((t) => isActive(t.status));
  const now = useNow(1000, anyLive || !!tasks?.length);

  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-surface">
      <table className="w-full min-w-[1080px] table-fixed border-collapse">
        <thead>
          <tr className="border-b border-border bg-subtle text-left text-xs font-medium text-muted">
            {COLS.map((c, i) => (
              <th key={c.label} title={c.title} className={clsx("h-9 pr-3 font-medium", i === 0 && "pl-4", c.className)}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {error && !tasks ? (
            <Message
              icon={<CircleAlert className="size-5 text-red" />}
              title="Couldn't load tasks"
              body={error.message}
              action={
                <Button size="sm" onClick={onRetry}>
                  <RefreshCw className="size-3.5" /> Try again
                </Button>
              }
            />
          ) : isPending ? (
            <SkeletonRows />
          ) : !tasks?.length ? (
            <Message
              icon={<Inbox className="size-5" />}
              title={filter ? `No ${statusMeta[filter].label.toLowerCase()} tasks` : "No tasks yet"}
              body={
                filter
                  ? "Tasks show up here as soon as they reach this status."
                  : "Submit a text to summarize. It is queued in RabbitMQ and picked up by the next free worker."
              }
              action={
                !filter && (
                  <Button variant="primary" size="sm" onClick={onNewTask}>
                    Create your first task
                  </Button>
                )
              }
            />
          ) : (
            tasks.map((t) => (
              <Row key={t.task_id} task={t} now={now} selected={t.task_id === selectedId} onSelect={() => onSelect(t.task_id)} />
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
