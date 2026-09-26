import clsx from "clsx";
import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

type Theme = "system" | "light" | "dark";

function read(): Theme {
  try {
    const t = localStorage.getItem("theme");
    return t === "light" || t === "dark" ? t : "system";
  } catch {
    return "system";
  }
}

function apply(theme: Theme) {
  const dark = theme === "dark" || (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

/** Vercel-style 3-way segmented switch: system / light / dark. */
export function ThemeSwitcher() {
  const [theme, setTheme] = useState<Theme>(read);

  useEffect(() => {
    apply(theme);
    try {
      localStorage.setItem("theme", theme);
    } catch {
      /* private mode: theme just won't persist */
    }
    if (theme !== "system") return;
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => apply("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);

  const options: { value: Theme; Icon: typeof Sun; label: string }[] = [
    { value: "system", Icon: Monitor, label: "System theme" },
    { value: "light", Icon: Sun, label: "Light theme" },
    { value: "dark", Icon: Moon, label: "Dark theme" },
  ];

  return (
    <div role="radiogroup" aria-label="Theme" className="flex items-center rounded-full border border-border p-0.5">
      {options.map(({ value, Icon, label }) => (
        <button
          key={value}
          role="radio"
          aria-checked={theme === value}
          aria-label={label}
          title={label}
          onClick={() => setTheme(value)}
          className={clsx(
            "inline-flex size-6 items-center justify-center rounded-full transition-colors",
            theme === value ? "bg-hover text-fg shadow-[inset_0_0_0_1px_var(--border-strong)]" : "text-faint hover:text-fg",
          )}
        >
          <Icon className="size-3.5" />
        </button>
      ))}
    </div>
  );
}
