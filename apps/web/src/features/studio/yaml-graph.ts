/**
 * YAML text is canonical, the graph is a view (brainstorm §6.2, CLAUDE.md rule 7, ADR-023).
 *
 * Re-serialising the `yaml` Document (even with keepSourceTokens) reformats flow maps and re-wraps
 * folded `>-` scalars, so "parse → mutate → toString" can't keep untouched lines identical. So the AST
 * is only used to *locate* things: every graph edit is a splice into the original text at the node's
 * source range. Bytes outside the edited range never change, comments included.
 *
 * All edit functions are text → text and keep the input's line endings.
 */
import { Document, isMap, isScalar, isSeq, parseDocument, stringify, type Pair, type YAMLMap, type YAMLSeq } from "yaml";

/** `placed`: the node has a `layout:` entry (otherwise x/y are a fallback and the canvas auto-lays it out). */
export type GraphNode = { id: string; type: string; x: number; y: number; placed: boolean; ref?: string; label?: string };
/** `implicit` edges come from rule `cases[].goto` / `default`, not the `edges:` list. */
export type GraphEdge = { from: string; to: string; on?: string; label?: string; implicit?: boolean };
export type Graph = { nodes: GraphNode[]; edges: GraphEdge[]; hasLayout: boolean };
export type YamlError = { message: string; line: number; col: number };
type Path = (string | number)[];
type Ranged = { range: [number, number, number] };

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
const cutAll = (t: string, cuts: [number, number][]) => cuts.sort((a, b) => b[0] - a[0]).reduce((acc, [from, to]) => splice(acc, from, to, ""), t);

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

/** Insert a line (or block) after the last item of a block map/seq, matching its indent. */
function appendTo(t: string, coll: YAMLMap | YAMLSeq, block: (indent: string) => string): string {
  const last = coll.items.at(-1) as Pair | Ranged | undefined;
  if (!last) throw new Error("empty collection");
  const lastNode = "value" in last ? (last.value as Ranged) : last;
  const first = coll.items[0] as Pair | Ranged;
  // map: indent of the first key; sequence: column of the first item's "-" (its range starts after "- ")
  const indent = "key" in first ? indentOf(t, (first.key as Ranged).range[0]) : t.lastIndexOf("-", first.range[0]) - lineStart(t, first.range[0]);
  const at = itemEnd(t, lastNode);
  return ensureNl(t.slice(0, at)) + block(" ".repeat(indent)) + t.slice(at);
}

/** Line/column errors for the YAML pane's markers (1-based). */
export function yamlErrors(text: string): YamlError[] {
  return parseDocument(text).errors.map((e) => ({ message: e.message.split("\n")[0], line: e.linePos?.[0].line ?? 1, col: e.linePos?.[0].col ?? 1 }));
}

type NodeJs = { id: string; type: string; agent?: string; action?: string; app?: string; title?: string; cases?: { when?: string; goto?: string }[]; default?: string };

/** The read side: what React Flow renders. Throws on YAML that doesn't parse (the caller keeps the last graph). */
export function toGraph(text: string): Graph {
  const doc = parseDocument(text);
  if (doc.errors.length) throw new Error(`invalid YAML: ${doc.errors[0].message}`);
  const js = (doc.toJS() ?? {}) as { nodes?: NodeJs[]; edges?: GraphEdge[]; layout?: Record<string, { x: number; y: number }> | null };
  const layout = js.layout ?? {};
  const nodes = (Array.isArray(js.nodes) ? js.nodes : []).filter((n) => n && typeof n.id === "string");
  const edges: GraphEdge[] = (Array.isArray(js.edges) ? js.edges : []).filter((e) => e && e.from && e.to);
  for (const n of nodes) {
    if (n.type !== "rule") continue;
    (n.cases ?? []).forEach((c, i) => c?.goto && edges.push({ from: n.id, to: c.goto, on: `case ${i + 1}`, label: c.when, implicit: true }));
    if (n.default) edges.push({ from: n.id, to: n.default, on: "default", implicit: true });
  }
  return {
    nodes: nodes.map((n, i) => ({
      id: n.id,
      type: n.type,
      x: layout[n.id]?.x ?? 0,
      y: layout[n.id]?.y ?? i * 120,
      placed: !!layout[n.id],
      ref: n.agent ?? n.action ?? n.app,
      label: n.title,
    })),
    edges,
    hasLayout: nodes.length > 0 && nodes.every((n) => layout[n.id]),
  };
}

/** Edit one value anywhere inside one node, e.g. `["with", "schema"]` or `["questions", 0, "threshold"]`.
 * `undefined` deletes the key. Only the value's own bytes change; a missing key adds one line. Inside a
 * flow collection (`{a: 1}`) the whole flow collection is re-inlined, because plain scalars quote
 * differently there. */
