import { Activity } from "lucide-react";
import Link from "next/link";

import { AutoRefresh } from "@/components/auto-refresh";
import { Card, cx, duration, Empty, money, PageHeader, StatusBadge, timeAgo } from "@/components/ui";
import { apiGet } from "@/lib/api";
import type { RunOut } from "@/lib/types";

const FILTERS = [
  { label: "All", value: "" },
  { label: "Needs review", value: "waiting_human" },
  { label: "Completed", value: "completed" },
  { label: "Needs attention", value: "needs_attention" },
  { label: "Cancelled", value: "cancelled" },
];

export default async function RunsPage(props: PageProps<"/runs">) {
  const sp = await props.searchParams;
  const status = typeof sp.status === "string" ? sp.status : "";
  const runs = await apiGet<RunOut[]>(`/runs?limit=100&hide_scheduled_ok=${status ? "false" : "true"}${status ? `&status=${encodeURIComponent(status)}` : ""}`);
  const spend = runs.reduce((a, r) => a + (r.cost_usd ?? 0), 0);

  return (
    <div className="mx-auto max-w-6xl">
      <AutoRefresh active={runs.some((r) => r.status === "running" || r.status === "pending")} />
      <PageHeader title="Runs" sub={`Every workflow execution, pinned to its definition and config version · ${money(spend)} LLM spend shown`} />
      <div className="mb-3 flex flex-wrap gap-1.5">
        {FILTERS.map((f) => (
          <Link
            key={f.value}
            href={f.value ? `/runs?status=${f.value}` : "/runs"}
            className={cx("rounded-full border px-3 py-1 text-xs font-medium", status === f.value ? "border-accent bg-accent-soft text-accent" : "border-line bg-panel text-muted hover:text-ink")}
          >
            {f.label}
          </Link>
        ))}
      </div>
      <Card className="overflow-hidden">
        {runs.length === 0 ? (
          <Empty icon={<Activity className="size-8" />} title="No runs" />
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-line bg-canvas/60 text-left text-xs text-muted">
              <tr>
                <th className="px-4 py-2 font-medium">Run</th>
                <th className="px-4 py-2 font-medium">Workflow</th>
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2 text-right font-medium">Duration</th>
                <th className="px-4 py-2 text-right font-medium">LLM cost</th>
                <th className="px-4 py-2 font-medium">Started</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {runs.map((r) => (
                <tr key={r.id} className="hover:bg-canvas/60">
                  <td className="px-4 py-2.5">
                    <Link href={`/runs/${r.id}`} className="font-mono text-xs font-medium text-accent hover:underline">
                      {r.id.slice(0, 8)}
                    </Link>
                  </td>
                  <td className="px-4 py-2.5">
                    <span className="font-medium">{r.workflow_key}</span>
                    <span className="ml-1.5 text-xs text-muted">
                      v{r.version} · cfg v{r.config_version}
                    </span>
                  </td>
                  <td className="px-4 py-2.5">
                    <StatusBadge status={r.status} />
                  </td>
                  <td className="num px-4 py-2.5 text-right text-muted">{duration(r.started_at, r.ended_at)}</td>
                  <td className="num px-4 py-2.5 text-right text-muted">{money(r.cost_usd)}</td>
                  <td className="px-4 py-2.5 text-muted">{timeAgo(r.started_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
