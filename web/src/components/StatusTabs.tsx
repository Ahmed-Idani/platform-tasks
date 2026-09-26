import clsx from "clsx";
import type { Stats, Status } from "../lib/api";
import { Skeleton } from "./ui";

const TABS: { value: Status | null; label: string }[] = [
  { value: null, label: "All" },
  { value: "running", label: "Running" },
  { value: "queued", label: "Queued" },
  { value: "pending", label: "Pending" },
  { value: "completed", label: "Completed" },
  { value: "failed", label: "Failed" },
];

export function StatusTabs({
  stats,
  value,
  onChange,
}: {
  stats: Stats | undefined;
  value: Status | null;
  onChange: (s: Status | null) => void;
}) {
  return (
    <div role="tablist" className="-mb-px flex gap-1 overflow-x-auto">
      {TABS.map((tab) => {
        const count = stats ? (tab.value ? stats.counts[tab.value] : stats.total) : null;
        const selected = value === tab.value;
        return (
          <button
            key={tab.label}
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(tab.value)}
            className={clsx(
              "group relative flex h-10 items-center gap-2 px-2.5 text-sm whitespace-nowrap transition-colors",
              selected ? "text-fg" : "text-muted hover:text-fg",
            )}
          >
            {tab.label}
            {count == null ? (
              <Skeleton className="h-4 w-5 rounded-full" />
            ) : (
              <span
                className={clsx(
                  "tabular rounded-full px-1.5 text-xs leading-5",
                  selected ? "bg-inverted text-inverted-fg" : "bg-hover text-muted",
                  tab.value === "failed" && count > 0 && !selected && "bg-red-soft text-red",
                  tab.value === "running" && count > 0 && !selected && "bg-blue-soft text-blue",
                )}
              >
                {count}
              </span>
            )}
            <span
              className={clsx(
                "absolute inset-x-2 bottom-0 h-0.5 rounded-full transition-colors",
                selected ? "bg-fg" : "bg-transparent",
              )}
            />
          </button>
        );
      })}
    </div>
  );
}
