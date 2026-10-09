/**
 * M2 spike for M3 (brainstorm §6.2, CLAUDE.md rule 7): YAML text is canonical, the graph is a view.
 *
 * Finding: re-serialising the `yaml` Document (even with keepSourceTokens) reformats flow maps and
 * re-wraps folded `>-` scalars, so "parse → mutate → toString" can't keep untouched lines identical.
 * So the AST is only used to *locate* things: every graph edit is a splice into the original text at
 * the node's source range. Bytes outside the edited range never change, comments included.
 *
 * All functions are text → text and keep the input's line endings.
 */
import { Document, isMap, isScalar, isSeq, parseDocument, stringify, type Pair, type YAMLMap, type YAMLSeq } from "yaml";

export type GraphNode = { id: string; type: string; x: number; y: number };
export type GraphEdge = { from: string; to: string; on?: string };

/** Run an edit on LF text and give the result back in the source's line endings. */
function edit(text: string, fn: (lf: string, doc: Document) => string): string {
  const crlf = text.includes("\r\n");
  const lf = crlf ? text.replace(/\r\n/g, "\n") : text;
  const doc = parseDocument(lf);
  if (doc.errors.length) throw new Error(`invalid YAML: ${doc.errors[0].message}`);
  const out = fn(lf, doc);
  return crlf ? out.replace(/\n/g, "\r\n") : out;
}

const lineStart = (t: string, i: number) => t.lastIndexOf("\n", i - 1) + 1;
const nextLine = (t: string, i: number) => {
  const n = t.indexOf("\n", i);
  return n === -1 ? t.length : n + 1;
};
const indentOf = (t: string, i: number) => i - lineStart(t, i);
const splice = (t: string, from: number, to: number, ins: string) => t.slice(0, from) + ins + t.slice(to);
const ensureNl = (t: string) => (t.endsWith("\n") ? t : `${t}\n`);

/** One-line YAML for a value: scalars plain, collections in flow style `{a: 1}`. */
function inline(v: unknown): string {
  const d = new Document(v);
  if (isMap(d.contents) || isSeq(d.contents)) (d.contents as YAMLMap).flow = true;
  return d.toString({ flowCollectionPadding: false, lineWidth: 0 }).trimEnd();
}

const seqOf = (doc: Document, key: string): YAMLSeq | undefined => {
  const s = doc.get(key, true);
  return isSeq(s) ? s : undefined;
};
const nodeItem = (doc: Document, id: string): YAMLMap | undefined =>
  seqOf(doc, "nodes")?.items.find((n): n is YAMLMap => isMap(n) && n.get("id") === id);
const pairOf = (m: YAMLMap, key: string): Pair | undefined => m.items.find((p) => isScalar(p.key) && p.key.value === key);

/** End of a block item: the start of the line after its last content line. */
const itemEnd = (t: string, n: { range?: [number, number, number] | null }) => nextLine(t, n.range![1] - 1);

/** Insert a line (or block) after the last item of a map/seq, matching its indent. */
function appendTo(t: string, coll: YAMLMap | YAMLSeq, block: (indent: string) => string): string {
  const last = coll.items.at(-1) as Pair | { range: [number, number, number] } | undefined;
  if (!last) throw new Error("empty collection");
  const lastNode = "value" in last ? (last.value as { range: [number, number, number] }) : last;
  const first = coll.items[0] as Pair | { range: [number, number, number] };
  // map: indent of the first key; sequence: column of the first item's "-" (its range starts after "- ")
  const indent = "key" in first
    ? indentOf(t, (first.key as { range: [number, number, number] }).range[0])
    : t.lastIndexOf("-", first.range[0]) - lineStart(t, first.range[0]);
  const at = itemEnd(t, lastNode);
  return ensureNl(t.slice(0, at)) + block(" ".repeat(indent)) + t.slice(at);
}

