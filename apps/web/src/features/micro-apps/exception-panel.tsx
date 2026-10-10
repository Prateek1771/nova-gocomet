"use client";

import { Anchor, ChevronDown, Clock, Database, Ship, Sparkles, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";

import { Badge, cx, SEVERITY_TONE } from "@/components/ui";
import { api } from "@/lib/client";
import type { MetricOut } from "@/lib/types";

type Shipment = {
  shipment_id: string;
  container_no: string;
  bol_number?: string | null;
  pol?: string | null;
  ts_port?: string | null;
  pod?: string | null;
  lane?: string | null;
  vessel?: string | null;
  voyage?: string | null;
  carrier?: string | null;
  eta_planned?: string | null;
};
type Fact = { metric: string; sql: string; rows: Record<string, unknown>[] };
type Milestone = { event_time: string; event_type: string; location: string; port_role: string; vessel: string | null; booked_vessel: string | null; eta: string | null };

const TYPE_LABEL: Record<string, string> = { ETA_SLIP: "ETA slip", DWELL: "Long dwell", MISSED_TS: "Missed transhipment", ROLLOVER: "Rollover" };
const EVENT_LABEL: Record<string, string> = { gate_in: "Gate in", loaded: "Loaded", departed: "Departed", arrived: "Arrived", discharged: "Discharged", eta_update: "ETA update" };
const fmt = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) : "—";
const hours = (a: string, b: string) => (new Date(b).getTime() - new Date(a).getTime()) / 3_600_000;

/** W3 (FR-3.2): what happened to this container, where on its route, and the governed-metric facts (the
 * exact SQL and rows the analyst read) behind the exception. Times are sim times (1 sim-day = 1 minute). */
export function ExceptionPanel({
  type,
  severity,
  summary,
  shipment,
  facts = [],
  slip_hours,
  customer_impacting,
  why,
  rule,
}: {
  type: string;
  severity: string;
  summary?: string;
  shipment: Shipment;
  facts?: Fact[];
  slip_hours?: number | null;
  customer_impacting?: boolean;
  why?: Record<string, string>;
  /** the TenantConfig rule that raised it: {type, metric, op, threshold} */
  rule?: { type?: string; threshold?: number };
}) {
  const [events, setEvents] = useState<Milestone[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!shipment?.shipment_id) return;
    api<MetricOut>(`/analytics/metrics/shipment_timeline?shipment_id=${shipment.shipment_id}&limit=100`)
      .then((m) => setEvents(m.rows as unknown as Milestone[]))
      .catch(() => setFailed(true));
  }, [shipment?.shipment_id]);

  const where = (facts[0]?.rows[0]?.location as string | undefined) ?? (type === "ETA_SLIP" || type === "ROLLOVER" ? undefined : shipment.ts_port ?? undefined);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto">
      <header className="border-b border-line p-4">
        <div className="flex flex-wrap items-center gap-2">
          <TriangleAlert className={cx("size-4", severity === "high" || severity === "critical" ? "text-bad" : "text-warn")} aria-hidden />
          <h2 className="text-base font-semibold">{TYPE_LABEL[type] ?? type}</h2>
          <Badge tone={SEVERITY_TONE[severity] ?? (severity === "critical" ? "bad" : "neutral")}>{severity}</Badge>
          {customer_impacting && <Badge tone="info">customer-impacting</Badge>}
          <span className="ml-auto font-mono text-xs text-muted">
            {shipment.container_no} · {shipment.bol_number}
          </span>
        </div>
        {summary && <p className="mt-2 text-sm">{summary}</p>}
        {why && Object.keys(why).length > 0 && (
          <p className="mt-2 flex items-start gap-1.5 text-xs text-muted">
            <Sparkles className="mt-0.5 size-3.5 shrink-0 text-accent" aria-hidden />
            <span>
              <span className="font-medium text-ink">Decide:</span> {Object.values(why).join(" · ")}
            </span>
          </p>
        )}
      </header>

      <RouteStrip shipment={shipment} events={events ?? []} incidentAt={where} type={type} />

      {typeof slip_hours === "number" && <SlipBar hours={slip_hours} threshold={rule?.type === "ETA_SLIP" ? rule.threshold : undefined} />}

      <section aria-labelledby="tl-h" className="border-b border-line p-4">
        <h3 id="tl-h" className="flex items-center gap-2 text-sm font-semibold">
          <Clock className="size-4 text-accent" aria-hidden /> Milestones
          <span className="text-xs font-normal text-muted">sim time, UTC</span>
        </h3>
        {failed && <p className="mt-2 text-xs text-bad">Timeline unavailable (analytics store down).</p>}
        {!events && !failed && <p className="mt-2 text-xs text-muted">Loading…</p>}
        {events && <Timeline events={events} incidentAt={where} />}
      </section>

      <section aria-labelledby="facts-h" className="p-4">
        <h3 id="facts-h" className="flex items-center gap-2 text-sm font-semibold">
          <Database className="size-4 text-accent" aria-hidden /> Evidence
          <span className="text-xs font-normal text-muted">governed metrics, read-only, this tenant only</span>
        </h3>
        <div className="mt-3 space-y-3">
          {facts.map((f, i) => (
            <FactCard key={i} fact={f} />
          ))}
        </div>
      </section>
    </div>
  );
}

