"use client";

import { Radio, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { cx, duration, money, StatusBadge } from "@/components/ui";
import type { RunDetail, StepOut } from "@/lib/types";

import { Canvas, type NodeStatus } from "./canvas";
import { autoLayout } from "./auto-layout";
import { metaOf } from "./node-meta";
import { toGraph } from "./yaml-graph";

type Out = { meta?: { cost_usd?: number; calls?: { model?: string; alias?: string; ms?: number; cost_usd?: number; error?: string }[] }; [k: string]: unknown };
const TERMINAL = new Set(["completed", "rejected", "cancelled", "failed"]);

/** Live run view (FR-1.7): the run's pinned definition, painted with step status from the SSE stream. */
export default function RunGraph({ yaml, initial }: { yaml: string; initial: RunDetail }) {
  const router = useRouter();
  const [run, setRun] = useState(initial);
  const [live, setLive] = useState(!TERMINAL.has(initial.status));
  const [lastEventAt, setLastEventAt] = useState<number | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const graph = useMemo(() => toGraph(yaml), [yaml]);
  const [pos, setPos] = useState<Record<string, { x: number; y: number }>>({});
  const refreshed = useRef(false);

  useEffect(() => {
    if (graph.hasLayout) return;
    void autoLayout(graph).then(setPos);
  }, [graph]);

  useEffect(() => {
    if (TERMINAL.has(initial.status)) return;
    const es = new EventSource(`/api/v1/runs/${initial.id}/stream`);
    es.addEventListener("snapshot", (e) => {
      setRun(JSON.parse((e as MessageEvent<string>).data) as RunDetail);
      setLastEventAt(Date.now());
      setLive(true);
    });
    es.addEventListener("end", () => {
      es.close();
      setLive(false);
      if (!refreshed.current) {
        refreshed.current = true;
        router.refresh(); // the server-rendered timeline + header catch up
      }
    });
    es.onerror = () => setLive(es.readyState !== EventSource.CLOSED);
    return () => es.close();
  }, [initial.id, initial.status, router]);

  // last step per node wins (retries re-run a node)
  const byNode = useMemo(() => {
    const m = new Map<string, StepOut[]>();
    for (const s of run.steps) m.set(s.node_id, [...(m.get(s.node_id) ?? []), s]);
    return m;
  }, [run.steps]);
  const status = useMemo(() => {
    const out: Record<string, NodeStatus> = {};
    const waiting = new Set(run.tasks.map((t) => t.node_id));
    for (const [id, steps] of byNode) {
      const s = steps.at(-1)!.status;
      out[id] = waiting.has(id) ? "waiting" : s === "completed" ? "completed" : s === "failed" ? "failed" : s === "running" ? "running" : "skipped";
    }
    return out;
  }, [byNode, run.tasks]);
  const meta = useMemo(() => {
    const out: Record<string, string> = {};
    for (const [id, steps] of byNode) {
      const s = steps.at(-1)!;
      const cost = (s.output as Out | null)?.meta?.cost_usd;
      out[id] = [s.ended_at ? duration(s.started_at, s.ended_at) : "running…", cost ? money(cost) : null, steps.length > 1 ? `${steps.length} attempts` : null].filter(Boolean).join(" · ");
    }
    return out;
  }, [byNode]);
  const taken = useMemo(() => {
    const order = run.steps.map((s) => s.node_id);
    return new Set(order.slice(1).map((id, i) => `${order[i]}->${id}`));
  }, [run.steps]);

  const steps = selected ? (byNode.get(selected) ?? []) : [];
  const node = graph.nodes.find((n) => n.id === selected);

  return (
    <div className="flex h-full">
      <div className="relative min-w-0 flex-1">
        <div className="absolute left-3 top-3 z-10 flex items-center gap-2 rounded-lg border border-line bg-panel/95 px-2.5 py-1.5 text-xs shadow-sm backdrop-blur">
          <StatusBadge status={run.status} />
          {live ? (
            <span className="inline-flex items-center gap-1 text-info" data-testid="live">
              <Radio className="size-3.5 animate-pulse" /> Live
            </span>
          ) : (
            <span className="text-muted">Finished</span>
          )}
          {lastEventAt && <span className="num text-muted" data-last-event={lastEventAt}>updated {new Date(lastEventAt).toLocaleTimeString()}</span>}
        </div>
        <Canvas graph={graph} positions={graph.hasLayout ? undefined : pos} fitKey={`${Object.keys(pos).length}`} status={status} meta={meta} taken={taken} selected={selected} onSelect={setSelected} />
      </div>
      {selected && node && (
        <aside className="w-96 shrink-0 overflow-y-auto border-l border-line bg-panel" aria-label={`Step ${selected}`}>
          <header className="flex items-center gap-2 border-b border-line px-4 py-3">
            <span className={cx("grid size-8 place-items-center rounded-lg", metaOf(node.type).tile)}>
              {(() => {
                const I = metaOf(node.type).icon;
                return <I className="size-4" />;
              })()}
            </span>
            <div className="min-w-0 flex-1">
              <div className="font-mono text-sm font-semibold">{selected}</div>
              <div className="text-[11px] text-muted">
                {metaOf(node.type).label}
                {node.ref ? ` · ${node.ref}` : ""}
              </div>
            </div>
            <button type="button" onClick={() => setSelected(null)} className="rounded p-1 text-muted hover:bg-canvas" aria-label="Close step inspector">
              <X className="size-4" />
            </button>
          </header>
          {steps.length === 0 ? (
            <p className="p-4 text-sm text-muted">This step hasn&apos;t run{TERMINAL.has(run.status) ? " (a different branch was taken)" : " yet"}.</p>
          ) : (
            steps.map((s, k) => {
              const o = (s.output ?? {}) as Out;
              return (
                <section key={k} className="space-y-2 border-b border-line px-4 py-3">
                  <div className="flex items-center gap-2 text-xs">
                    {steps.length > 1 && <span className="font-medium">Attempt {k + 1}</span>}
                    <StatusBadge status={s.status} />
                    <span className="num ml-auto text-muted">
                      {duration(s.started_at, s.ended_at)}
                      {o.meta?.cost_usd ? ` · ${money(o.meta.cost_usd)}` : ""}
                    </span>
                  </div>
                  {(o.meta?.calls ?? []).length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {o.meta!.calls!.map((c, i) => (
                        <span key={i} className={cx("rounded border px-1.5 py-0.5 font-mono text-[10px]", c.error ? "border-bad/40 text-bad" : "border-line text-muted")}>
                          {c.model ?? c.alias}
                          {c.ms ? ` ${(c.ms / 1000).toFixed(1)}s` : ""}
                        </span>
                      ))}
                    </div>
                  )}
                  <details open={k === steps.length - 1}>
                    <summary className="cursor-pointer text-[11px] font-medium text-muted">Output</summary>
                    <pre className="mt-1 max-h-80 overflow-auto rounded-md bg-canvas p-2 font-mono text-[11px] leading-relaxed">{JSON.stringify(s.output, null, 2)}</pre>
                  </details>
                </section>
              );
            })
          )}
        </aside>
      )}
    </div>
  );
}
