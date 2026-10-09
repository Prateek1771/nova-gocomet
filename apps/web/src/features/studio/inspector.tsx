"use client";

import { AlertTriangle, Code2, Plus, Trash2, X } from "lucide-react";
import type { ReactNode } from "react";

import { cx } from "@/components/ui";
import type { Catalog, CatalogEntry, ValidationIssue } from "@/lib/types";

import { metaOf } from "./node-meta";

type Path = (string | number)[];
type Json = Record<string, unknown>;
export type NodeEdits = {
  set: (path: Path, value: unknown) => void;
  append: (path: Path, value: unknown) => void;
  remove: (path: Path, index: number) => void;
  deleteNode: () => void;
  showYaml: () => void;
};

const input = "w-full rounded-md border border-line bg-panel px-2 py-1 text-xs outline-none focus:border-accent focus:ring-2 focus:ring-accent/20 disabled:opacity-60";
const mono = `${input} font-mono`;

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="flex items-baseline justify-between gap-2 text-[11px] font-medium text-muted">
        {label}
        {hint && <span className="truncate font-normal">{hint}</span>}
      </span>
      {children}
    </label>
  );
}

const str = (v: unknown) => (v === undefined || v === null ? "" : typeof v === "string" ? v : JSON.stringify(v));
/** Text back to a value: JSON when it parses as an object/array/number/bool, else the string. Empty → unset. */
const val = (s: string): unknown => {
  if (s === "") return undefined;
  if (/^[[{]|^(true|false|-?\d+(\.\d+)?)$/.test(s.trim())) {
    try {
      return JSON.parse(s);
    } catch {
      /* keep the string */
    }
  }
  return s;
};

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2.5 border-t border-line px-4 py-3">
      <h3 className="text-[10px] font-semibold uppercase tracking-wider text-muted">{title}</h3>
      {children}
    </section>
  );
}

function RefSelect({ value, entries, onChange, label }: { value: string; entries: CatalogEntry[]; onChange: (v: string) => void; label: string }) {
  const known = entries.some((e) => e.key === value);
  return (
    <Field label={label}>
      <select value={value} onChange={(e) => onChange(e.target.value)} className={cx(input, !known && "border-bad text-bad")}>
        {!known && <option value={value}>{value || "(none)"} (unknown)</option>}
        {entries.map((e) => (
          <option key={e.key} value={e.key}>
            {e.title} · {e.key}
          </option>
        ))}
      </select>
    </Field>
  );
}

function WithParams({ node, entry, ed }: { node: Json; entry?: CatalogEntry; ed: NodeEdits }) {
  const w = (node.with ?? {}) as Json;
  const keys = [...new Set([...Object.keys(entry?.with ?? {}), ...Object.keys(w)])];
  if (!keys.length) return <p className="text-xs text-muted">No inputs.</p>;
  return (
    <>
      {keys.map((k) => {
        const p = entry?.with?.[k];
        return (
          <Field key={k} label={`${k}${p?.required ? " *" : ""}`} hint={p?.hint ?? (p?.type === "expression" ? "${{ … }}" : undefined)}>
            <input className={mono} value={str(w[k])} onChange={(e) => ed.set(["with", k], val(e.target.value))} placeholder={p?.type === "expression" ? "${{ nodes.x.output }}" : ""} />
          </Field>
        );
      })}
    </>
  );
}

function GotoSelect({ value, onChange, label, ids, self }: { value: string; onChange: (v: string) => void; label: string; ids: string[]; self: string }) {
  return (
    <Field label={label}>
      <select value={value} onChange={(e) => onChange(e.target.value)} className={cx(input, value && !ids.includes(value) && "border-bad text-bad")}>
        {!ids.includes(value) && <option value={value}>{value || "(choose)"}</option>}
        {ids
          .filter((x) => x !== self)
          .map((x) => (
            <option key={x}>{x}</option>
          ))}
      </select>
    </Field>
  );
}