/** POL → (TS) → POD with the legs' vessels; the container's last known position and the incident port. */
function RouteStrip({ shipment, events, incidentAt, type }: { shipment: Shipment; events: Milestone[]; incidentAt?: string; type: string }) {
  const stops = [
    { code: shipment.pol, role: "pol", label: "Origin" },
    ...(shipment.ts_port ? [{ code: shipment.ts_port, role: "ts", label: "Transhipment" }] : []),
    { code: shipment.pod, role: "pod", label: "Destination" },
  ];
  const last = events.at(-1);
  // a vessel departing is the container moving only if it was loaded there (a missed connection sails without it)
  const aboard = (i: number) => {
    const at = events.slice(0, i).filter((e) => e.location === events[i].location);
    return at.findLastIndex((e) => e.event_type === "loaded") > at.findLastIndex((e) => e.event_type === "discharged");
  };
  const lastIdx = events.findLastIndex((e, i) => e.event_type !== "eta_update" && (e.event_type !== "departed" || aboard(i)));
  const lastMove = lastIdx >= 0 ? events[lastIdx] : undefined;
  const atIdx = lastMove ? stops.findIndex((s) => s.role === lastMove.port_role) : 0;
  const sailing = lastMove?.event_type === "departed";
  const legVessel = (i: number) => events.find((e) => e.event_type === "departed" && e.port_role === stops[i]?.role)?.vessel ?? (i === 0 ? shipment.vessel : null);
  const W = 560;
  const x = (i: number) => 40 + (i * (W - 80)) / Math.max(1, stops.length - 1);
  const pos = sailing ? (x(atIdx) + x(Math.min(atIdx + 1, stops.length - 1))) / 2 : x(Math.max(0, atIdx));
  const hit = (code?: string | null, role?: string) =>
    (incidentAt && code === incidentAt) || (!incidentAt && role === "pod" && (type === "ETA_SLIP" || type === "ROLLOVER"));

  return (
    <section aria-label="Route" className="border-b border-line px-4 pt-3 pb-2">
      <svg viewBox={`0 0 ${W} 92`} className="w-full" role="img" aria-label={`Route ${stops.map((s) => s.code).join(" to ")}`}>
        {stops.slice(0, -1).map((s, i) => (
          <g key={`leg-${i}`}>
            <line x1={x(i)} y1={40} x2={x(i + 1)} y2={40} className={cx("stroke-current", i < atIdx || (i === atIdx && sailing) ? "text-accent" : "text-line")} strokeWidth={3} strokeDasharray={i >= atIdx && !(i === atIdx && sailing) ? "6 5" : undefined} />
            {legVessel(i) && (
              <text x={(x(i) + x(i + 1)) / 2} y={30} textAnchor="middle" className="fill-current text-muted" fontSize={10}>
                {legVessel(i)}
              </text>
            )}
          </g>
        ))}
        {stops.map((s, i) => (
          <g key={s.role}>
            <circle cx={x(i)} cy={40} r={hit(s.code, s.role) ? 9 : 7} className={cx("stroke-current", hit(s.code, s.role) ? "fill-bad-soft text-bad" : i <= atIdx ? "fill-accent-soft text-accent" : "fill-panel text-line")} strokeWidth={2} />
            <text x={x(i)} y={66} textAnchor="middle" className="fill-current font-mono text-ink" fontSize={11} fontWeight={600}>
              {s.code}
            </text>
            <text x={x(i)} y={80} textAnchor="middle" className="fill-current text-muted" fontSize={9}>
              {s.label}
            </text>
          </g>
        ))}
        {last && (
          <g transform={`translate(${pos - 9}, ${sailing ? 31 : 6})`} className="text-accent">
            <rect width={18} height={sailing ? 18 : 14} rx={3} className="fill-current" opacity={sailing ? 1 : 0.9} />
            <rect x={3} y={sailing ? 5 : 3} width={12} height={sailing ? 8 : 8} rx={1} className="fill-panel" opacity={0.35} />
          </g>
        )}
      </svg>
      <p className="flex flex-wrap items-center gap-x-3 text-xs text-muted">
        <span className="inline-flex items-center gap-1">
          <Ship className="size-3.5" aria-hidden /> {shipment.carrier} · {shipment.lane}
        </span>
        {lastMove && (
          <span className="inline-flex items-center gap-1">
            <Anchor className="size-3.5" aria-hidden /> last: {EVENT_LABEL[lastMove.event_type]} {lastMove.location} · {fmt(lastMove.event_time)}
          </span>
        )}
      </p>
    </section>
  );
}

