import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";
import { Header } from "./components/Header";
import { NewTaskDialog } from "./components/NewTaskDialog";
import { RunsTable } from "./components/RunsTable";
import { StatusTabs } from "./components/StatusTabs";
import { TaskPanel } from "./components/TaskPanel";
import { Spinner } from "./components/ui";
import { STATUSES, type Status, api } from "./lib/api";

// Filter and selected task live in the URL (?status=failed&task=…), so a view can be
// reloaded or shared.
function readUrl() {
  const q = new URLSearchParams(location.search);
  const s = q.get("status") as Status | null;
  return { status: s && STATUSES.includes(s) ? s : null, task: q.get("task") };
}

function writeUrl(status: Status | null, task: string | null) {
  const q = new URLSearchParams();
  if (status) q.set("status", status);
  if (task) q.set("task", task);
  const qs = q.toString();
  history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
}

export default function App() {
  const [filter, setFilter] = useState<Status | null>(() => readUrl().status);
  const [selected, setSelected] = useState<string | null>(() => readUrl().task);
  const [creating, setCreating] = useState(false);

  useEffect(() => writeUrl(filter, selected), [filter, selected]);

  const stats = useQuery({ queryKey: ["stats"], queryFn: api.stats, refetchInterval: 2000 });
  const tasks = useQuery({
    queryKey: ["tasks", filter],
    queryFn: () => api.list(filter),
    refetchInterval: 2000,
    placeholderData: (prev, prevQuery) => (prevQuery?.queryKey[1] === filter ? prev : undefined),
  });

  // "N" opens the dialog, like Linear / Vercel.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLElement && e.target.closest("input, textarea, [contenteditable]");
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "n" || e.key === "N") {
        e.preventDefault();
        setCreating(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const closePanel = useCallback(() => setSelected(null), []);
  const closeDialog = useCallback(() => setCreating(false), []);

  const running = stats.data?.counts.running ?? 0;
  const queues = stats.data?.queues;

  return (
    <div className="min-h-screen">
      <Header onNewTask={() => setCreating(true)} />

      <main className="mx-auto max-w-[1400px] px-4 py-8 sm:px-6">
        <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Tasks</h1>
            <p className="mt-1 text-sm text-muted">
              Summarization jobs flowing through the API, RabbitMQ and the inference workers.
            </p>
          </div>
          <div className="flex items-center gap-4 text-[13px] text-muted">
            {stats.data && (
              <>
                <span className="tabular">
                  <span className="text-blue">{running}</span> running
                </span>
                <span className="h-3.5 w-px bg-border-strong" />
                {queues ? (
                  <>
                    <span className="tabular" title="tasks.llm_inference: messages waiting for a worker">
                      <span className={queues.main.ready ? "text-amber" : undefined}>{queues.main.ready}</span> in queue
                    </span>
                    <span className="h-3.5 w-px bg-border-strong" />
                    <span className="tabular" title="tasks.llm_inference.retry: waiting out the 30s retry delay">
                      <span className={queues.retry.ready ? "text-amber" : undefined}>{queues.retry.ready}</span> retrying
                    </span>
                    <span className="h-3.5 w-px bg-border-strong" />
                    <span
                      className={
                        queues.dlq.ready
                          ? "tabular rounded-full bg-red-soft px-2 py-0.5 font-medium text-red"
                          : "tabular"
                      }
                      title="tasks.llm_inference.dlq: messages that failed for good"
                    >
                      {queues.dlq.ready} dead-lettered
                    </span>
                    <span className="h-3.5 w-px bg-border-strong" />
                    <span className="tabular" title="consumers attached to tasks.llm_inference">
                      {queues.main.consumers} worker{queues.main.consumers === 1 ? "" : "s"}
                    </span>
                  </>
                ) : (
                  <span className="text-red">RabbitMQ unreachable</span>
                )}
                <span className="h-3.5 w-px bg-border-strong" />
              </>
            )}
            <span className="inline-flex items-center gap-1.5" title="Refreshes every 2 seconds">
              {tasks.isFetching ? (
                <Spinner className="size-3" />
              ) : (
                <span className="size-1.5 animate-pulse-dot rounded-full bg-green" />
              )}
              Live
            </span>
          </div>
        </div>

        <div className="mb-4 border-b border-border">
          <StatusTabs stats={stats.data} value={filter} onChange={setFilter} />
        </div>

        <RunsTable
          tasks={tasks.data}
          isPending={tasks.isPending}
          error={tasks.error}
          filter={filter}
          selectedId={selected}
          onSelect={setSelected}
          onRetry={() => tasks.refetch()}
          onNewTask={() => setCreating(true)}
        />
        {tasks.data && tasks.data.length >= 100 && (
          <p className="mt-3 text-center text-xs text-faint">Showing the 100 most recent tasks.</p>
        )}
      </main>

      {selected && <TaskPanel key={selected} id={selected} onClose={closePanel} />}
      {creating && (
        <NewTaskDialog
          onClose={closeDialog}
          onCreated={(t) => {
            setCreating(false);
            setSelected(t.task_id);
          }}
        />
      )}
    </div>
  );
}
