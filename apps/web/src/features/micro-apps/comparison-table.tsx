"use client";

import { ChevronDown, FileSignature, Scale, Timer } from "lucide-react";
import { useState } from "react";

import { Badge, cx } from "@/components/ui";

import { useTaskApp } from "./context";

type Match = {
  index: number;
  charge_code: string;
  description: string | null;
  qty: number;
  po_qty: number | null;
  unit_rate: number;
  unit_rate_usd: number;
  contract_rate: number | null;
  amount_usd: number;
  expected_usd: number;
  variance_pct: number;
  clause_id: string | null;
  status: string;
};
type Clause = { clause_id: string; charge_code: string; title: string; text: string; rate: number; unit: string };
type Accessorial = {
  index: number;
  charge_code: string;
  charged_days: number;
  free_time_days: number;
  dwell_days: number | null;
  chargeable_days: number | null;
};

const STATUS: Record<string, [string, "ok" | "bad" | "warn" | "neutral"]> = {
  ok: ["matches", "ok"],
  over: ["over contract", "bad"],
  unsupported: ["not supported", "bad"],
  not_on_po: ["not on PO", "warn"],
  qty: ["qty differs", "warn"],
  no_clause: ["no clause", "warn"],
};

const usd = (n: number | null | undefined) =>
  n == null ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });

/** W2 (FR-3.2): each invoice line against its PO line and the contract clause that prices it. Rates in
 * USD (FX applied); a row links to its evidence on the PDF, a clause chip expands to the contract text. */
