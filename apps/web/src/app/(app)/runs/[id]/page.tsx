import { ArrowLeft, Bot, CircleDot, ExternalLink, GitBranch, Hand, Play, Square, Zap } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { AutoRefresh } from "@/components/auto-refresh";
import { Badge, Card, cx, duration, money, StatusBadge, timeAgo } from "@/components/ui";
import { apiGetOrNotFound } from "@/lib/api";
import { env } from "@/lib/env";
import type { RunDetail, StepOut } from "@/lib/types";

const ICON: Record<string, ReactNode> = {
  agent: <Bot className="size-4" />,
  decide: <Zap className="size-4" />,
  rule: <GitBranch className="size-4" />,
  human_task: <Hand className="size-4" />,
  action: <Play className="size-4" />,
  end: <Square className="size-4" />,
};

type Out = {
  meta?: { cost_usd?: number; calls?: { model?: string; alias?: string; ms?: number; cost_usd?: number; error?: string }[] };
  issues?: { code: string; severity: string }[];
  min_confidence?: number;
  mode?: string;
  why?: Record<string, string>;
  goto?: string;
  decision?: string;
  error?: string;
  [k: string]: unknown;
};

function Summary({ s }: { s: StepOut }) {
  const o = (s.output ?? {}) as Out;
  const bits: ReactNode[] = [];
  if (o.error) bits.push(<span key="e" className="text-bad">{o.error}</span>);
  if (s.node_type === "agent" && o.min_confidence !== undefined)
    bits.push(
      <span key="c">
        {o.mode} extraction · min confidence <b className="num">{Math.round(o.min_confidence * 100)}%</b>
      </span>,
    );
  if (o.issues)
    bits.push(
      o.issues.length ? (
        <span key="i" className="inline-flex flex-wrap gap-1">
          {o.issues.map((i, k) => (
            <Badge key={k} tone={i.severity === "high" ? "bad" : i.severity === "medium" ? "warn" : "info"}>
              {i.code}
            </Badge>
          ))}
        </span>
      ) : (
        <span key="i">no issues</span>
      ),
    );
  if (s.node_type === "decide" && o.why) bits.push(<span key="w">{Object.entries(o.why).map(([k, v]) => `${k}: ${String(o[k])} (${v})`).join(" · ")}</span>);
  if (s.node_type === "rule" && o.goto) bits.push(<span key="g">→ {o.goto}</span>);
  if (s.node_type === "human_task" && o.decision) bits.push(<span key="d">decided {o.decision}</span>);
  if (s.node_type === "action" && o.shipment_id) bits.push(<span key="a">shipment {String(o.bol_number ?? "")} upserted</span>);
  return <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">{bits}</div>;
}

export default async function RunPage(props: PageProps<"/runs/[id]">) {
  const { id } = await props.params;
  const run = await apiGetOrNotFound<RunDetail>(`/runs/${id}`);
  const wf = run.temporal_workflow_id ?? `run-${run.id}`;
  const jaeger = `${env.jaegerUiUrl}/search?service=nova-engine&limit=20&lookback=2d&tags=${encodeURIComponent(JSON.stringify({ temporalWorkflowID: wf }))}`;
  const live = run.status === "running" || run.status === "pending";

  return (
    <div className="mx-auto max-w-5xl">
      <AutoRefresh active={live} />
      <Link href="/runs" className="mb-3 inline-flex items-center gap-1 text-xs text-muted hover:text-ink">
        <ArrowLeft className="size-3.5" /> Runs
      </Link>
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold tracking-tight">{run.workflow_key}</h1>
        <StatusBadge status={run.status} />
        <span className="font-mono text-xs text-muted">{run.id}</span>
        <span className="ml-auto flex gap-2">
          <a href={`${env.temporalUiUrl}/namespaces/default/workflows/${wf}`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded-md border border-line bg-panel px-2.5 py-1 text-xs font-medium hover:bg-canvas">
            Temporal <ExternalLink className="size-3" />
          </a>
          <a href={jaeger} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded-md border border-line bg-panel px-2.5 py-1 text-xs font-medium hover:bg-canvas">
            Trace <ExternalLink className="size-3" />
          </a>
        </span>
      </div>

      <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-4">
        {[
          ["Definition", `v${run.version}`],
          ["Config", `v${run.config_version}`],
          ["Duration", duration(run.started_at, run.ended_at)],
          ["LLM cost", money(run.cost_usd)],
        ].map(([k, v]) => (
          <Card key={k} className="px-4 py-3">
            <div className="text-xs text-muted">{k}</div>
            <div className="num mt-0.5 font-semibold">{v}</div>
          </Card>
        ))}
      </div>

      {run.tasks.length > 0 && (
        <Card className="mb-5 flex items-center gap-3 border-warn/40 bg-warn-soft px-4 py-3">
          <Hand className="size-4 text-warn" />
          <span className="text-sm">Waiting on a human: {run.tasks[0].title}</span>
          <Link href={`/inbox/${run.tasks[0].id}`} className="ml-auto rounded-md bg-accent px-3 py-1 text-xs font-medium text-on-accent">
            Open task
          </Link>
        </Card>
      )}

      <Card className="p-4">
        <h2 className="mb-3 text-sm font-semibold">Timeline</h2>
        <ol className="relative ml-3 border-l border-line">
          {run.steps.map((s, i) => {
            const o = (s.output ?? {}) as Out;
            const calls = o.meta?.calls ?? [];
            return (
              <li key={i} className="mb-4 ml-5 last:mb-0">
                <span
                  className={cx(
                    "absolute -left-3 grid size-6 place-items-center rounded-full border bg-panel",
                    s.status === "completed" ? "border-ok/50 text-ok" : s.status === "failed" ? "border-bad/50 text-bad" : "border-warn/50 text-warn",
                  )}
                >
                  {ICON[s.node_type] ?? <CircleDot className="size-4" />}
                </span>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-sm font-semibold">{s.node_id}</span>
                  <span className="text-xs text-muted">{s.node_type.replace("_", " ")}</span>
                  <StatusBadge status={s.status} />
                  <span className="num ml-auto text-xs text-muted">
                    {duration(s.started_at, s.ended_at)}
                    {o.meta?.cost_usd ? ` · ${money(o.meta.cost_usd)}` : ""}
                  </span>
                </div>
                <Summary s={s} />
                {calls.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {calls.map((c, k) => (
                      <span key={k} className={cx("rounded border px-1.5 py-0.5 font-mono text-[10px]", c.error ? "border-bad/40 text-bad" : "border-line text-muted")}>
                        {c.model ?? c.alias}
                        {c.ms ? ` ${(c.ms / 1000).toFixed(1)}s` : ""}
                        {c.error ? " ✕" : ""}
                      </span>
                    ))}
                  </div>
                )}
              </li>
            );
          })}
        </ol>
        <p className="mt-4 text-xs text-muted">
          Started {timeAgo(run.started_at)} · input <code className="font-mono">{JSON.stringify(run.input)}</code>
        </p>
      </Card>
    </div>
  );
}
