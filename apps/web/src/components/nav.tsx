"use client";

import { Activity, FileText, Inbox, LayoutDashboard, Settings, ShieldAlert, Workflow } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cx } from "./ui";

// screens land milestone by milestone; later ones render disabled with their milestone tag
const NAV = [
  { label: "Overview", href: "/", icon: LayoutDashboard, m: null },
  { label: "Inbox", href: "/inbox", icon: Inbox, m: null, badge: "tasks" as const },
  { label: "Documents", href: "/documents", icon: FileText, m: null },
  { label: "Runs", href: "/runs", icon: Activity, m: null },
  { label: "Studio", href: "/studio", icon: Workflow, m: "M3" },
  { label: "Exceptions", href: "/exceptions", icon: ShieldAlert, m: "M6" },
  { label: "Admin", href: "/admin", icon: Settings, m: "M4" },
];

export function Nav({ openTasks }: { openTasks: number }) {
  const path = usePathname();
  return (
    <nav className="mt-6 flex flex-col gap-0.5" aria-label="Main">
      {NAV.map(({ label, href, icon: Icon, m, badge }) => {
        if (m)
          return (
            <span key={href} className="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm text-muted" title={`Arrives in ${m}`}>
              <Icon className="size-4" aria-hidden />
              <span className="flex-1">{label}</span>
              <span className="rounded border border-line px-1 text-[10px] font-medium">{m}</span>
            </span>
          );
        const active = href === "/" ? path === "/" : path.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cx(
              "flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm transition-colors focus-visible:outline-2 focus-visible:outline-accent",
              active ? "bg-accent-soft font-medium text-accent" : "text-ink/80 hover:bg-canvas hover:text-ink",
            )}
          >
            <Icon className="size-4" aria-hidden />
            <span className="flex-1">{label}</span>
            {badge && openTasks > 0 && (
              <span className="num rounded-full bg-warn-soft px-1.5 text-[11px] font-semibold text-warn">{openTasks}</span>
            )}
          </Link>
        );
      })}
    </nav>
  );
}