export function setPath(text: string, id: string, path: Path, value: unknown): string {
  return edit(text, (t, doc) => {
    const n = nodeItem(doc, id);
    if (!n) throw new Error(`no node ${id}`);
    let coll: YAMLMap | YAMLSeq = n;
    let i = 0;
    for (; i < path.length - 1; i++) {
      const child = coll.get(path[i], true);
      if (!isMap(child) && !isSeq(child)) break;
      coll = child;
    }
    // missing (or scalar) intermediate: set the rest as one nested value at the deepest collection found
    const key = path[i];
    const rest = path.slice(i + 1).reduceRight<unknown>((v, k) => (typeof k === "number" ? Object.assign([], { [k]: v }) : { [k]: v }), value);
    if (coll.flow) {
      const js = coll.toJSON() as Record<string | number, unknown>;
      if (rest === undefined) delete js[key];
      else js[key] = rest;
      const [from, to] = (coll as unknown as Ranged).range;
      return splice(t, from, to, inline(js));
    }
    if (isSeq(coll)) {
      const item = coll.items[key as number] as Ranged | undefined;
      if (!item || rest === undefined) throw new Error(`can't set item ${String(key)}`);
      return splice(t, item.range[0], item.range[1], inline(rest));
    }
    const p = pairOf(coll, String(key));
    if (rest === undefined) {
      if (!p) return t;
      return splice(t, lineStart(t, (p.key as Ranged).range[0]), itemEnd(t, (p.value as Ranged | null) ?? (p.key as Ranged)), "");
    }
    if (p?.value && (p.value as Partial<Ranged>).range) {
      const [from, to] = (p.value as Ranged).range;
      return splice(t, from, to, inline(rest));
    }
    if (p) {
      // `key:` with no value: put the value after the colon
      const k = (p.key as Ranged).range[1];
      const eol = t.indexOf("\n", k);
      return splice(t, k, eol === -1 ? t.length : eol, `: ${inline(rest)}`);
    }
    return appendTo(t, coll, (ind) => `${ind}${String(key)}: ${inline(rest)}\n`);
  });
}

/** Side-panel edit of one top-level key on one node. */
export const setField = (text: string, id: string, key: string, value: unknown) => setPath(text, id, [key], value);

/** Drag on the canvas → `layout.<id>: {x, y}` only (the engine ignores `layout`). */
export function moveNode(text: string, id: string, x: number, y: number): string {
  return moveNodes(text, { [id]: { x, y } });
}

/** Several positions in one edit (auto-layout). Existing entries change in place; new ones are appended. */
export function moveNodes(text: string, pos: Record<string, { x: number; y: number }>): string {
  return edit(text, (t, doc) => {
    const ids = Object.keys(pos);
    const line = (id: string) => `${id}: ${inline({ x: Math.round(pos[id].x), y: Math.round(pos[id].y) })}`;
    const layout = doc.get("layout", true);
    if (!isMap(layout) || !layout.items.length) {
      const block = ids.map((id) => `  ${line(id)}\n`).join("");
      const lp = doc.contents && isMap(doc.contents) ? pairOf(doc.contents, "layout") : undefined;
      if (lp) {
        const k = (lp.key as Ranged).range;
        return splice(t, lineStart(t, k[0]), itemEnd(t, (lp.value as Ranged | null) ?? (lp.key as Ranged)), `layout:\n${block}`);
      }
      return `${ensureNl(t)}layout:\n${block}`;
    }
    let out = t;
    const fresh: string[] = [];
    // replace in place from the end so earlier offsets stay valid
    const existing = ids.map((id) => [id, pairOf(layout, id)] as const).filter(([, p]) => p);
    for (const [id, p] of existing.sort((a, b) => (b[1]!.value as Ranged).range[0] - (a[1]!.value as Ranged).range[0])) {
      const [from, to] = (p!.value as Ranged).range;
      out = splice(out, from, to, line(id).slice(id.length + 2));
    }
    for (const id of ids) if (!pairOf(layout, id)) fresh.push(id);
    if (!fresh.length) return out;
    return edit(out, (t2, d2) => appendTo(t2, d2.get("layout", true) as YAMLMap, (ind) => fresh.map((id) => `${ind}${line(id)}\n`).join("")));
  });
}

/** Palette drop → a node block appended after the last node. */
export function addNode(text: string, node: Record<string, unknown> & { id: string; type: string }): string {
  return edit(text, (t, doc) => {
    if (nodeItem(doc, node.id)) throw new Error(`node ${node.id} already exists`);
    const nodes = seqOf(doc, "nodes");
    const block = stringify([node], { flowCollectionPadding: false, lineWidth: 0 });
    if (!nodes || !nodes.items.length) return `${ensureNl(t.replace(/^nodes:.*\n?/m, ""))}nodes:\n${block.split("\n").filter(Boolean).map((l) => `  ${l}`).join("\n")}\n`;
    return appendTo(t, nodes, (ind) => block.split("\n").filter(Boolean).map((l) => ind + l).join("\n") + "\n");
  });
}