function SlipBar({ hours: h, threshold }: { hours: number; threshold?: number }) {
  const limit = threshold ?? 24;
  const max = Math.max(72, h * 1.2, limit * 1.5);
  const tone = h > limit ? "bg-bad" : h > limit / 2 ? "bg-warn" : "bg-ok";
  return (
    <section aria-label="ETA slip" className="border-b border-line px-4 py-3">
      <div className="flex items-baseline justify-between text-xs">
        <span className="font-medium">ETA slip</span>
        <span className="num font-semibold">{h > 0 ? "+" : ""}{h.toFixed(1)} h</span>
      </div>
      <div className="relative mt-1.5 h-2 rounded-full bg-canvas" role="meter" aria-valuemin={0} aria-valuemax={max} aria-valuenow={h} aria-label="ETA slip hours">
        <div className={cx("h-2 rounded-full", tone)} style={{ width: `${Math.min(100, (Math.max(0, h) / max) * 100)}%` }} />
        {threshold != null && <div className="absolute -top-1 h-4 w-0.5 bg-ink/60" style={{ left: `${(threshold / max) * 100}%` }} title={`threshold ${threshold} h`} />}
      </div>
      {threshold != null && <p className="mt-1 text-[11px] text-muted">tenant threshold {threshold} h</p>}
    </section>
  );
}

function Timeline({ events, incidentAt }: { events: Milestone[]; incidentAt?: string }) {
  if (!events.length) return <p className="mt-2 text-xs text-muted">No events yet.</p>;
  const firstEta = events.find((e) => e.eta)?.eta ?? null;
  return (
    <ol className="mt-3 space-y-0">
      {events.map((e, i) => {
        const prevEta = events.slice(0, i).reverse().find((p) => p.eta)?.eta ?? null;
        const delta = e.event_type === "eta_update" && e.eta && prevEta ? hours(prevEta, e.eta) : 0;
        const total = e.eta && firstEta ? hours(firstEta, e.eta) : 0;
        const rolled = i > 0 && e.booked_vessel && events[i - 1].booked_vessel && e.booked_vessel !== events[i - 1].booked_vessel;
        const hot = Math.abs(delta) >= 12 || rolled || (incidentAt === e.location && e.port_role === "ts");
        return (
          <li key={i} className="relative flex gap-3 pb-2.5 pl-4">
            <span className={cx("absolute top-1.5 left-0 size-2 rounded-full", hot ? "bg-bad" : e.event_type === "eta_update" ? "bg-line" : "bg-accent")} aria-hidden />
            {i < events.length - 1 && <span className="absolute top-3.5 bottom-0 left-[3.5px] w-px bg-line" aria-hidden />}
            <span className="num w-24 shrink-0 text-xs text-muted">{fmt(e.event_time)}</span>
            <span className="min-w-0 flex-1 text-xs">
              <span className={cx("font-medium", hot && "text-bad")}>{EVENT_LABEL[e.event_type] ?? e.event_type}</span>{" "}
              <span className="font-mono text-muted">{e.location}</span>
              {e.vessel && e.event_type !== "eta_update" && <span className="text-muted"> · {e.vessel}</span>}
              {e.event_type === "eta_update" && (
                <span className="text-muted">
                  {" "}
                  → {fmt(e.eta)}
                  {delta !== 0 && <span className={cx("num ml-1", Math.abs(delta) >= 12 ? "text-bad" : "")}>({delta > 0 ? "+" : ""}{delta.toFixed(1)} h, total {total > 0 ? "+" : ""}{total.toFixed(1)} h)</span>}
                </span>
              )}
              {rolled && <span className="text-bad"> · booking moved to {e.booked_vessel}</span>}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function FactCard({ fact }: { fact: Fact }) {
  const [open, setOpen] = useState(false);
  const cols = Object.keys(fact.rows[0] ?? {});
  return (
    <div className="overflow-hidden rounded-lg border border-line">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex w-full items-center gap-2 bg-canvas px-3 py-2 text-left text-xs hover:bg-canvas/70 focus-visible:outline-2 focus-visible:outline-accent">
        <span className="font-mono font-semibold">{fact.metric}</span>
        <span className="text-muted">{fact.rows.length} row{fact.rows.length === 1 ? "" : "s"}</span>
        <span className="ml-auto inline-flex items-center gap-1 text-muted">
          SQL <ChevronDown className={cx("size-3.5 transition-transform", open && "rotate-180")} aria-hidden />
        </span>
      </button>
      {open && <pre className="overflow-x-auto border-t border-line bg-panel px-3 py-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-muted">{fact.sql}</pre>}
      {cols.length > 0 && (
        // focusable so a keyboard user can scroll a wide result (axe scrollable-region-focusable)
        <div className="overflow-x-auto focus-visible:outline-2 focus-visible:outline-accent" tabIndex={0} role="region" aria-label={`${fact.metric} rows`}>
          <table className="w-full text-xs">
            <thead className="text-left text-muted">
              <tr>
                {cols.filter((c) => c !== "shipment_id").map((c) => (
                  <th key={c} className="px-3 py-1.5 font-medium whitespace-nowrap">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-line border-t border-line">
              {fact.rows.map((r, i) => (
                <tr key={i}>
                  {cols.filter((c) => c !== "shipment_id").map((c) => (
                    <td key={c} className="num px-3 py-1.5 whitespace-nowrap">
                      {String(r[c] ?? "—")}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
