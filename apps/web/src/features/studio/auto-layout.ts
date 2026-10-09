import ELK from "elkjs/lib/elk.bundled.js";

import type { Graph } from "./yaml-graph";

export const NODE_W = 232;
export const NODE_H = 76;
const elk = new ELK();

/** Layered top-down layout (elkjs) for graphs without a `layout:` block, or on "Auto-layout". */
export async function autoLayout(g: Pick<Graph, "nodes" | "edges">): Promise<Record<string, { x: number; y: number }>> {
  const ids = new Set(g.nodes.map((n) => n.id));
  const res = await elk.layout({
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "DOWN",
      "elk.layered.spacing.nodeNodeBetweenLayers": "64",
      "elk.spacing.nodeNode": "48",
      "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
      "elk.edgeRouting": "ORTHOGONAL",
    },
    children: g.nodes.map((n) => ({ id: n.id, width: NODE_W, height: NODE_H })),
    edges: g.edges.filter((e) => ids.has(e.from) && ids.has(e.to)).map((e, i) => ({ id: `e${i}`, sources: [e.from], targets: [e.to] })),
  });
  return Object.fromEntries((res.children ?? []).map((c) => [c.id, { x: c.x ?? 0, y: c.y ?? 0 }]));
}
