"use client";

import "@xyflow/react/dist/style.css";

import {
  Background,
  BackgroundVariant,
  Controls,
  type Edge,
  Handle,
  MarkerType,
  MiniMap,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useNodesState,
  useReactFlow,
} from "@xyflow/react";
import { AlertTriangle, Check, Loader2, X } from "lucide-react";
import { type DragEvent, memo, useEffect, useMemo } from "react";

import { cx } from "@/components/ui";

import { NODE_H, NODE_W } from "./auto-layout";
import { metaOf } from "./node-meta";
import type { Graph, GraphEdge } from "./yaml-graph";

export type NodeStatus = "running" | "completed" | "failed" | "waiting" | "skipped";
export type CardData = {
  type: string;
  ref?: string;
  label?: string;
  issues: string[];
  status?: NodeStatus;
  meta?: string;
  entry?: boolean;
};
export type PaletteDrop = { type: string; ref?: string };
export const DND_MIME = "application/x-nova-node";

const STATUS_RING: Record<NodeStatus, string> = {
  completed: "ring-2 ring-ok/70",
  running: "ring-2 ring-info animate-pulse",
  failed: "ring-2 ring-bad",
  waiting: "ring-2 ring-warn",
  skipped: "opacity-50",
};

const NodeCard = memo(function NodeCard({ data, selected, id }: NodeProps<Node<CardData>>) {
  const m = metaOf(data.type);
  const Icon = m.icon;
  return (
    <div
      className={cx(
        "group relative flex overflow-hidden rounded-xl border bg-panel text-left shadow-sm transition-shadow",
        selected ? "border-accent shadow-md ring-2 ring-accent/30" : "border-line hover:shadow-md",
        data.issues.length > 0 && !selected && "border-bad/60",
        data.status && STATUS_RING[data.status],
      )}
      style={{ width: NODE_W, height: NODE_H }}
      title={data.issues.join("\n") || undefined}
    >
      <Handle type="target" position={Position.Top} className="!size-2.5 !border-2 !border-panel !bg-muted" />
      <span className={cx("w-1 shrink-0", m.strip)} aria-hidden />
      <div className="flex min-w-0 flex-1 items-center gap-2.5 px-3">
        <span className={cx("grid size-9 shrink-0 place-items-center rounded-lg", m.tile)}>
          <Icon className="size-4.5" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate font-mono text-[13px] font-semibold">{id}</span>
            {data.entry && <span className="rounded bg-accent-soft px-1 text-[9px] font-semibold uppercase tracking-wide text-accent">start</span>}
          </div>
          <div className="truncate text-[11px] text-muted">
            {m.label}
            {data.ref ? ` · ${data.ref}` : data.label ? ` · ${data.label}` : ""}
          </div>
          {data.meta && <div className="num truncate text-[10px] text-muted">{data.meta}</div>}
        </div>
        {data.status === "completed" && <Check className="size-4 shrink-0 text-ok" aria-label="completed" />}
        {data.status === "running" && <Loader2 className="size-4 shrink-0 animate-spin text-info" aria-label="running" />}
        {data.status === "failed" && <X className="size-4 shrink-0 text-bad" aria-label="failed" />}
        {data.issues.length > 0 && (
          <span className="flex shrink-0 items-center gap-0.5 rounded-md bg-bad-soft px-1 py-0.5 text-[10px] font-semibold text-bad" aria-label={`${data.issues.length} issues`}>
            <AlertTriangle className="size-3" />
            {data.issues.length}
          </span>
        )}
      </div>
      <Handle type="source" position={Position.Bottom} className="!size-2.5 !border-2 !border-panel !bg-accent" />
    </div>
  );
});

const nodeTypes = { card: NodeCard };

export const edgeId = (e: GraphEdge) => `${e.from}->${e.to}:${e.on ?? ""}`;

type Props = {
  graph: Graph;
  positions?: Record<string, { x: number; y: number }>;
  /** Re-fit the viewport when this changes (e.g. once async auto-layout positions arrive). */
  fitKey?: string;
  issues?: Record<string, string[]>;
  status?: Record<string, NodeStatus>;
  meta?: Record<string, string>;
  taken?: Set<string>;
  selected?: string | null;
  selectedEdge?: string | null;
  editable?: boolean;
  onSelect?: (id: string | null) => void;
  onSelectEdge?: (e: GraphEdge | null) => void;
  onMove?: (id: string, x: number, y: number) => void;
  onConnect?: (from: string, to: string) => void;
  onDropNode?: (d: PaletteDrop, pos: { x: number; y: number }) => void;
};