/** Side drawer for the selected node. Every change is one targeted YAML edit (setPath / appendItem / removeItem). */
export function Inspector({ node, ids, catalog, issues, ed, onClose }: { node: Json; ids: string[]; catalog: Catalog; issues: ValidationIssue[]; ed: NodeEdits; onClose: () => void }) {
  const type = String(node.type);
  const m = metaOf(type);
  const Icon = m.icon;
  const id = String(node.id);
  const others = ids.filter((x) => x !== id);
  return (
    <aside className="flex h-full flex-col" aria-label={`Node ${id}`}>
      <header className="flex items-center gap-2.5 px-4 py-3">
        <span className={cx("grid size-8 place-items-center rounded-lg", m.tile)}>
          <Icon className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate font-mono text-sm font-semibold">{id}</div>
          <div className="text-[11px] text-muted">{m.label}</div>
        </div>
        <button type="button" onClick={ed.showYaml} className="rounded p-1 text-muted hover:bg-canvas hover:text-ink" title="Show in YAML" aria-label="Show in YAML">
          <Code2 className="size-4" />
        </button>
        <button type="button" onClick={onClose} className="rounded p-1 text-muted hover:bg-canvas hover:text-ink" aria-label="Close inspector">
          <X className="size-4" />
        </button>
      </header>
      {m.blurb && <p className="px-4 pb-3 text-xs text-muted">{m.blurb}</p>}
      {issues.length > 0 && (
        <ul className="mx-4 mb-3 space-y-1 rounded-lg border border-bad/30 bg-bad-soft p-2">
          {issues.map((i, k) => (
            <li key={k} className="flex gap-1.5 text-xs text-bad">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
              <span>
                <b className="font-mono">{i.code}</b> {i.message}
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {type === "agent" && (
          <>
            <Section title="Agent">
              <RefSelect label="Agent" value={str(node.agent)} entries={catalog.agents} onChange={(v) => ed.set(["agent"], v)} />
              <p className="text-xs text-muted">{catalog.agents.find((a) => a.key === node.agent)?.description}</p>
            </Section>
            <Section title="Inputs">
              <WithParams node={node} entry={catalog.agents.find((a) => a.key === node.agent)} ed={ed} />
            </Section>
          </>
        )}
        {type === "action" && (
          <>
            <Section title="Action">
              <RefSelect label="Action" value={str(node.action)} entries={catalog.actions} onChange={(v) => ed.set(["action"], v)} />
              <p className="text-xs text-muted">{catalog.actions.find((a) => a.key === node.action)?.description}</p>
            </Section>
            <Section title="Inputs">
              <WithParams node={node} entry={catalog.actions.find((a) => a.key === node.action)} ed={ed} />
            </Section>
          </>
        )}
        {type === "decide" &&
          ((node.questions ?? []) as Json[]).map((q, i) => {
            const c = (q.criteria ?? {}) as Json;
            const p = ["questions", i];
            return (
              <Section key={i} title={`Question ${i + 1} · ${str(q.id)}`}>
                <Field label="Ask">
                  <textarea className={cx(input, "h-14 resize-y")} value={str(q.ask)} onChange={(e) => ed.set([...p, "ask"], e.target.value)} />
                </Field>
                <Field label="Context" hint="${{ … }}">
                  <input className={mono} value={str(q.context)} onChange={(e) => ed.set([...p, "context"], val(e.target.value))} />
                </Field>
                <Field label="Yes means">
                  <textarea className={cx(input, "h-16 resize-y")} value={str(c.true)} onChange={(e) => ed.set([...p, "criteria", "true"], e.target.value)} />
                </Field>
                <Field label="No means">
                  <textarea className={cx(input, "h-12 resize-y")} value={str(c.false)} onChange={(e) => ed.set([...p, "criteria", "false"], e.target.value)} />
                </Field>
                <Field label="Threshold" hint={`yes when P(yes) ≥ ${Number(q.threshold ?? 0.5).toFixed(2)}`}>
                  <div className="flex items-center gap-2">
                    <input type="range" min={0} max={1} step={0.05} value={Number(q.threshold ?? 0.5)} onChange={(e) => ed.set([...p, "threshold"], Number(e.target.value))} className="flex-1 accent-accent" aria-label="Threshold" />
                    <span className="num w-9 text-right font-mono text-xs">{Number(q.threshold ?? 0.5).toFixed(2)}</span>
                  </div>
                </Field>
              </Section>
            );
          })}
        {type === "rule" && (
          <Section title="Branches (first match wins)">
            {((node.cases ?? []) as Json[]).map((c, i) => (
              <div key={i} className="space-y-1.5 rounded-lg border border-line p-2">
                <div className="flex items-center justify-between text-[11px] font-medium text-muted">
                  Case {i + 1}
                  <button type="button" onClick={() => ed.remove(["cases"], i)} className="rounded p-0.5 hover:bg-bad-soft hover:text-bad" aria-label={`Remove case ${i + 1}`}>
                    <Trash2 className="size-3.5" />
                  </button>
                </div>
                <Field label="When (CEL)">
                  <textarea className={cx(mono, "h-14 resize-y")} value={str(c.when)} onChange={(e) => ed.set(["cases", i, "when"], e.target.value)} />
                </Field>
                <GotoSelect ids={ids} self={id} label="Go to" value={str(c.goto)} onChange={(v) => ed.set(["cases", i, "goto"], v)} />
              </div>
            ))}
            <button type="button" onClick={() => ed.append(["cases"], { when: "false", goto: others[0] ?? id })} className="inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline">
              <Plus className="size-3.5" /> Add case
            </button>
            <GotoSelect ids={ids} self={id} label="Otherwise go to" value={str(node.default)} onChange={(v) => ed.set(["default"], v)} />
          </Section>
        )}
        {type === "human_task" && (
          <>
            <Section title="Task">
              <Field label="Title" hint="${{ … }} allowed">
                <input className={input} value={str(node.title)} onChange={(e) => ed.set(["title"], e.target.value)} />
              </Field>
              <RefSelect label="Micro-app" value={str(node.app)} entries={catalog.apps} onChange={(v) => ed.set(["app"], v)} />
              <Field label="Assignee role">
                <input className={mono} value={str((node.assignee as Json)?.role)} onChange={(e) => ed.set(["assignee", "role"], val(e.target.value))} />
              </Field>
              <Field label="Outcomes" hint="comma separated">
                <input className={mono} value={((node.outputs as string[]) ?? []).join(", ")} onChange={(e) => ed.set(["outputs"], e.target.value.split(",").map((s) => s.trim()).filter(Boolean))} />
              </Field>
            </Section>
            <Section title="SLA">
              <div className="grid grid-cols-2 gap-2">
                <Field label="Due in">
                  <input className={mono} value={str(node.sla)} onChange={(e) => ed.set(["sla"], val(e.target.value))} placeholder="4h" />
                </Field>
                <Field label="Escalate after">
                  <input className={mono} value={str((node.escalate as Json)?.after)} onChange={(e) => ed.set(["escalate", "after"], val(e.target.value))} placeholder="4h" />
                </Field>
              </div>
              <Field label="Escalate to role">
                <input className={mono} value={str(((node.escalate as Json)?.to as Json)?.role)} onChange={(e) => ed.set(["escalate", "to", "role"], val(e.target.value))} />
              </Field>
            </Section>
            <Section title="Payload">
              <WithParams node={node} ed={ed} />
            </Section>
          </>
        )}
        {type === "wait" && (
          <Section title="Wait">
            <Field label="Duration">
              <input className={mono} value={str(node.duration)} onChange={(e) => ed.set(["duration"], val(e.target.value))} placeholder="1h" />
            </Field>
            <Field label="Event (signal)">
              <input className={mono} value={str(node.event)} onChange={(e) => ed.set(["event"], val(e.target.value))} />
            </Field>
          </Section>
        )}
        {type === "end" && (
          <Section title="End">
            <Field label="Run status">
              <select className={input} value={str(node.status) || "completed"} onChange={(e) => ed.set(["status"], e.target.value === "completed" ? undefined : e.target.value)}>
                <option>completed</option>
                <option>rejected</option>
                <option>cancelled</option>
              </select>
            </Field>
          </Section>
        )}
        {["agent", "action", "decide", "human_task", "wait"].includes(type) && (
          <Section title="Execution">
            <div className="grid grid-cols-2 gap-2">
              <Field label="Timeout">
                <input className={mono} value={str(node.timeout)} onChange={(e) => ed.set(["timeout"], val(e.target.value))} placeholder="60s" />
              </Field>
              {(type === "agent" || type === "action") && (
                <Field label="Max attempts">
                  <input type="number" min={1} max={10} className={mono} value={str((node.retry as Json)?.max_attempts)} onChange={(e) => ed.set(["retry", "max_attempts"], e.target.value ? Number(e.target.value) : undefined)} placeholder="3" />
                </Field>
              )}
            </div>
          </Section>
        )}
      </div>
      <footer className="border-t border-line p-3">
        <button type="button" onClick={ed.deleteNode} className="inline-flex w-full items-center justify-center gap-1.5 rounded-md border border-line px-3 py-1.5 text-xs font-medium text-bad hover:bg-bad-soft">
          <Trash2 className="size-3.5" /> Delete node <kbd className="ml-1 rounded border border-current/30 px-1 font-mono text-[10px]">Del</kbd>
        </button>
      </footer>
    </aside>
  );
}
