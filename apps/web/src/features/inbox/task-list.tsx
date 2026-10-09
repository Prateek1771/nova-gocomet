"use client";

import { AlarmClock, ChevronRight } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Badge, cx, Kbd, SEVERITY_TONE, StatusBadge } from "@/components/ui";
import type { Issue, TaskOut } from "@/lib/types";

function sla(due: string | null | undefined, now: number): { label: string; tone: "ok" | "warn" | "bad" } | null {
  if (!due) return null;
  const s = Math.round((new Date(due).getTime() - now) / 1000);
  if (s <= 0) return { label: `overdue ${fmt(-s)}`, tone: "bad" };
  return { label: `${fmt(s)} left`, tone: s < 1800 ? "warn" : "ok" };
}

function fmt(s: number) {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${m}m` : m ? `${m}m` : `${s}s`;
}

/** Keyboard-first: j/k move, Enter opens. SLA countdowns tick client-side. */
export function TaskList({ tasks }: { tasks: TaskOut[] }) {
  const router = useRouter();
  const [now, setNow] = useState(() => Date.now());
  const [sel, setSel] = useState(0);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  // j/k move real focus onto the row's link, so Enter opens it natively (and screen readers follow)
  const rows = useRef<(HTMLAnchorElement | null)[]>([]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      const step = e.key === "j" ? 1 : e.key === "k" ? -1 : 0;
      if (step) {
        const next = Math.min(Math.max(sel + step, 0), tasks.length - 1);
        setSel(next);
        rows.current[next]?.focus();
      }
      // Enter with nothing focused (fresh page) opens the highlighted task
      if (e.key === "Enter" && document.activeElement === document.body && tasks[sel]) router.push(`/inbox/${tasks[sel].id}`);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [tasks, sel, router]);

  return (
    <div>
      <ul className="divide-y divide-line" role="list">
        {tasks.map((t, i) => {
          const issues = ((t.payload as { issues?: Issue[] }).issues ?? []) as Issue[];
          const conf = (t.payload as { confidence?: Record<string, number> }).confidence;
          const minConf = conf ? Math.min(...Object.values(conf)) : null;
          const due = sla(t.due_at, now);
          return (
            <li key={t.id}>
              <Link
                ref={(el) => {
                  rows.current[i] = el;
                }}
                href={`/inbox/${t.id}`}
                onMouseEnter={() => setSel(i)}
                onFocus={() => setSel(i)}
                className={cx(
                  "flex items-center gap-4 px-4 py-3 transition-colors focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent",
                  i === sel ? "bg-accent-soft/60" : "hover:bg-canvas/60",
                )}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium">{t.title}</span>
                    <StatusBadge status={t.status} />
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    {issues.map((iss, k) => (
                      <Badge key={k} tone={SEVERITY_TONE[iss.severity] ?? "neutral"}>
                        {iss.code}
                      </Badge>
                    ))}
                    {issues.length === 0 && minConf !== null && minConf < 0.85 && <Badge tone="warn">LOW CONFIDENCE {Math.round(minConf * 100)}%</Badge>}
                    <span className="text-xs text-muted">
                      {t.app_key.replaceAll("_", " ")} · {t.assignee_role ?? "anyone"}
                    </span>
                  </div>
                </div>
                {due && (
                  <span className={cx("num flex shrink-0 items-center gap-1 text-xs font-medium", due.tone === "bad" ? "text-bad" : due.tone === "warn" ? "text-warn" : "text-muted")}>
                    <AlarmClock className="size-3.5" aria-hidden />
                    {due.label}
                  </span>
                )}
                <ChevronRight className="size-4 shrink-0 text-muted" aria-hidden />
              </Link>
            </li>
          );
        })}
      </ul>
      <div className="flex items-center gap-3 border-t border-line px-4 py-2 text-xs text-muted">
        <span>
          <Kbd>j</Kbd> <Kbd>k</Kbd> move
        </span>
        <span>
          <Kbd>Enter</Kbd> open
        </span>
      </div>
    </div>
  );
}