export function ComparisonTable({
  matches = [],
  clauses = [],
  accessorials = [],
  fields = {},
  variance_pct = 0,
  amount,
  level,
}: {
  matches?: Match[];
  clauses?: Clause[];
  accessorials?: Accessorial[];
  fields?: { currency?: string; invoice_no?: string; po_number?: string };
  variance_pct?: number;
  amount?: number;
  level?: string;
}) {
  const { focus, setFocus } = useTaskApp();
  const [open, setOpen] = useState<string | null>(null);
  const byId = Object.fromEntries(clauses.map((c) => [c.clause_id, c]));
  const vTone = variance_pct > 5 ? "bad" : variance_pct > 1 ? "warn" : "ok";
  const fx = fields.currency && fields.currency !== "USD";

  return (
    <section aria-labelledby="cmp-h" className="border-b border-line p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Scale className="size-4 text-accent" aria-hidden />
        <h2 id="cmp-h" className="text-sm font-semibold">
          Invoice vs PO vs contract
        </h2>
        {level && <Badge tone="accent">{level}</Badge>}
        <span className="ml-auto flex items-center gap-2">
          <span className="num text-sm font-semibold">{usd(amount)}</span>
          <Badge tone={vTone}>
            {variance_pct > 0 ? "+" : ""}
            {variance_pct.toFixed(2)}% vs contract
          </Badge>
        </span>
      </div>
      <p className="mt-1 text-xs text-muted">
        PO <span className="font-mono text-ink">{fields.po_number ?? "—"}</span>
        {fx && (
          <>
            {" "}
            · billed in <b className="text-bad">{fields.currency}</b>, shown in USD at the tenant FX rate
          </>
        )}
      </p>

      <div className="mt-3 overflow-x-auto rounded-lg border border-line">
        <table className="w-full text-sm">
          <thead className="bg-canvas text-left text-[11px] uppercase tracking-wide text-muted">
            <tr>
              <th className="px-3 py-2 font-medium">Line</th>
              <th className="px-3 py-2 text-right font-medium">Qty / PO</th>
              <th className="px-3 py-2 text-right font-medium">Rate (USD)</th>
              <th className="px-3 py-2 text-right font-medium">Contract</th>
              <th className="px-3 py-2 text-right font-medium">Amount</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {matches.map((m) => {
              const field = `lines[${m.index}].amount`;
              const [label, tone] = STATUS[m.status] ?? [m.status, "neutral"];
              const bad = tone === "bad";
              return (
                <tr
                  key={m.index}
                  onClick={() => setFocus(field)}
                  className={cx("cursor-pointer transition-colors", bad ? "bg-bad-soft/60" : "hover:bg-canvas", focus === field && "outline outline-2 -outline-offset-2 outline-accent")}
                >
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-xs font-semibold">{m.charge_code}</span>
                      <Badge tone={tone}>{label}</Badge>
                    </div>
                    <div className="max-w-56 truncate text-xs text-muted" title={m.description ?? ""}>
                      {m.description}
                    </div>
                    <div className="mt-1">
                    {m.clause_id ? (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpen(open === m.clause_id ? null : m.clause_id);
                        }}
                        aria-expanded={open === m.clause_id}
                        className="inline-flex items-center gap-1 rounded-md border border-line bg-panel px-1.5 py-0.5 font-mono text-[11px] hover:border-accent"
                      >
                        <FileSignature className="size-3 text-accent" aria-hidden />
                        {m.clause_id}
                        <ChevronDown className={cx("size-3 transition-transform", open === m.clause_id && "rotate-180")} aria-hidden />
                      </button>
                    ) : null}
                    </div>
                  </td>
                  <td className={cx("num px-3 py-2 text-right", m.po_qty != null && m.po_qty !== m.qty && "font-semibold text-warn")}>
                    {m.qty}
                    <span className="text-muted"> / {m.po_qty ?? "—"}</span>
                  </td>
                  <td className={cx("num px-3 py-2 text-right", bad && m.status === "over" && "font-semibold text-bad")}>{usd(m.unit_rate_usd)}</td>
                  <td className="num px-3 py-2 text-right text-muted">{usd(m.contract_rate)}</td>
                  <td className="num px-3 py-2 text-right">
                    {usd(m.amount_usd)}
                    {Math.abs(m.amount_usd - m.expected_usd) > 0.01 && <div className="text-[11px] text-muted">exp. {usd(m.expected_usd)}</div>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {open && byId[open] && (
        <blockquote className="mt-3 rounded-lg border-l-4 border-accent bg-accent-soft/40 p-3 text-sm">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="font-mono font-semibold">{byId[open].clause_id}</span>
            <span className="font-medium">{byId[open].title}</span>
            <span className="ml-auto text-muted">
              {usd(byId[open].rate)} {byId[open].unit}
            </span>
          </div>
          <p className="mt-1.5 leading-relaxed">{byId[open].text}</p>
        </blockquote>
      )}

      {accessorials.length > 0 && (
        <div className="mt-3 space-y-2">
          {accessorials.map((a) => {
            const ok = a.chargeable_days != null && a.charged_days <= a.chargeable_days;
            const total = Math.max(a.dwell_days ?? 0, a.free_time_days, 1);
            return (
              <div key={a.index} className={cx("rounded-lg border p-3 text-sm", ok ? "border-line" : "border-bad/40 bg-bad-soft/50")}>
                <div className="flex items-center gap-2">
                  <Timer className={cx("size-4", ok ? "text-ok" : "text-bad")} aria-hidden />
                  <span className="font-medium">{a.charge_code === "DET" ? "Detention" : a.charge_code} vs container events</span>
                  <Badge tone={ok ? "ok" : "bad"} className="ml-auto">
                    {ok ? "supported" : "not supported"}
                  </Badge>
                </div>
                <div className="mt-2 flex h-2 overflow-hidden rounded-full bg-canvas" aria-hidden>
                  <div className="bg-ok/70" style={{ width: `${(Math.min(a.free_time_days, total) / total) * 100}%` }} />
                  <div className="bg-warn/70" style={{ width: `${(Math.max((a.dwell_days ?? 0) - a.free_time_days, 0) / total) * 100}%` }} />
                </div>
                <p className="mt-1.5 text-xs text-muted">
                  Out <b className="text-ink">{a.dwell_days ?? "?"}</b> days (gate-out → gate-in) · <b className="text-ink">{a.free_time_days}</b> free →{" "}
                  <b className="text-ink">{a.chargeable_days ?? "?"}</b> chargeable · carrier charged <b className={ok ? "text-ink" : "text-bad"}>{a.charged_days}</b>
                </p>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