function Flow(p: Props) {
  const { screenToFlowPosition, fitView } = useReactFlow();
  const entry = useMemo(() => {
    const targets = new Set(p.graph.edges.map((e) => e.to));
    return p.graph.nodes.find((n) => !targets.has(n.id))?.id;
  }, [p.graph]);

  const derived = useMemo<Node<CardData>[]>(
    () =>
      p.graph.nodes.map((n) => ({
        id: n.id,
        type: "card",
        position: p.positions?.[n.id] ?? { x: n.x, y: n.y },
        selected: p.selected === n.id,
        draggable: !!p.editable,
        data: { type: n.type, ref: n.ref, label: n.label, issues: p.issues?.[n.id] ?? [], status: p.status?.[n.id], meta: p.meta?.[n.id], entry: n.id === entry },
      })),
    [p.graph, p.positions, p.selected, p.editable, p.issues, p.status, p.meta, entry],
  );
  const [nodes, setNodes, onNodesChange] = useNodesState(derived);
  useEffect(() => setNodes(derived), [derived, setNodes]);
  const fitKey = `${p.graph.nodes.length}|${p.fitKey ?? ""}`;
  useEffect(() => {
    const t = setTimeout(() => void fitView({ padding: 0.15, duration: 200 }), 50);
    return () => clearTimeout(t);
  }, [fitKey, fitView]);

  const edges = useMemo<Edge[]>(
    () =>
      p.graph.edges.map((e) => {
        const id = edgeId(e);
        const hot = p.taken?.has(`${e.from}->${e.to}`);
        const sel = p.selectedEdge === id;
        return {
          id,
          source: e.from,
          target: e.to,
          type: "smoothstep",
          label: e.on ? (e.label ? `${e.on}: ${e.label.length > 38 ? `${e.label.slice(0, 38)}…` : e.label}` : e.on) : undefined,
          labelStyle: { fontSize: 10, fill: "var(--muted)", fontFamily: "var(--font-geist-mono)" },
          labelBgStyle: { fill: "var(--panel)" },
          labelBgPadding: [4, 2] as [number, number],
          animated: !!hot,
          selected: sel,
          style: { stroke: sel ? "var(--accent)" : hot ? "var(--ok)" : "var(--muted)", strokeWidth: sel || hot ? 2 : 1.25, strokeDasharray: e.implicit ? "5 4" : undefined },
          markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: sel ? "var(--accent)" : hot ? "var(--ok)" : "var(--muted)" },
        };
      }),
    [p.graph.edges, p.taken, p.selectedEdge],
  );

  const onDrop = (ev: DragEvent) => {
    const raw = ev.dataTransfer.getData(DND_MIME);
    if (!raw || !p.onDropNode) return;
    ev.preventDefault();
    const pos = screenToFlowPosition({ x: ev.clientX, y: ev.clientY });
    p.onDropNode(JSON.parse(raw) as PaletteDrop, { x: pos.x - NODE_W / 2, y: pos.y - NODE_H / 2 });
  };

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodesChange={onNodesChange}
      onNodeDragStop={(_, n) => p.onMove?.(n.id, n.position.x, n.position.y)}
      onNodeClick={(_, n) => p.onSelect?.(n.id)}
      onEdgeClick={(_, e) => p.onSelectEdge?.(p.graph.edges.find((x) => edgeId(x) === e.id) ?? null)}
      onPaneClick={() => {
        p.onSelect?.(null);
        p.onSelectEdge?.(null);
      }}
      onConnect={(c) => c.source && c.target && c.source !== c.target && p.onConnect?.(c.source, c.target)}
      onDragOver={(ev) => {
        if (p.onDropNode) {
          ev.preventDefault();
          ev.dataTransfer.dropEffect = "copy";
        }
      }}
      onDrop={onDrop}
      nodesConnectable={!!p.editable}
      deleteKeyCode={null}
      snapToGrid
      snapGrid={[8, 8]}
      minZoom={0.2}
      maxZoom={1.75}
      colorMode="system"
      fitView
      fitViewOptions={{ padding: 0.15 }}
    >
      <Background variant={BackgroundVariant.Dots} gap={16} size={1} color="var(--line)" />
      <Controls showInteractive={false} position="bottom-left" />
      <MiniMap pannable zoomable position="bottom-right" nodeStrokeWidth={2} nodeColor={(n) => (n.data as CardData).issues.length ? "var(--bad)" : "var(--accent)"} maskColor="color-mix(in srgb, var(--canvas) 70%, transparent)" style={{ background: "var(--panel)", width: 160, height: 110 }} />
    </ReactFlow>
  );
}

/** The workflow graph: the Studio editor and the live run view share it (FR-1.1, FR-1.7). */
export function Canvas(p: Props) {
  return (
    <ReactFlowProvider>
      <Flow {...p} />
    </ReactFlowProvider>
  );
}
