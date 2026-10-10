import { Activity, ArrowRight, BellRing, BookOpen, Inbox, Radio, ShieldAlert } from "lucide-react";
import Link from "next/link";

import { Badge, Card, cx, Empty, SEVERITY_TONE, Stat, StatusBadge, timeAgo, type Tone } from "@/components/ui";
import type { ExceptionOut, LagOut, MetricOut, NotificationOut } from "@/lib/types";

export const TYPE_LABEL: Record<string, string> = { ETA_SLIP: "ETA slip", DWELL: "Long dwell", MISSED_TS: "Missed transhipment", ROLLOVER: "Rollover" };
const TYPE_TONE: Record<string, Tone> = { ETA_SLIP: "warn", DWELL: "info", MISSED_TS: "bad", ROLLOVER: "accent" };
const BAR: Record<string, string> = { ETA_SLIP: "bg-warn", DWELL: "bg-info", MISSED_TS: "bg-bad", ROLLOVER: "bg-accent" };

type Props = {
  exceptions: ExceptionOut[];
  slips: MetricOut | null;
  touchless: MetricOut | null;
  cost: MetricOut | null;
  notifications: NotificationOut[];
  lag: LagOut[] | null;
  slipThreshold: number;
  /** the caller may read analytics (can_view_analytics); otherwise the cards say so instead of failing */
  analytics: boolean;
};

/** W3 ops console (FR-3.2, docs/04): what monitoring raised, where each exception is in triage, what the
 * customers were told, and whether the pipeline keeps up. Server-rendered; the page refreshes itself. */
