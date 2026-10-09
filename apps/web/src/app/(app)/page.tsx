import { ArrowRight, FileUp, Inbox } from "lucide-react";
import Link from "next/link";

import { Card, money, PageHeader, Stat, StatusBadge, timeAgo } from "@/components/ui";
import { apiGet, getMe } from "@/lib/api";
import type { RunOut, TaskOut } from "@/lib/types";

export default async function Overview() {
  const me = await getMe();
  if (!me.tenant)
    return <PageHeader title={`Welcome, ${me.name ?? me.email}`} sub="Platform account: tenant screens need an organization login." />;
  const [runs, tasks] = await Promise.all([apiGet<RunOut[]>("/runs?limit=200"), apiGet<TaskOut[]>("/tasks")]);
  const settled = runs.filter((r) => ["completed", "waiting_human", "rejected", "needs_attention"].includes(r.status));
  // a run is touchless when it completed and no human task was ever opened for it
  const reviewed = new Set(tasks.map((t) => t.run_id));
  const touchless = runs.filter((r) => r.status === "completed" && !reviewed.has(r.id) && r.workflow_key === "bol_intake").length;
  const bol = settled.filter((r) => r.workflow_key === "bol_intake").length;
  const spend = runs.reduce((a, r) => a + (r.cost_usd ?? 0), 0);
  const priced = runs.filter((r) => r.cost_usd);

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title={`Good to see you, ${me.name?.split(" ")[0] ?? me.email}`}
        sub={`${me.tenant.name} · ${me.roles.join(", ") || "no roles"}`}
        actions={
          <div className="flex gap-2">
            <Link href="/documents" className="inline-flex items-center gap-1.5 rounded-md border border-line bg-panel px-3 py-1.5 text-sm font-medium hover:bg-canvas">
              <FileUp className="size-4" /> Upload
            </Link>
            <Link href="/inbox" className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-on-accent">
              <Inbox className="size-4" /> Open inbox
            </Link>
          </div>
        }
      />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="BoL touchless rate" value={bol ? `${Math.round((touchless / bol) * 100)}%` : "—"} hint={`${touchless} of ${bol} with no human step`} tone="ok" />
        <Stat label="Waiting on people" value={tasks.length} hint={tasks.length ? "oldest first in the inbox" : "inbox zero"} tone={tasks.length ? "warn" : undefined} />
        <Stat label="LLM spend" value={money(spend)} hint={priced.length ? `${money(spend / priced.length)} per run` : "no model calls yet"} />
        <Stat label="Runs" value={runs.length} hint={`${runs.filter((r) => r.status === "needs_attention").length} need attention`} />
      </div>

      <Card className="mt-5 overflow-hidden">
        <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
          <h2 className="text-sm font-semibold">Recent activity</h2>
          <Link href="/runs" className="inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline">
            All runs <ArrowRight className="size-3" />
          </Link>
        </div>
        <ul className="divide-y divide-line">
          {runs.slice(0, 8).map((r) => (
            <li key={r.id}>
              <Link href={`/runs/${r.id}`} className="flex items-center gap-3 px-4 py-2.5 text-sm hover:bg-canvas/60">
                <StatusBadge status={r.status} />
                <span className="font-medium">{r.workflow_key}</span>
                <span className="font-mono text-xs text-muted">{r.id.slice(0, 8)}</span>
                <span className="num ml-auto text-xs text-muted">{money(r.cost_usd)}</span>
                <span className="w-20 text-right text-xs text-muted">{timeAgo(r.started_at)}</span>
              </Link>
            </li>
          ))}
          {runs.length === 0 && <li className="px-4 py-6 text-sm text-muted">Nothing has run yet. Upload a BoL to start.</li>}
        </ul>
      </Card>
    </div>
  );
}
