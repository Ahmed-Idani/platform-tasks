import clsx from "clsx";
import { Ban, Check, CircleCheck, CircleDashed, CircleX, Clock3, Copy, LoaderCircle } from "lucide-react";
import { type ButtonHTMLAttributes, type ReactNode, forwardRef, useState } from "react";
import type { Status } from "../lib/api";

// ---- Button ----------------------------------------------------------------------

type Variant = "primary" | "secondary" | "ghost";
type Size = "sm" | "md";

export const Button = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size; loading?: boolean }
>(function Button({ variant = "secondary", size = "md", loading, disabled, className, children, ...rest }, ref) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={clsx(
        "inline-flex select-none items-center justify-center gap-1.5 whitespace-nowrap rounded-md font-medium transition-colors",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue",
        "disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "h-7 px-2.5 text-[13px]" : "h-8 px-3 text-sm",
        variant === "primary" && "bg-inverted text-inverted-fg hover:opacity-90",
        variant === "secondary" && "border border-border bg-surface text-fg shadow-[0_1px_1px_rgba(0,0,0,0.02)] hover:bg-hover",
        variant === "ghost" && "text-muted hover:bg-hover hover:text-fg",
        className,
      )}
      {...rest}
    >
      {loading && <LoaderCircle className="size-3.5 animate-spin" />}
      {children}
    </button>
  );
});

// ---- Status ----------------------------------------------------------------------

export const statusMeta: Record<Status, { label: string; tone: string; soft: string; Icon: typeof Check }> = {
  pending: { label: "Pending", tone: "text-gray", soft: "bg-gray-soft", Icon: CircleDashed },
  queued: { label: "Queued", tone: "text-amber", soft: "bg-amber-soft", Icon: Clock3 },
  running: { label: "Running", tone: "text-blue", soft: "bg-blue-soft", Icon: LoaderCircle },
  completed: { label: "Completed", tone: "text-green", soft: "bg-green-soft", Icon: CircleCheck },
  failed: { label: "Failed", tone: "text-red", soft: "bg-red-soft", Icon: CircleX },
  cancelled: { label: "Cancelled", tone: "text-gray", soft: "bg-gray-soft", Icon: Ban },
};

export function StatusIcon({ status, className }: { status: Status; className?: string }) {
  const { tone, Icon } = statusMeta[status];
  return <Icon className={clsx("shrink-0", tone, status === "running" && "animate-spin", className ?? "size-3.5")} />;
}

export function StatusBadge({ status, pill }: { status: Status; pill?: boolean }) {
  const { label, tone, soft } = statusMeta[status];
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 text-[13px] font-medium",
        tone,
        pill && ["h-6 rounded-full px-2", soft],
      )}
    >
      <StatusIcon status={status} />
      {label}
    </span>
  );
}

// ---- Loading ---------------------------------------------------------------------

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("skeleton", className ?? "h-3.5 w-24")} />;
}

export function Spinner({ className }: { className?: string }) {
  return <LoaderCircle className={clsx("animate-spin text-faint", className ?? "size-4")} />;
}

// ---- Small things ----------------------------------------------------------------

export function CopyButton({ value, label = "Copy", className }: { value: string; label?: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      title={copied ? "Copied" : label}
      aria-label={label}
      onClick={(e) => {
        e.stopPropagation();
        navigator.clipboard.writeText(value).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1200);
        });
      }}
      className={clsx(
        "inline-flex size-6 items-center justify-center rounded text-faint transition-colors hover:bg-hover hover:text-fg",
        className,
      )}
    >
      {copied ? <Check className="size-3.5 text-green" /> : <Copy className="size-3.5" />}
    </button>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="inline-flex h-[18px] min-w-[18px] items-center justify-center rounded border border-border bg-subtle px-1 font-mono text-[11px] text-muted">
      {children}
    </kbd>
  );
}

export function Label({ children, hint, htmlFor }: { children: ReactNode; hint?: ReactNode; htmlFor?: string }) {
  return (
    <div className="mb-1.5 flex items-baseline justify-between gap-3">
      <label htmlFor={htmlFor} className="text-[13px] font-medium text-fg">
        {children}
      </label>
      {hint && <span className="text-xs text-faint">{hint}</span>}
    </div>
  );
}

export const inputClass = clsx(
  "w-full rounded-md border border-border bg-surface px-3 text-sm text-fg placeholder:text-faint",
  "transition-[border-color,box-shadow] outline-none",
  "hover:border-border-strong focus:border-fg/40 focus:ring-[3px] focus:ring-fg/8",
  "aria-invalid:border-red aria-invalid:focus:ring-red/15",
);
