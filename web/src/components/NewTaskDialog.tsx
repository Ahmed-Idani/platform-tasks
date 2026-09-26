import { useMutation, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { CircleAlert, Sparkles, Upload, X } from "lucide-react";
import { type DragEvent, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { MAX_CHARS, MAX_WORDS, type Task, api } from "../lib/api";
import { num } from "../lib/format";
import { Button, Kbd, Label, inputClass } from "./ui";

const SAMPLE = `Kubernetes is an open-source system for automating the deployment, scaling and management of containerized applications. It groups containers into logical units called pods, schedules them onto a cluster of machines, and continuously reconciles the actual state of the cluster with the desired state declared by the operator.

Its control plane runs an API server, a scheduler, a set of controllers and etcd, a consistent key-value store that holds the cluster state. Worker nodes run the kubelet, which starts containers and reports their health, and a network proxy.

Workloads are described declaratively: a Deployment keeps a given number of identical pods running and replaces them during rolling updates, a Service gives a stable address to a changing set of pods, and a HorizontalPodAutoscaler adjusts the replica count from observed metrics. Event-driven autoscalers such as KEDA extend this to external signals like queue depth, and can scale a workload down to zero when there is nothing to do.`;

const WORD_PRESETS = [50, 150, 300];
const LOCAL_HOOK = "http://host.docker.internal:9000/hook";

function validUrl(s: string) {
  try {
    const u = new URL(s);
    return (u.protocol === "http:" || u.protocol === "https:") && !!u.host;
  } catch {
    return false;
  }
}

export function NewTaskDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (t: Task) => void }) {
  const [text, setText] = useState("");
  const [maxWords, setMaxWords] = useState(150);
  const [callback, setCallback] = useState("");
  const [dragging, setDragging] = useState(false);
  const [touched, setTouched] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();

  const create = useMutation({
    mutationFn: api.create,
    onSuccess: (task) => {
      qc.invalidateQueries({ queryKey: ["tasks"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      if (task.status === "pending") {
        toast.warning("Task accepted, not queued yet", {
          description: "RabbitMQ didn't confirm the publish. The sweeper will retry within a minute.",
        });
      } else {
        toast.success(task.status === "queued" ? "Task queued" : "Task picked up", {
          description:
            task.status === "queued"
              ? "Published to RabbitMQ. The next free worker will pick it up."
              : `A worker claimed it immediately (${task.worker_id}).`,
        });
      }
      onCreated(task);
    },
  });

  const chars = text.length;
  const errors = {
    text: !text.trim() ? "Text is required" : chars > MAX_CHARS ? `Too long: ${num(chars)} / ${num(MAX_CHARS)} characters` : null,
    maxWords: !Number.isInteger(maxWords) || maxWords < 1 || maxWords > MAX_WORDS ? `Between 1 and ${MAX_WORDS}` : null,
    callback: callback && !validUrl(callback) ? "Must be an absolute http(s) URL" : null,
  };
  const invalid = !!(errors.text || errors.maxWords || errors.callback);

  const submit = () => {
    setTouched(true);
    if (invalid || create.isPending) return;
    create.mutate({ text, max_words: maxWords, callback_url: callback || null });
  };

  useEffect(() => {
    textRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !create.isPending) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, create.isPending]);

  const loadFile = async (file: File | undefined) => {
    if (!file) return;
    if (!/^text\/|\.(txt|md)$/i.test(file.type || file.name)) {
      toast.error("Only plain-text files", { description: `${file.name} is not .txt or .md` });
      return;
    }
    setText(await file.text());
    toast(`Loaded ${file.name}`, { description: `${num(file.size)} bytes` });
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    loadFile(e.dataTransfer.files[0]);
  };

  const pct = Math.min(100, (chars / MAX_CHARS) * 100);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-4 sm:pt-[8vh]">
      <div className="fixed inset-0 animate-fade-in bg-black/30 dark:bg-black/60" onClick={() => !create.isPending && onClose()} />
      <form
        role="dialog"
        aria-label="New task"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
        }}
        className="relative w-full max-w-[640px] animate-pop-in rounded-xl border border-border bg-surface shadow-2xl"
      >
        <div className="flex items-start justify-between gap-4 px-6 pt-5 pb-4">
          <div>
            <h2 className="text-base font-semibold tracking-tight">New task</h2>
            <p className="mt-0.5 text-[13px] text-muted">Summarize a text with Qwen3 on a CPU worker.</p>
          </div>
          <Button type="button" variant="ghost" size="sm" onClick={onClose} disabled={create.isPending} aria-label="Close" className="-mr-2 px-1.5">
            <X className="size-4" />
          </Button>
        </div>

        <div className="space-y-5 px-6 pb-6">
          <div>
            <Label>Task type</Label>
            <div className="flex h-9 items-center justify-between rounded-md border border-border bg-subtle px-3 text-sm">
              <span className="font-mono text-[13px]">llm_inference</span>
              <span className="text-xs text-faint">queue: tasks.llm_inference</span>
            </div>
          </div>

          <div>
            <Label
              htmlFor="text"
              hint={
                <span className="flex items-center gap-3">
                  <button type="button" onClick={() => setText(SAMPLE)} className="inline-flex items-center gap-1 text-muted hover:text-fg">
                    <Sparkles className="size-3" /> Use sample
                  </button>
                  <button type="button" onClick={() => fileRef.current?.click()} className="inline-flex items-center gap-1 text-muted hover:text-fg">
                    <Upload className="size-3" /> Upload .txt
                  </button>
                </span>
              }
            >
              Text
            </Label>
            <input ref={fileRef} type="file" accept=".txt,.md,text/plain" className="hidden" onChange={(e) => loadFile(e.target.files?.[0])} />
            <div
              className="relative"
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
            >
              <textarea
                id="text"
                ref={textRef}
                value={text}
                onChange={(e) => setText(e.target.value)}
                aria-invalid={touched && !!errors.text}
                placeholder="Paste the text to summarize, or drop a .txt file here…"
                className={clsx(inputClass, "block min-h-[200px] resize-y py-2.5 leading-relaxed")}
              />
              {dragging && (
                <div className="pointer-events-none absolute inset-0 flex animate-fade-in items-center justify-center rounded-md border-2 border-dashed border-blue bg-blue-soft/90 text-sm font-medium text-blue">
                  Drop to load the file
                </div>
              )}
            </div>
            <div className="mt-2 flex items-center gap-3">
              <div className="h-1 flex-1 overflow-hidden rounded-full bg-hover">
                <div
                  className={clsx("h-full rounded-full transition-[width]", pct >= 100 ? "bg-red" : pct > 80 ? "bg-amber" : "bg-fg/60")}
                  style={{ width: `${pct}%` }}
                />
              </div>
              <span className={clsx("tabular text-xs", chars > MAX_CHARS ? "text-red" : "text-muted")}>
                {num(chars)} / {num(MAX_CHARS)}
              </span>
            </div>
            {touched && errors.text && <p className="mt-1.5 text-xs text-red">{errors.text}</p>}
          </div>

          <div className="grid gap-5 sm:grid-cols-[180px_1fr]">
            <div>
              <Label htmlFor="max_words">Max words</Label>
              <input
                id="max_words"
                type="number"
                min={1}
                max={MAX_WORDS}
                value={Number.isNaN(maxWords) ? "" : maxWords}
                onChange={(e) => setMaxWords(e.target.valueAsNumber)}
                aria-invalid={!!errors.maxWords}
                className={clsx(inputClass, "tabular h-9")}
              />
              <div className="mt-2 flex gap-1">
                {WORD_PRESETS.map((n) => (
                  <button
                    key={n}
                    type="button"
                    onClick={() => setMaxWords(n)}
                    className={clsx(
                      "tabular h-6 flex-1 rounded border text-xs transition-colors",
                      maxWords === n ? "border-fg bg-fg text-bg" : "border-border text-muted hover:border-border-strong hover:text-fg",
                    )}
                  >
                    {n}
                  </button>
                ))}
              </div>
              {errors.maxWords && <p className="mt-1.5 text-xs text-red">{errors.maxWords}</p>}
            </div>

            <div>
              <Label htmlFor="callback" hint="optional">
                Callback URL
              </Label>
              <input
                id="callback"
                type="url"
                value={callback}
                onChange={(e) => setCallback(e.target.value.trim())}
                aria-invalid={!!errors.callback}
                placeholder="https://example.com/hooks/task-finished"
                className={clsx(inputClass, "h-9 font-mono text-[13px]")}
              />
              {errors.callback ? (
                <p className="mt-1.5 text-xs text-red">{errors.callback}</p>
              ) : (
                <p className="mt-1.5 text-xs text-faint">
                  The worker POSTs the result here when the task finishes.{" "}
                  <button type="button" onClick={() => setCallback(LOCAL_HOOK)} className="text-muted underline decoration-border-strong underline-offset-2 hover:text-fg">
                    Use local receiver
                  </button>
                </p>
              )}
            </div>
          </div>

          {create.isError && (
            <div className="flex gap-2.5 rounded-md border border-red/25 bg-red-soft px-3.5 py-2.5 text-[13px] text-red">
              <CircleAlert className="mt-px size-4 shrink-0" />
              {create.error.message}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 rounded-b-xl border-t border-border bg-subtle px-6 py-3.5">
          <span className="hidden items-center gap-1 text-xs text-faint sm:flex">
            <Kbd>Ctrl</Kbd>
            <Kbd>↵</Kbd>
            to submit
          </span>
          <div className="ml-auto flex gap-2">
            <Button type="button" onClick={onClose} disabled={create.isPending}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={create.isPending} disabled={touched && invalid}>
              {create.isPending ? "Creating…" : "Create task"}
            </Button>
          </div>
        </div>
      </form>
    </div>
  );
}
