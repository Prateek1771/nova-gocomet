/** Small shared primitives on the CSS tokens (globals.css). Server-safe: no hooks. */
import type { ReactNode } from "react";

export function cx(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

const TONES = {
  neutral: "bg-canvas text-muted border-line",
  accent: "bg-accent-soft text-accent border-transparent",
  ok: "bg-ok-soft text-ok border-transparent",
  warn: "bg-warn-soft text-warn border-transparent",
  bad: "bg-bad-soft text-bad border-transparent",
  info: "bg-info-soft text-info border-transparent",
} as const;
export type Tone = keyof typeof TONES;

export function Badge({ tone = "neutral", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span className={cx("inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium leading-none whitespace-nowrap", TONES[tone], className)}>
      {children}
    </span>
  );
}

const STATUS: Record<string, [Tone, string]> = {
  completed: ["ok", "Completed"],
  waiting_human: ["warn", "Needs review"],
  needs_attention: ["bad", "Needs attention"],
  running: ["info", "Running"],
  pending: ["info", "Queued"],
  failed: ["bad", "Failed"],
  rejected: ["neutral", "Rejected"],
  cancelled: ["neutral", "Cancelled"],
  open: ["warn", "Open"],
  claimed: ["accent", "Claimed"],
  escalated: ["bad", "Escalated"],
  done: ["ok", "Done"],
  waiting: ["warn", "Waiting"],
  skipped: ["neutral", "Skipped"],
};

export function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <Badge>—</Badge>;
  const [tone, label] = STATUS[status] ?? (["neutral", status] as [Tone, string]);
  const live = status === "running" || status === "pending";
  return (
    <Badge tone={tone}>
      <span className={cx("size-1.5 rounded-full bg-current", live && "animate-pulse")} />
      {label}
    </Badge>
  );
}

export const SEVERITY_TONE: Record<string, Tone> = { high: "bad", medium: "warn", low: "info" };

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx("rounded-xl border border-line bg-panel", className)}>{children}</div>;
}

export function PageHeader({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {sub && <p className="mt-0.5 text-sm text-muted">{sub}</p>}
      </div>
      {actions}
    </div>
  );
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="grid place-items-center gap-2 px-6 py-14 text-center">
      {icon && <div className="text-muted">{icon}</div>}
      <p className="text-sm font-medium">{title}</p>
      {children && <div className="max-w-sm text-sm text-muted">{children}</div>}
    </div>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded border border-line bg-canvas px-1 font-mono text-[10px] text-muted">{children}</kbd>;
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: Tone }) {
  return (
    <Card className="p-4">
      <div className="text-xs font-medium text-muted">{label}</div>
      <div className={cx("num mt-1 text-2xl font-semibold tracking-tight", tone === "ok" && "text-ok", tone === "warn" && "text-warn", tone === "bad" && "text-bad")}>{value}</div>
      {hint && <div className="mt-0.5 text-xs text-muted">{hint}</div>}
    </Card>
  );
}

export function timeAgo(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const s = Math.round((now - new Date(iso).getTime()) / 1000);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export function money(usd: number | null | undefined): string {
  if (!usd) return "$0";
  return usd < 0.01 ? `$${usd.toFixed(5)}` : `$${usd.toFixed(3)}`;
}

export function duration(from: string | null | undefined, to: string | null | undefined): string {
  if (!from || !to) return "—";
  const s = (new Date(to).getTime() - new Date(from).getTime()) / 1000;
  return s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}