/** The read side: what React Flow renders. */
export function toGraph(text: string): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const js = parseDocument(text).toJS() as {
    nodes?: { id: string; type: string }[];
    edges?: GraphEdge[];
    layout?: Record<string, { x: number; y: number }>;
  };
  const layout = js.layout ?? {};
  return {
    nodes: (js.nodes ?? []).map((n, i) => ({ id: n.id, type: n.type, x: layout[n.id]?.x ?? 0, y: layout[n.id]?.y ?? i * 120 })),
    edges: js.edges ?? [],
  };
}

/** Side-panel edit of one key on one node: replaces just that value, or adds one line. */
export function setField(text: string, id: string, key: string, value: unknown): string {
  return edit(text, (t, doc) => {
    const n = nodeItem(doc, id);
    if (!n) throw new Error(`no node ${id}`);
    const p = pairOf(n, key);
    if (p && p.value && (p.value as { range?: [number, number, number] }).range) {
      const [from, to] = (p.value as { range: [number, number, number] }).range;
      return splice(t, from, to, inline(value));
    }
    return appendTo(t, n, (ind) => `${ind}${key}: ${inline(value)}\n`);
  });
}

/** Drag on the canvas → `layout.<id>: {x, y}` only (the engine ignores `layout`). */
export function moveNode(text: string, id: string, x: number, y: number): string {
  const pos = inline({ x: Math.round(x), y: Math.round(y) });
  return edit(text, (t, doc) => {
    const layout = doc.get("layout", true);
    if (!isMap(layout)) return `${ensureNl(t)}layout:\n  ${id}: ${pos}\n`;
    const p = pairOf(layout, id);
    if (p) {
      const [from, to] = (p.value as { range: [number, number, number] }).range;
      return splice(t, from, to, pos);
    }
    return appendTo(t, layout, (ind) => `${ind}${id}: ${pos}\n`);
  });
}

/** Palette drop → a node block appended after the last node. */
export function addNode(text: string, node: Record<string, unknown> & { id: string; type: string }): string {
  return edit(text, (t, doc) => {
    if (nodeItem(doc, node.id)) throw new Error(`node ${node.id} already exists`);
    const nodes = seqOf(doc, "nodes");
    if (!nodes) throw new Error("no nodes list");
    const block = stringify([node], { flowCollectionPadding: false, lineWidth: 0 });
    return appendTo(t, nodes, (ind) => block.split("\n").filter(Boolean).map((l) => ind + l).join("\n") + "\n");
  });
}

export function addEdge(text: string, e: GraphEdge): string {
  const item = inline(e.on ? { from: e.from, to: e.to, on: e.on } : { from: e.from, to: e.to });
  return edit(text, (t, doc) => {
    const edges = seqOf(doc, "edges");
    if (!edges) return `${ensureNl(t)}edges:\n  - ${item}\n`;
    return appendTo(t, edges, (ind) => `${ind}- ${item}\n`);
  });
}

/** Delete removes the node's lines, its edges' lines and its layout line. Nothing else moves. */
export function removeNode(text: string, id: string): string {
  return edit(text, (t, doc) => {
    const cuts: [number, number][] = [];
    const nodes = seqOf(doc, "nodes");
    const n = nodeItem(doc, id);
    if (!nodes || !n) throw new Error(`no node ${id}`);
    const i = nodes.items.indexOf(n);
    const next = nodes.items[i + 1] as { range: [number, number, number] } | undefined;
    cuts.push([lineStart(t, n.range![0]), next ? lineStart(t, next.range[0]) : itemEnd(t, n)]);
    for (const e of seqOf(doc, "edges")?.items ?? []) {
      if (isMap(e) && (e.get("from") === id || e.get("to") === id)) cuts.push([lineStart(t, e.range![0]), itemEnd(t, e)]);
    }
    const layout = doc.get("layout", true);
    const lp = isMap(layout) ? pairOf(layout, id) : undefined;
    if (lp) cuts.push([lineStart(t, (lp.key as { range: [number, number, number] }).range[0]), itemEnd(t, lp.value as { range: [number, number, number] })]);
    return cuts.sort((a, b) => b[0] - a[0]).reduce((acc, [from, to]) => splice(acc, from, to, ""), t);
  });
}
