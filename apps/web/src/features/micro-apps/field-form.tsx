"use client";

import { Pencil } from "lucide-react";

import { cx } from "@/components/ui";
import type { Issue, JsonSchema } from "@/lib/types";

import { baseField, useTaskApp } from "./context";

const LABELS: Record<string, string> = {
  bol_number: "B/L number",
  carrier_scac: "Carrier SCAC",
  pol: "Port of loading",
  pod: "Port of discharge",
  hs_codes: "HS codes",
  gross_weight_kg: "Gross weight (kg)",
};
const label = (k: string) => LABELS[k] ?? k.replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase());
const isType = (s: JsonSchema, t: string) => (Array.isArray(s.type) ? s.type.includes(t) : s.type === t);

function ConfChip({ v }: { v: number | undefined }) {
  if (v === undefined) return null;
  const pct = Math.round(v * 100);
  return (
    <span
      title="match between the extracted value and the page text"
      className={cx("num rounded px-1 text-[10px] font-semibold", v >= 0.95 ? "text-ok" : v >= 0.85 ? "text-muted" : "bg-warn-soft text-warn")}
    >
      {pct}%
    </span>
  );
}

/** Extracted fields, generated from the extraction schema (FR-3.2 FieldForm). Edits are tracked and
 * sent with the approval; focusing a field highlights its evidence on the page. */
export function FieldForm({ schema: key, confidence = {} }: { schema?: string; confidence?: Record<string, number> }) {
  const { app, fields, setField, edited, focus, setFocus, task } = useTaskApp();
  const schema = key ? app.schemas[key] : undefined;
  const issues = ((task.payload as { issues?: Issue[] }).issues ?? []) as Issue[];
  const flagged = new Set(issues.map((i) => baseField(i.field)));
  if (!schema?.properties) return null;

  const input = "w-full rounded-md border bg-panel px-2 py-1.5 text-sm outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent/20";

  return (
    <section aria-labelledby="fields-h" className="p-4">
      <h2 id="fields-h" className="flex items-center gap-2 text-sm font-semibold">
        <Pencil className="size-4 text-muted" aria-hidden />
        Extracted fields
        {edited.size > 0 && <span className="rounded bg-accent-soft px-1.5 text-[11px] font-medium text-accent">{edited.size} edited</span>}
      </h2>
      <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2.5">
        {Object.entries(schema.properties).map(([k, p]) => {
          const v = fields[k];
          const wide = ["shipper", "consignee", "notify_party", "description_of_goods", "cargo_lines"].includes(k);
          const ring = flagged.has(k) ? "border-bad ring-1 ring-bad/30" : focus === k ? "border-accent" : "border-line";
          const id = `f-${k}`;
          let control;
          if (k === "cargo_lines" && Array.isArray(v)) {
            const rows = v as Record<string, unknown>[];
            control = (
              <table className="w-full text-xs">
                <thead className="text-left text-muted">
                  <tr>
                    <th className="py-1 font-medium">Description</th>
                    <th className="py-1 font-medium">HS</th>
                    <th className="py-1 text-right font-medium">Pkgs</th>
                    <th className="py-1 text-right font-medium">Weight kg</th>
                  </tr>
                </thead>
                <tbody className="num">
                  {rows.map((r, i) => (
                    <tr key={i} className="cursor-pointer border-t border-line hover:bg-canvas" onClick={() => setFocus(`cargo_lines[${i}].weight_kg`)}>
                      <td className="py-1 pr-2">{String(r.description ?? "")}</td>
                      <td className="py-1 pr-2 font-mono">{String(r.hs_code ?? "")}</td>
                      <td className="py-1 text-right">{String(r.packages ?? "")}</td>
                      <td className="py-1 text-right">{typeof r.weight_kg === "number" ? r.weight_kg.toLocaleString() : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            );
          } else if (isType(p, "array")) {
            control = (
              <input
                id={id}
                className={cx(input, ring, "font-mono")}
                value={Array.isArray(v) ? (v as unknown[]).join(", ") : ""}
                onFocus={() => setFocus(k)}
                onChange={(e) => setField(k, e.target.value.split(",").map((s) => s.trim()).filter(Boolean))}
              />
            );
          } else if (p.enum) {
            control = (
              <select id={id} className={cx(input, ring)} value={v == null ? "" : String(v)} onFocus={() => setFocus(k)} onChange={(e) => setField(k, e.target.value || null)}>
                {p.enum.map((o) => (
                  <option key={String(o)} value={o == null ? "" : String(o)}>
                    {o == null ? "—" : String(o)}
                  </option>
                ))}
              </select>
            );
          } else {
            const numeric = isType(p, "number") || isType(p, "integer");
            control = (
              <input
                id={id}
                className={cx(input, ring, numeric && "num text-right", (k.endsWith("_number") || k === "pol" || k === "pod") && "font-mono")}
                inputMode={numeric ? "decimal" : undefined}
                value={v == null ? "" : String(v)}
                onFocus={() => setFocus(k)}
                onChange={(e) => setField(k, numeric ? (e.target.value === "" ? null : Number(e.target.value)) : e.target.value)}
              />
            );
          }
          return (
            <div key={k} className={cx(wide && "col-span-2")}>
              <label htmlFor={id} className="mb-1 flex items-center gap-1.5 text-xs font-medium text-muted">
                {label(k)}
                <ConfChip v={confidence[k]} />
                {edited.has(k) && <span className="text-[10px] font-semibold text-accent">edited</span>}
              </label>
              {control}
            </div>
          );
        })}
      </div>
    </section>
  );
}
