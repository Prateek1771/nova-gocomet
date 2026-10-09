"use client";

import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Cloud,
  CloudOff,
  Columns2,
  GitCompare,
  LayoutGrid,
  Loader2,
  Redo2,
  Rocket,
  Trash2,
  Undo2,
  Wand2,
  Workflow,
  FileCode2,
  History,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { parseDocument } from "yaml";

import { cx } from "@/components/ui";
import { api, ClientError, postJson } from "@/lib/client";
import type { Catalog, Validation, ValidationIssue, VersionBrief } from "@/lib/types";

import { autoLayout, NODE_W } from "./auto-layout";
import { Canvas, edgeId, type PaletteDrop } from "./canvas";
import { Inspector, type NodeEdits } from "./inspector";
import { Palette } from "./palette";
import { type Marker, YamlDiff, YamlPane } from "./yaml-pane";
import {
  addEdge,
  addNode,
  appendItem,
  freshId,
  type Graph,
  type GraphEdge,
  moveNode,
  moveNodes,
  removeEdge,
  removeItem,
  removeNode,
  setPath,
  toGraph,
  yamlErrors,
} from "./yaml-graph";

type View = "canvas" | "split" | "yaml" | "diff";
type Save = "saved" | "dirty" | "saving" | "error";
type Json = Record<string, unknown>;

const lineOfNode = (text: string, id: string) => {
  const i = text.split(/\r?\n/).findIndex((l) => new RegExp(`^\\s*-\\s+id:\\s*["']?${id}["']?\\s*$`).test(l));
  return i < 0 ? 1 : i + 1;
};

/** Defaults for a dropped block: enough to be valid where possible, and obvious to finish in the inspector. */
function blockFor(d: PaletteDrop, id: string, catalog: Catalog, ids: string[]): Json & { id: string; type: string } {
  const params = (entries: Catalog["agents"]) =>
    Object.fromEntries(Object.entries(entries.find((e) => e.key === d.ref)?.with ?? {}).filter(([, p]) => p.required).map(([k]) => [k, ""]));
  switch (d.type) {
    case "agent":
      return { id, type: "agent", agent: d.ref ?? catalog.agents[0]?.key ?? "", with: params(catalog.agents) };
    case "action":
      return { id, type: "action", action: d.ref ?? "noop", with: params(catalog.actions) };
    case "human_task":
      return { id, type: "human_task", title: "Review", assignee: { role: "ops_exec" }, app: d.ref ?? "generic_review", sla: "4h", outputs: ["approved", "rejected"] };
    case "decide":
      return { id, type: "decide", questions: [{ id: "q1", ask: "Is this a yes?", context: "", threshold: 0.5 }] };
    case "rule":
      return { id, type: "rule", cases: [{ when: "true", goto: ids[0] ?? id }], default: ids[0] ?? id };
    case "wait":
      return { id, type: "wait", duration: "1h" };
    default:
      return { id, type: d.type };
  }
}

