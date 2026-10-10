"use client";

import { BookOpen, ChevronDown, ListChecks, Mail } from "lucide-react";
import { useState } from "react";

import { Badge, cx } from "@/components/ui";

type Rec = { action: string; why: string; sop_ref: string; chunk_id: string; sop_title?: string; section?: string; text?: string };

/** W3: the recommender's next steps, each citing the SOP section it came from (retrieved, never invented). */
export function Recommendations({ recommendations = [], customer_message }: { recommendations?: Rec[]; customer_message?: string }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <section aria-labelledby="recs-h" className="border-b border-line p-4">
      <h2 id="recs-h" className="flex items-center gap-2 text-sm font-semibold">
        <ListChecks className="size-4 text-accent" aria-hidden /> Recommended next steps
      </h2>
      <ol className="mt-3 space-y-2">
        {recommendations.map((r, i) => (
          <li key={r.chunk_id + i} className="rounded-lg border border-line p-3">
            <div className="flex items-start gap-2">
              <span className="num mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-accent-soft text-[11px] font-semibold text-accent">{i + 1}</span>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{r.action}</p>
                {r.why && <p className="mt-0.5 text-xs text-muted">{r.why}</p>}
                <button
                  type="button"
                  onClick={() => setOpen(open === r.chunk_id ? null : r.chunk_id)}
                  aria-expanded={open === r.chunk_id}
                  className="mt-2 inline-flex items-center gap-1 rounded-md focus-visible:outline-2 focus-visible:outline-accent"
                >
                  <Badge tone="accent">
                    <BookOpen className="size-3" aria-hidden /> {r.sop_ref}
                    {r.section ? ` · ${r.section}` : ""}
                  </Badge>
                  <ChevronDown className={cx("size-3.5 text-muted transition-transform", open === r.chunk_id && "rotate-180")} aria-hidden />
                </button>
                {open === r.chunk_id && (
                  <blockquote className="mt-2 border-l-2 border-accent/40 pl-3 text-xs whitespace-pre-line text-muted">
                    <span className="font-medium text-ink">{r.sop_title}</span>
                    {"\n"}
                    {r.text}
                  </blockquote>
                )}
              </div>
            </div>
          </li>
        ))}
        {recommendations.length === 0 && <li className="text-sm text-muted">No recommendation.</li>}
      </ol>
      {customer_message && (
        <div className="mt-3 rounded-lg border border-info/30 bg-info-soft/40 p-3">
          <p className="flex items-center gap-1.5 text-xs font-semibold text-info">
            <Mail className="size-3.5" aria-hidden /> Draft to the customer
            <span className="font-normal text-muted">sent on Accept; Override replaces it</span>
          </p>
          <p className="mt-1.5 text-sm">{customer_message}</p>
        </div>
      )}
    </section>
  );
}