export function ExceptionsView({ exceptions, slips, touchless, cost, notifications, lag, slipThreshold, analytics }: Props) {
  const open = exceptions.filter((e) => e.status !== "resolved");
  const inReview = exceptions.filter((e) => e.task_id);
  const notified = notifications.filter((n) => n.type === "email.customer_delay");
  const lastTouchless = touchless?.rows.at(-1) as { touchless_rate?: number; runs?: number } | undefined;
  const costRows = (cost?.rows ?? []) as { cost_per_run?: number; runs?: number }[];
  const avgCost = costRows.length ? costRows.reduce((a, r) => a + Number(r.cost_per_run ?? 0), 0) / costRows.length : null;
  const byType = Object.entries(
    exceptions.reduce<Record<string, number>>((acc, e) => ({ ...acc, [e.type]: (acc[e.type] ?? 0) + 1 }), {}),
  ).sort((a, b) => b[1] - a[1]);
  const maxType = Math.max(1, ...byType.map(([, n]) => n));

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Open" value={open.length} tone={open.length ? "warn" : "ok"} hint="raised, not yet resolved" />
        <Stat label="Awaiting ops" value={inReview.length} tone={inReview.length ? "warn" : undefined} hint="human task open" />
        <Stat label="Resolved" value={exceptions.length - open.length} hint={`of ${exceptions.length} raised`} />
        <Stat label="Customers notified" value={notified.length} hint="mock sink" />
        <Stat
          label="Touchless rate"
          value={lastTouchless?.touchless_rate != null ? `${Math.round(Number(lastTouchless.touchless_rate) * 100)}%` : "—"}
          hint={lastTouchless?.runs ? `${lastTouchless.runs} runs today` : "all workflows"}
        />
        <Stat label="Cost per run" value={avgCost != null ? `$${avgCost.toFixed(4)}` : "—"} hint="LLM spend, all workflows" />
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.7fr)_minmax(320px,1fr)]">
        <Card className="overflow-hidden">
          <div className="flex items-center gap-2 border-b border-line px-4 py-3">
            <ShieldAlert className="size-4 text-accent" aria-hidden />
            <h2 className="text-sm font-semibold">Exceptions</h2>
            <span className="text-xs text-muted">one per shipment and type; detection runs every minute</span>
          </div>
          {exceptions.length === 0 ? (
            <Empty icon={<Radio className="size-6" />} title="Nothing detected yet">
              Start the simulator (<code className="font-mono">make sim</code>). The scripted incidents appear within about a minute of their events.
            </Empty>
          ) : (
            <div className="overflow-x-auto focus-visible:outline-2 focus-visible:outline-accent" tabIndex={0} role="region" aria-label="Exceptions">
              <table className="w-full text-sm">
                <thead className="bg-canvas text-left text-xs text-muted">
                  <tr>
                    <th className="px-4 py-2 font-medium">Type</th>
                    <th className="px-4 py-2 font-medium">Container · route</th>
                    <th className="px-4 py-2 font-medium">Severity</th>
                    <th className="px-4 py-2 font-medium">Status</th>
                    <th className="px-4 py-2 font-medium">Detected</th>
                    <th className="px-4 py-2 font-medium">
                      <span className="sr-only">Open</span>
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {exceptions.map((e) => {
                    const href = e.task_id ? `/inbox/${e.task_id}` : e.run_id ? `/runs/${e.run_id}` : null;
                    return (
                      <tr key={e.id} className="group hover:bg-canvas">
                        <td className="px-4 py-2.5">
                          <Badge tone={TYPE_TONE[e.type] ?? "neutral"}>{TYPE_LABEL[e.type] ?? e.type}</Badge>
                        </td>
                        <td className="px-4 py-2.5">
                          <div className="font-mono text-xs font-medium">{e.shipment.container_no as string}</div>
                          <div className="text-[11px] whitespace-nowrap text-muted">
                            {e.shipment.carrier as string} ·{" "}
                            <span className="font-mono">
                              {e.shipment.pol as string}
                              {e.shipment.ts_port ? ` › ${e.shipment.ts_port as string}` : ""} › {e.shipment.pod as string}
                            </span>
                          </div>
                        </td>
                        <td className="px-4 py-2.5">
                          <Badge tone={SEVERITY_TONE[e.severity] ?? "neutral"}>{e.severity}</Badge>
                        </td>
                        <td className="px-4 py-2.5">
                          {e.task_id ? <StatusBadge status="waiting_human" /> : e.status === "resolved" ? <Badge tone="ok">Resolved</Badge> : e.run_id ? <StatusBadge status="running" /> : <StatusBadge status="pending" />}
                          {e.note && (
                            <div className="mt-1 max-w-56 truncate text-[11px] text-muted" title={e.note}>
                              {e.note}
                            </div>
                          )}
                        </td>
                        <td className="px-4 py-2.5 text-xs whitespace-nowrap text-muted" title={e.detected_at}>
                          {timeAgo(e.detected_at)}
                        </td>
                        <td className="px-4 py-2.5 text-right">
                          {href && (
                            <Link
                              href={href}
                              className={cx(
                                "inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium focus-visible:outline-2 focus-visible:outline-accent",
                                e.task_id ? "bg-accent text-on-accent hover:opacity-90" : "text-accent hover:bg-accent-soft",
                              )}
                            >
                              {e.task_id ? (
                                <>
                                  <Inbox className="size-3.5" aria-hidden /> Review
                                </>
                              ) : (
                                <>
                                  Run <ArrowRight className="size-3.5" aria-hidden />
                                </>
                              )}
                            </Link>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <div className="space-y-5">
          <Card className="p-4">
            <h2 className="text-sm font-semibold">By type</h2>
            <ul className="mt-3 space-y-2">
              {byType.map(([t, n]) => (
                <li key={t} className="grid grid-cols-[8rem_1fr_2rem] items-center gap-2 text-xs">
                  <span className="truncate">{TYPE_LABEL[t] ?? t}</span>
                  <span className="h-2 rounded-full bg-canvas">
                    <span className={cx("block h-2 rounded-full", BAR[t] ?? "bg-muted")} style={{ width: `${(n / maxType) * 100}%` }} />
                  </span>
                  <span className="num text-right font-medium">{n}</span>
                </li>
              ))}
              {byType.length === 0 && <li className="text-xs text-muted">No exceptions.</li>}
            </ul>
          </Card>

          <SlipWatch slips={slips} threshold={slipThreshold} denied={!analytics} />
          <PipelineHealth lag={lag} denied={!analytics} />
        </div>
      </div>

      <Card className="overflow-hidden">
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <BellRing className="size-4 text-accent" aria-hidden />
          <h2 className="text-sm font-semibold">Notifications</h2>
          <span className="text-xs text-muted">what the outbox delivered (mock sink): customer delay notices and carrier disputes</span>
        </div>
        {notifications.length === 0 ? (
          <Empty title="Nothing sent yet">A customer notice appears here when an ops lead accepts or overrides a recommendation.</Empty>
        ) : (
          <ul className="divide-y divide-line">
            {notifications.slice(0, 12).map((n) => {
              const refs = n.refs as { sop_refs?: string[]; clause_ids?: string[] };
              return (
                <li key={n.id}>
                  <details className="group px-4 py-2.5">
                    <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 text-sm focus-visible:outline-2 focus-visible:outline-accent">
                      <Badge tone={n.type === "email.customer_delay" ? "info" : "neutral"}>{n.type === "email.customer_delay" ? "customer" : "carrier"}</Badge>
                      <span className="font-medium">{n.subject}</span>
                      <span className="text-xs text-muted">→ {n.recipient}</span>
                      {[...(refs.sop_refs ?? []), ...(refs.clause_ids ?? [])].map((r) => (
                        <Badge key={r} tone="accent">
                          <BookOpen className="size-3" aria-hidden /> {r}
                        </Badge>
                      ))}
                      <span className="ml-auto text-xs text-muted">{timeAgo(n.received_at)}</span>
                    </summary>
                    <pre className="mt-2 rounded-md bg-canvas p-3 font-sans text-xs leading-relaxed whitespace-pre-wrap">{n.body}</pre>
                  </details>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}

/** Shipments closest to (or over) the ETA rule, from the governed eta_slip_hours metric. */
function SlipWatch({ slips, threshold, denied }: { slips: MetricOut | null; threshold: number; denied: boolean }) {
  const rows = ((slips?.rows ?? []) as { container_no: string; slip_hours: number }[]).filter((r) => Math.abs(Number(r.slip_hours)) >= 1).slice(0, 8);
  const max = Math.max(threshold * 1.5, ...rows.map((r) => Number(r.slip_hours)));
  return (
    <Card className="p-4">
      <h2 className="text-sm font-semibold">ETA slip watchlist</h2>
      <p className="text-xs text-muted">
        metric <span className="font-mono">eta_slip_hours</span>, rule at {threshold} h
      </p>
      {!slips && <p className="mt-3 text-xs text-muted">{denied ? "Analytics aren’t available for your role." : "Analytics store unavailable."}</p>}
      <ul className="mt-3 space-y-1.5">
        {rows.map((r) => {
          const h = Number(r.slip_hours);
          return (
            <li key={r.container_no} className="grid grid-cols-[7.5rem_1fr_3.5rem] items-center gap-2 text-xs">
              <span className="font-mono">{r.container_no}</span>
              <span className="relative h-2 rounded-full bg-canvas">
                <span className={cx("block h-2 rounded-full", h > threshold ? "bg-bad" : "bg-warn")} style={{ width: `${Math.min(100, (Math.max(0, h) / max) * 100)}%` }} />
                <span className="absolute -top-0.5 h-3 w-px bg-ink/50" style={{ left: `${(threshold / max) * 100}%` }} aria-hidden />
              </span>
              <span className={cx("num text-right font-medium", h > threshold && "text-bad")}>
                {h > 0 ? "+" : ""}
                {h.toFixed(1)} h
              </span>
            </li>
          );
        })}
        {slips && rows.length === 0 && <li className="text-xs text-muted">No shipment has slipped yet.</li>}
      </ul>
    </Card>
  );
}

const GROUP_LABEL: Record<string, string> = { "nova-router": "Trigger router", "nova-notifier": "Notifier", "ch-shipment-events": "ClickHouse ingest" };

function PipelineHealth({ lag, denied }: { lag: LagOut[] | null; denied: boolean }) {
  const groups = Object.entries(
    (lag ?? []).reduce<Record<string, { lag: number; alerting: boolean; at: string }>>((acc, l) => {
      const g = acc[l.group_id] ?? { lag: 0, alerting: false, at: l.measured_at };
      return { ...acc, [l.group_id]: { lag: g.lag + l.lag, alerting: g.alerting || l.alerting, at: l.measured_at > g.at ? l.measured_at : g.at } };
    }, {}),
  );
  return (
    <Card className="p-4">
      <h2 className="flex items-center gap-2 text-sm font-semibold">
        <Activity className="size-4 text-accent" aria-hidden /> Pipeline
      </h2>
      <p className="text-xs text-muted">Kafka consumer lag; alerts when it stays over the limit for 60 s</p>
      {!lag && <p className="mt-3 text-xs text-muted">{denied ? "Not available for your role." : "Unavailable."}</p>}
      <ul className="mt-3 space-y-2">
        {groups.map(([g, v]) => (
          <li key={g} className="flex items-center gap-2 text-xs">
            <span className={cx("size-2 rounded-full", v.alerting ? "bg-bad" : v.lag > 0 ? "bg-warn" : "bg-ok")} aria-hidden />
            <span className="flex-1">{GROUP_LABEL[g] ?? g}</span>
            <span className={cx("num font-medium", v.alerting && "text-bad")}>{v.lag} behind</span>
            {v.alerting && <Badge tone="bad">alert</Badge>}
            <span className="w-14 text-right text-muted">{timeAgo(v.at)}</span>
          </li>
        ))}
        {lag && groups.length === 0 && <li className="text-xs text-muted">No readings yet (ingest service not running?).</li>}
      </ul>
    </Card>
  );
}
