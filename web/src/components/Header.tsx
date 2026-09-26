import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { Plus } from "lucide-react";
import { api } from "../lib/api";
import { ThemeSwitcher } from "./ThemeSwitcher";
import { Button } from "./ui";

function ApiStatus() {
  const { isSuccess, isError, isPending } = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    refetchInterval: 5000,
    retry: false,
  });
  const label = isPending ? "Connecting" : isSuccess ? "API online" : "API offline";
  return (
    <div className="hidden items-center gap-2 text-xs text-muted sm:flex" title="GET /healthz every 5s">
      <span className="relative flex size-2">
        {isSuccess && <span className="absolute inline-flex size-full animate-ping rounded-full bg-green opacity-40" />}
        <span
          className={clsx(
            "relative inline-flex size-2 rounded-full",
            isPending && "bg-faint",
            isSuccess && "bg-green",
            isError && "bg-red",
          )}
        />
      </span>
      {label}
    </div>
  );
}

export function Header({ onNewTask }: { onNewTask: () => void }) {
  return (
    <header className="sticky top-0 z-20 border-b border-border bg-bg/80 backdrop-blur-md">
      <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-3 px-4 sm:px-6">
        <div className="flex items-center gap-2.5">
          <svg viewBox="0 0 32 32" className="size-6" aria-hidden>
            <rect width="32" height="32" rx="7" className="fill-inverted" />
            <path
              d="M9 11h14M9 16h9M9 21h11"
              className="stroke-inverted-fg"
              strokeWidth="2.5"
              strokeLinecap="round"
            />
          </svg>
          <span className="text-sm font-semibold tracking-tight">platform-tasks</span>
        </div>
        <span className="text-border-strong select-none">/</span>
        <nav className="flex items-center gap-1 text-sm">
          <span className="rounded-md px-2 py-1 font-medium text-fg">Tasks</span>
        </nav>

        <div className="ml-auto flex items-center gap-3">
          <ApiStatus />
          <ThemeSwitcher />
          <Button variant="primary" onClick={onNewTask} className="pr-2">
            <Plus className="size-4" />
            New task
            <span className="ml-1 hidden rounded border border-inverted-fg/20 px-1 font-mono text-[11px] leading-4 opacity-70 sm:inline">
              N
            </span>
          </Button>
        </div>
      </div>
    </header>
  );
}