export function addEdge(text: string, e: GraphEdge): string {
  const item = inline(e.on ? { from: e.from, to: e.to, on: e.on } : { from: e.from, to: e.to });
  return edit(text, (t, doc) => {
    const edges = seqOf(doc, "edges");
    if (edges?.items.some((x) => isMap(x) && x.get("from") === e.from && x.get("to") === e.to && (x.get("on") ?? undefined) === e.on)) return t;
    if (!edges || !edges.items.length) return `${ensureNl(t.replace(/^edges:.*\n?/m, ""))}edges:\n  - ${item}\n`;
    return appendTo(t, edges, (ind) => `${ind}- ${item}\n`);
  });
}

/** Drops matching `edges:` items (an `on` of undefined matches any). Implicit rule edges are edited via setPath. */
export function removeEdge(text: string, from: string, to: string, on?: string): string {
  return edit(text, (t, doc) =>
    cutAll(
      t,
      (seqOf(doc, "edges")?.items ?? [])
        .filter((e): e is YAMLMap => isMap(e) && e.get("from") === from && e.get("to") === to && (on === undefined || e.get("on") === on))
        .map((e) => [lineStart(t, e.range![0]), itemEnd(t, e)]),
    ),
  );
}

/** Delete removes the node's lines, its edges' lines and its layout line. Nothing else moves. */
export function removeNode(text: string, id: string): string {
  return edit(text, (t, doc) => {
    const cuts: [number, number][] = [];
    const nodes = seqOf(doc, "nodes");
    const n = nodeItem(doc, id);
    if (!nodes || !n) throw new Error(`no node ${id}`);
    const i = nodes.items.indexOf(n);
    const next = nodes.items[i + 1] as Ranged | undefined;
    cuts.push([lineStart(t, n.range![0]), next ? lineStart(t, next.range[0]) : itemEnd(t, n)]);
    for (const e of seqOf(doc, "edges")?.items ?? []) {
      if (isMap(e) && (e.get("from") === id || e.get("to") === id)) cuts.push([lineStart(t, e.range![0]), itemEnd(t, e)]);
    }
    const layout = doc.get("layout", true);
    const lp = isMap(layout) ? pairOf(layout, id) : undefined;
    if (lp && isMap(layout) && layout.items.length === 1) {
      // last layout entry: drop the whole `layout:` block so a later move doesn't add a second one
      const top = pairOf(doc.contents as YAMLMap, "layout")!;
      cuts.push([lineStart(t, (top.key as Ranged).range[0]), itemEnd(t, lp.value as Ranged)]);
    } else if (lp) cuts.push([lineStart(t, (lp.key as Ranged).range[0]), itemEnd(t, lp.value as Ranged)]);
    return cutAll(t, cuts);
  });
}

function seqAt(doc: Document, id: string, path: Path): YAMLSeq | undefined {
  const n = nodeItem(doc, id);
  if (!n) throw new Error(`no node ${id}`);
  const s = n.getIn(path, true);
  return isSeq(s) ? s : undefined;
}

/** Append one item to a list inside a node (a rule case, an output). Block lists get one `- {…}` line. */
export function appendItem(text: string, id: string, path: Path, value: unknown): string {
  return edit(text, (t, doc) => {
    const s = seqAt(doc, id, path);
    if (!s || s.flow || !s.items.length) return setPath(t, id, path, [...((s?.toJSON() as unknown[]) ?? []), value]);
    return appendTo(t, s, (ind) => `${ind}- ${inline(value)}\n`);
  });
}

/** Remove item `index` of a list inside a node; only that item's lines go. */
export function removeItem(text: string, id: string, path: Path, index: number): string {
  return edit(text, (t, doc) => {
    const s = seqAt(doc, id, path);
    const item = s?.items[index] as Ranged | undefined;
    if (!s || !item) throw new Error(`no item ${index}`);
    if (s.flow) return setPath(t, id, path, (s.toJSON() as unknown[]).filter((_, i) => i !== index));
    const next = s.items[index + 1] as Ranged | undefined;
    return splice(t, lineStart(t, item.range[0]), next ? lineStart(t, next.range[0]) : itemEnd(t, item), "");
  });
}

/** A node id not used yet: `notify`, `notify_2`, … */
export function freshId(text: string, base: string): string {
  const ids = new Set(toGraph(text).nodes.map((n) => n.id));
  const b = base.replace(/[^a-z0-9_]/g, "_").replace(/^[^a-z]+/, "") || "node";
  if (!ids.has(b)) return b;
  let i = 2;
  while (ids.has(`${b}_${i}`)) i++;
  return `${b}_${i}`;
}