export default function Studio({
  wfKey,
  initialYaml,
  initialValidation,
  catalog,
  initialVersions,
}: {
  wfKey: string;
  initialYaml: string;
  initialValidation: Validation;
  catalog: Catalog;
  initialVersions: VersionBrief[];
}) {
  // the YAML text is the only state that matters; everything else is derived from it
  const [hist, setHist] = useState({ stack: [initialYaml], i: 0, at: 0 });
  const text = hist.stack[hist.i];
  const [validation, setValidation] = useState(initialValidation);
  const [save, setSave] = useState<Save>("saved");
  const savedText = useRef(initialYaml);
  const [versions, setVersions] = useState(initialVersions);
  const [viewing, setViewing] = useState<{ version: number; yaml: string } | null>(null);
  const [view, setView] = useState<View>("canvas"); // YAML on demand: split + palette + inspector is cramped below ~1700 px
  const [selected, setSelected] = useState<string | null>(null);
  const [selEdge, setSelEdge] = useState<GraphEdge | null>(null);
  const [autoPos, setAutoPos] = useState<Record<string, { x: number; y: number }>>({});
  const [toast, setToast] = useState<{ tone: "ok" | "bad"; msg: string } | null>(null);
  const [confirmPublish, setConfirmPublish] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [showIssues, setShowIssues] = useState(false);
  const [reveal, setReveal] = useState<{ line: number; n: number } | null>(null);
  const readOnly = viewing !== null;
  const shown = viewing?.yaml ?? text;

  const commit = useCallback((next: string, coalesce = false) => {
    const now = Date.now();
    setHist((h) => {
      if (next === h.stack[h.i]) return h;
      const merge = coalesce && now - h.at < 600 && h.i > 0; // typing: one undo step per pause
      const base = h.stack.slice(0, merge ? h.i : h.i + 1);
      return { stack: [...base, next].slice(-200), i: Math.min(base.length, 199), at: now };
    });
  }, []);
  const apply = useCallback(
    (fn: (t: string) => string, coalesce = false) => {
      try {
        commit(fn(text), coalesce);
      } catch (e) {
        setToast({ tone: "bad", msg: e instanceof Error ? e.message : String(e) });
      }
    },
    [commit, text],
  );

  // graph: from the newest text that parses, so a typo in the YAML pane doesn't blank the canvas
  const errors = useMemo(() => yamlErrors(shown), [shown]);
  const graph = useMemo(() => {
    const candidates = viewing ? [viewing.yaml] : hist.stack.slice(0, hist.i + 1).reverse();
    for (const t of candidates) {
      try {
        return toGraph(t);
      } catch {
        /* older text */
      }
    }
    return { nodes: [], edges: [], hasLayout: false } as Graph;
  }, [viewing, hist]);
  const nodesJs = useMemo(() => {
    try {
      return ((parseDocument(shown).toJS() as { nodes?: Json[] })?.nodes ?? []).filter(Boolean);
    } catch {
      return [];
    }
  }, [shown]);
  const title = useMemo(() => {
    try {
      return ((parseDocument(shown).toJS() as { metadata?: { title?: string } })?.metadata?.title as string | undefined) ?? wfKey;
    } catch {
      return wfKey;
    }
  }, [shown, wfKey]);

  // auto-layout nodes that have no `layout:` entry (in memory only, until the user moves or clicks Auto-layout)
  const unplacedKey = graph.nodes.filter((n) => !n.placed).map((n) => n.id).join(",") + "|" + graph.edges.length;
  useEffect(() => {
    if (!graph.nodes.some((n) => !n.placed)) return;
    let live = true;
    void autoLayout(graph).then((p) => live && setAutoPos(p));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-run only when the unplaced set changes
  }, [unplacedKey]);
  const positions = useMemo(() => Object.fromEntries(graph.nodes.map((n) => [n.id, n.placed ? { x: n.x, y: n.y } : (autoPos[n.id] ?? { x: n.x, y: n.y })])), [graph, autoPos]);

  const issuesBy = useMemo(() => {
    const out: Record<string, string[]> = {};
    for (const i of validation.issues) if (i.node_id) (out[i.node_id] ??= []).push(`${i.code}: ${i.message}`);
    return out;
  }, [validation]);
  const markers = useMemo<Marker[]>(
    () => [
      ...errors.map((e) => ({ ...e, severity: "error" as const })),
      ...(readOnly ? [] : validation.issues.map((i) => ({ message: `${i.code}: ${i.message}`, line: i.node_id ? lineOfNode(text, i.node_id) : 1, col: 1, severity: "warning" as const }))),
    ],
    [errors, validation, text, readOnly],
  );

  // autosave + validate: PUT draft returns the validation (a draft may be invalid; publish is the gate)
  const saveNow = useCallback(async (yaml: string) => {
    setSave("saving");
    try {
      const r = await api<Validation>(`/workflows/${wfKey}/draft`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ yaml }) });
      savedText.current = yaml;
      setValidation({ valid: r.valid, issues: r.issues });
      setSave("saved");
    } catch {
      setSave("error");
    }
  }, [wfKey]);
  useEffect(() => {
    if (text === savedText.current) return;
    setSave("dirty");
    const t = setTimeout(() => void saveNow(text), 700);
    return () => clearTimeout(t);
  }, [text, saveNow]);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 3500);
    return () => clearTimeout(t);
  }, [toast]);

  const ids = graph.nodes.map((n) => n.id);
  const selNode = nodesJs.find((n) => n.id === selected) as Json | undefined;

  const removeSelected = useCallback(() => {
    if (readOnly) return;
    if (selected) {
      apply((t) => removeNode(t, selected));
      setSelected(null);
    } else if (selEdge) {
      apply((t) => {
        if (!selEdge.implicit) return removeEdge(t, selEdge.from, selEdge.to, selEdge.on);
        if (selEdge.on === "default") return setPath(t, selEdge.from, ["default"], undefined);
        return removeItem(t, selEdge.from, ["cases"], Number(selEdge.on?.replace("case ", "")) - 1);
      });
      setSelEdge(null);
    }
  }, [apply, readOnly, selEdge, selected]);

  const connect = useCallback(
    (from: string, to: string) => {
      const src = nodesJs.find((n) => n.id === from);
      apply((t) => {
        if (src?.type === "rule") return src.default ? appendItem(t, from, ["cases"], { when: "false", goto: to }) : setPath(t, from, ["default"], to);
        if (src?.type === "human_task") {
          const used = new Set(graph.edges.filter((e) => e.from === from).map((e) => e.on));
          const outs = (src.outputs as string[] | undefined) ?? ["approved", "rejected"];
          return addEdge(t, { from, to, on: outs.find((o) => !used.has(o)) ?? outs[0] });
        }
        return addEdge(t, { from, to });
      });
    },
    [apply, graph.edges, nodesJs],
  );

  const add = useCallback(
    (d: PaletteDrop, pos?: { x: number; y: number }) => {
      const id = freshId(text, d.ref?.split(".").at(-1) ?? d.type);
      const anchor = selected ? positions[selected] : undefined;
      // no drop point: under the selection, else to the right of everything (never on top of a node)
      const all = Object.values(positions);
      const right = all.length ? { x: Math.max(...all.map((p) => p.x)) + NODE_W + 64, y: Math.min(...all.map((p) => p.y)) } : { x: 0, y: 0 };
      const at = pos ?? (anchor ? { x: anchor.x + NODE_W + 48, y: anchor.y + 120 } : right);
      const from = !pos && selected && ["agent", "action", "decide", "wait"].includes(String(nodesJs.find((n) => n.id === selected)?.type)) ? selected : null;
      apply((t) => {
        let out = moveNode(addNode(t, blockFor(d, id, catalog, ids)), id, at.x, at.y);
        if (from) out = addEdge(out, { from, to: id });
        return out;
      });
      setSelected(id);
      setSelEdge(null);
    },
    [apply, catalog, ids, nodesJs, positions, selected, text],
  );

  const relayout = useCallback(async () => {
    const p = await autoLayout(graph);
    apply((t) => moveNodes(t, p));
  }, [apply, graph]);

  const publish = useCallback(async () => {
    setPublishing(true);
    try {
      if (text !== savedText.current) await saveNow(text);
      const r = await postJson<{ version: number }>(`/workflows/${wfKey}/publish`);
      setVersions(await api<VersionBrief[]>(`/workflows/${wfKey}/versions`));
      setToast({ tone: "ok", msg: `Published v${r.version}. Runs already in flight stay on their version.` });
    } catch (e) {
      setToast({ tone: "bad", msg: e instanceof ClientError ? e.message : "Publish failed" });
    } finally {
      setPublishing(false);
      setConfirmPublish(false);
    }
  }, [saveNow, text, wfKey]);

  const openVersion = useCallback(
    async (v: string) => {
      if (v === "draft") return setViewing(null);
      const r = await api<{ yaml: string; version: number }>(`/workflows/${wfKey}/versions/${v}`);
      setViewing({ version: r.version, yaml: r.yaml });
      setSelected(null);
    },
    [wfKey],
  );

  // keyboard: ⌘/Ctrl+S save · ⌘/Ctrl+Z undo · ⌘/Ctrl+Shift+Z (or Y) redo · Del delete selection
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      const typing = el.closest("input, textarea, select, .monaco-editor, [contenteditable=true]");
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === "s") {
        e.preventDefault();
        if (!readOnly) void saveNow(text);
        return;
      }
      if (typing) return;
      if (mod && e.key.toLowerCase() === "z") {
        e.preventDefault();
        setHist((h) => ({ ...h, i: e.shiftKey ? Math.min(h.stack.length - 1, h.i + 1) : Math.max(0, h.i - 1) }));
      } else if (mod && e.key.toLowerCase() === "y") {
        e.preventDefault();
        setHist((h) => ({ ...h, i: Math.min(h.stack.length - 1, h.i + 1) }));
      } else if (e.key === "Delete" || e.key === "Backspace") {
        removeSelected();
      } else if (e.key === "Escape") {
        setSelected(null);
        setSelEdge(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [readOnly, removeSelected, saveNow, text]);

  const edits: NodeEdits | null = selected
    ? {
        set: (p, v) => apply((t) => setPath(t, selected, p, v), true),
        append: (p, v) => apply((t) => appendItem(t, selected, p, v)),
        remove: (p, i) => apply((t) => removeItem(t, selected, p, i)),
        deleteNode: removeSelected,
        showYaml: () => {
          if (view === "canvas") setView("split");
          setReveal((r) => ({ line: lineOfNode(text, selected), n: (r?.n ?? 0) + 1 }));
        },
      }
    : null;

  const latest = versions[0]?.version ?? null;
  const blocked = errors.length > 0 || !validation.valid || save === "saving" || readOnly;
  const nIssues = validation.issues.length;
  const selIssues: ValidationIssue[] = selected ? validation.issues.filter((i) => i.node_id === selected) : [];

  return (
    <div className="flex h-full flex-col">
      {/* toolbar */}
      <div className="flex flex-wrap items-center gap-2 border-b border-line bg-panel px-3 py-2">
        <Link href="/studio" className="rounded p-1 text-muted hover:bg-canvas hover:text-ink" aria-label="Back to workflows">
          <ArrowLeft className="size-4" />
        </Link>
        <Workflow className="size-4 text-accent" aria-hidden />
        <h1 className="truncate text-sm font-semibold">{title}</h1>
        <span className="font-mono text-[11px] text-muted">{wfKey}</span>
        <span className="mx-1 h-4 w-px bg-line" aria-hidden />
        <label className="flex items-center gap-1 text-xs">
          <History className="size-3.5 text-muted" aria-hidden />
          <span className="sr-only">Version</span>
          <select value={viewing ? String(viewing.version) : "draft"} onChange={(e) => void openVersion(e.target.value)} className="rounded-md border border-line bg-panel px-1.5 py-0.5 text-xs">
            <option value="draft">Draft</option>
            {versions.map((v) => (
              <option key={v.version} value={v.version}>
                v{v.version}
                {v.version === latest ? " (live)" : ""}
              </option>
            ))}
          </select>
        </label>
        {!readOnly && (
          <span className="flex items-center gap-1 text-[11px] text-muted" role="status" aria-live="polite">
            {save === "saving" ? <Loader2 className="size-3.5 animate-spin" /> : save === "error" ? <CloudOff className="size-3.5 text-bad" /> : <Cloud className="size-3.5" />}
            {save === "saving" ? "Saving…" : save === "dirty" ? "Unsaved" : save === "error" ? "Save failed" : "Draft saved"}
          </span>
        )}
        <div className="relative">
          <button
            type="button"
            onClick={() => setShowIssues((s) => !s)}
            aria-expanded={showIssues}
            className={cx(
              "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium",
              errors.length ? "bg-bad-soft text-bad" : nIssues ? "bg-warn-soft text-warn" : "bg-ok-soft text-ok",
            )}
          >
            {errors.length || nIssues ? <AlertTriangle className="size-3.5" /> : <CheckCircle2 className="size-3.5" />}
            {errors.length ? `YAML error` : nIssues ? `${nIssues} issue${nIssues > 1 ? "s" : ""}` : "Valid"}
          </button>
          {showIssues && (errors.length > 0 || nIssues > 0) && (
            <ul className="absolute left-0 top-7 z-30 w-96 space-y-1 rounded-lg border border-line bg-panel p-2 shadow-lg">
              {errors.map((e, k) => (
                <li key={`y${k}`}>
                  <button type="button" className="w-full rounded px-2 py-1 text-left text-xs hover:bg-canvas" onClick={() => setReveal((r) => ({ line: e.line, n: (r?.n ?? 0) + 1 }))}>
                    <b className="text-bad">Line {e.line}</b> {e.message}
                  </button>
                </li>
              ))}
              {validation.issues.map((i, k) => (
                <li key={k}>
                  <button
                    type="button"
                    className="w-full rounded px-2 py-1 text-left text-xs hover:bg-canvas"
                    onClick={() => {
                      if (i.node_id) setSelected(i.node_id);
                      setShowIssues(false);
                    }}
                  >
                    <b className="font-mono text-warn">{i.code}</b> {i.node_id && <span className="font-mono text-muted">[{i.node_id}]</span>} {i.message}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="ml-auto flex items-center gap-1">
          <button type="button" disabled={hist.i === 0 || readOnly} onClick={() => setHist((h) => ({ ...h, i: h.i - 1 }))} className="rounded p-1.5 text-muted hover:bg-canvas hover:text-ink disabled:opacity-40" aria-label="Undo" title="Undo (Ctrl+Z)">
            <Undo2 className="size-4" />
          </button>
          <button type="button" disabled={hist.i >= hist.stack.length - 1 || readOnly} onClick={() => setHist((h) => ({ ...h, i: h.i + 1 }))} className="rounded p-1.5 text-muted hover:bg-canvas hover:text-ink disabled:opacity-40" aria-label="Redo" title="Redo (Ctrl+Shift+Z)">
            <Redo2 className="size-4" />
          </button>
          <button type="button" disabled={readOnly || errors.length > 0} onClick={() => void relayout()} className="rounded p-1.5 text-muted hover:bg-canvas hover:text-ink disabled:opacity-40" aria-label="Auto-layout" title="Auto-layout (writes layout:)">
            <Wand2 className="size-4" />
          </button>
          <div className="mx-1 flex rounded-md border border-line p-0.5" role="group" aria-label="View">
            {(
              [
                ["canvas", LayoutGrid, "Canvas"],
                ["split", Columns2, "Split"],
                ["yaml", FileCode2, "YAML"],
                ["diff", GitCompare, "Diff vs live"],
              ] as const
            ).map(([v, Icon, label]) => (
              <button
                key={v}
                type="button"
                onClick={() => setView(v)}
                disabled={v === "diff" && latest === null}
                aria-pressed={view === v}
                title={label}
                className={cx("inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium disabled:opacity-40", view === v ? "bg-accent-soft text-accent" : "text-muted hover:text-ink")}
              >
                <Icon className="size-3.5" />
                <span className="hidden xl:inline">{label}</span>
              </button>
            ))}
          </div>
          <div className="relative">
            <button
              type="button"
              disabled={blocked || publishing}
              onClick={() => setConfirmPublish(true)}
              className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-on-accent hover:opacity-90 disabled:opacity-50"
              title={blocked ? "Fix the issues first" : "Publish a new immutable version"}
            >
              {publishing ? <Loader2 className="size-3.5 animate-spin" /> : <Rocket className="size-3.5" />}
              Publish
            </button>
            {confirmPublish && (
              <div className="absolute right-0 top-9 z-30 w-72 rounded-lg border border-line bg-panel p-3 shadow-lg" role="dialog" aria-label="Confirm publish">
                <p className="text-sm font-medium">Publish v{(latest ?? 0) + 1}?</p>
                <p className="mt-1 text-xs text-muted">New runs start on v{(latest ?? 0) + 1}. Runs already in flight stay pinned to {latest ? `v${latest}` : "their version"}.</p>
                <div className="mt-3 flex justify-end gap-2">
                  <button type="button" onClick={() => setConfirmPublish(false)} className="rounded-md border border-line px-2.5 py-1 text-xs hover:bg-canvas">
                    Cancel
                  </button>
                  <button type="button" autoFocus onClick={() => void publish()} className="rounded-md bg-accent px-2.5 py-1 text-xs font-semibold text-on-accent">
                    Publish v{(latest ?? 0) + 1}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {readOnly && (
        <div className="flex items-center gap-3 border-b border-line bg-info-soft px-4 py-1.5 text-xs text-info">
          Viewing published v{viewing.version} (read-only).
          <button type="button" className="font-semibold underline" onClick={() => setViewing(null)}>
            Back to draft
          </button>
          <button
            type="button"
            className="font-semibold underline"
            onClick={() => {
              commit(viewing.yaml);
              setViewing(null);
            }}
          >
            Restore into draft
          </button>
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {!readOnly && view !== "yaml" && view !== "diff" && (
          <div className="w-56 shrink-0 border-r border-line bg-panel">
            <Palette catalog={catalog} onAdd={(d) => add(d)} />
          </div>
        )}
        <div className="flex min-w-0 flex-1">
          {view === "diff" ? (
            <div className="min-w-0 flex-1 bg-panel">
              <DiffView wfKey={wfKey} latest={latest} draft={text} />
            </div>
          ) : (
            <>
              {view !== "yaml" && (
                <div className="relative min-w-0 flex-1">
                  {errors.length > 0 && (
                    <div role="alert" className="absolute inset-x-3 top-3 z-10 flex items-center gap-2 rounded-lg border border-bad/40 bg-bad-soft px-3 py-2 text-xs text-bad shadow-sm">
                      <AlertTriangle className="size-4 shrink-0" />
                      <span>
                        YAML doesn&apos;t parse (line {errors[0].line}: {errors[0].message}). The canvas shows the last valid graph.
                      </span>
                    </div>
                  )}
                  <Canvas
                    graph={graph}
                    positions={positions}
                    fitKey={`${Object.keys(autoPos).length}`}
                    issues={readOnly ? undefined : issuesBy}
                    selected={selected}
                    selectedEdge={selEdge ? edgeId(selEdge) : null}
                    editable={!readOnly && errors.length === 0}
                    onSelect={(id) => {
                      setSelected(id);
                      if (id) setSelEdge(null);
                    }}
                    onSelectEdge={(e) => {
                      setSelEdge(e);
                      if (e) setSelected(null);
                    }}
                    onMove={(id, x, y) => apply((t) => moveNode(t, id, x, y))}
                    onConnect={connect}
                    onDropNode={readOnly ? undefined : add}
                  />
                </div>
              )}
              {view !== "canvas" && (
                <div className={cx("min-w-0 border-l border-line bg-panel", view === "yaml" ? "flex-1" : "w-[44%] max-w-160")}>
                  <YamlPane
                    value={shown}
                    // Monaco also reports programmatic value swaps (e.g. opening an old version): only user edits on the draft count
                    onChange={(v) => !readOnly && v !== text && commit(v, true)}
                    markers={markers}
                    readOnly={readOnly}
                    reveal={reveal}
                  />
                </div>
              )}
            </>
          )}
        </div>
        {selNode && edits && !readOnly && view !== "diff" && (
          <div className="w-80 shrink-0 border-l border-line bg-panel">
            <Inspector key={selected} node={selNode} ids={ids} catalog={catalog} issues={selIssues} ed={edits} onClose={() => setSelected(null)} />
          </div>
        )}
        {selEdge && !readOnly && view !== "diff" && (
          <div className="w-72 shrink-0 space-y-3 border-l border-line bg-panel p-4">
            <h2 className="text-sm font-semibold">Connection</h2>
            <p className="font-mono text-xs">
              {selEdge.from} → {selEdge.to}
            </p>
            {selEdge.on && <p className="text-xs text-muted">{selEdge.implicit ? `Rule branch ${selEdge.on}` : `On outcome “${selEdge.on}”`}</p>}
            {selEdge.label && <code className="block rounded bg-canvas p-2 font-mono text-[11px]">{selEdge.label}</code>}
            {selEdge.implicit && <p className="text-xs text-muted">Rule branches live on the rule node; edit the condition in its inspector.</p>}
            <button type="button" onClick={removeSelected} className="inline-flex items-center gap-1.5 rounded-md border border-line px-3 py-1.5 text-xs font-medium text-bad hover:bg-bad-soft">
              <Trash2 className="size-3.5" /> Delete connection
            </button>
          </div>
        )}
      </div>

      {toast && (
        <div role="status" className={cx("fixed bottom-5 left-1/2 z-50 -translate-x-1/2 rounded-lg px-4 py-2 text-sm font-medium shadow-lg", toast.tone === "ok" ? "bg-ok text-white dark:text-black" : "bg-bad text-white dark:text-black")}>
          {toast.msg}
        </div>
      )}
    </div>
  );
}

function DiffView({ wfKey, latest, draft }: { wfKey: string; latest: number | null; draft: string }) {
  const [live, setLive] = useState<string | null>(null);
  useEffect(() => {
    if (latest === null) return;
    void api<{ yaml: string }>(`/workflows/${wfKey}/versions/${latest}`).then((r) => setLive(r.yaml));
  }, [latest, wfKey]);
  if (latest === null) return <p className="p-4 text-sm text-muted">Nothing published yet.</p>;
  if (live === null) return <p className="p-4 text-sm text-muted">Loading v{latest}…</p>;
  return <YamlDiff original={live} modified={draft} labels={[`v${latest} (live)`, "Draft"]} />;
}
