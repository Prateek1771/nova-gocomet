"use client";

import { CircleCheck, Sparkles, TriangleAlert } from "lucide-react";

import { Badge, cx, SEVERITY_TONE } from "@/components/ui";
import type { Issue } from "@/lib/types";

import { useTaskApp } from "./context";

/** Why this task exists: each check failure with its field, plus the decide node's verdict. */
export function IssueList({ issues = [], why }: { issues?: Issue[]; why?: Record<string, string> }) {
  const { focus, setFocus, task } = useTaskApp();
  const conf = (task.payload as { confidence?: Record<string, number> }).confidence ?? {};
  const low = Object.entries(conf).filter(([, v]) => v < 0.85);

  return (
    <section aria-labelledby="issues-h" className="border-b border-line p-4">
      <h2 id="issues-h" className="flex items-center gap-2 text-sm font-semibold">
        <TriangleAlert className="size-4 text-warn" aria-hidden />
        Why this needs review
      </h2>
      <ul className="mt-3 space-y-2">
        {issues.map((iss, i) => (
          <li key={i}>
            <button
              type="button"
              onClick={() => setFocus(iss.field)}
              className={cx(
                "w-full rounded-lg border p-3 text-left transition-colors focus-visible:outline-2 focus-visible:outline-accent",
                focus === iss.field ? "border-accent bg-accent-soft/50" : "border-line hover:bg-canvas",
              )}
            >
              <div className="flex items-center gap-2">
                <Badge tone={SEVERITY_TONE[iss.severity] ?? "neutral"}>{iss.severity}</Badge>
                <span className="font-mono text-xs font-semibold">{iss.code}</span>
                <span className="ml-auto font-mono text-[11px] text-muted">{iss.field}</span>
              </div>
              <p className="mt-1.5 text-sm">{iss.message}</p>
              {iss.evidence[0] && (
                <p className="mt-1 text-xs text-muted">
                  on page {iss.evidence[0].page}: <span className="font-mono text-ink">“{iss.evidence[0].text}”</span>
                </p>
              )}
            </button>
          </li>
        ))}
        {issues.length === 0 && low.length > 0 && (
          <li className="rounded-lg border border-warn/40 bg-warn-soft p-3 text-sm">
            <span className="font-semibold text-warn">Low extraction confidence.</span> No check failed, but{" "}
            {low.length} field{low.length > 1 ? "s" : ""} couldn&apos;t be verified against the page text (min {Math.round(Math.min(...low.map(([, v]) => v)) * 100)}%). Compare
            them with the document before approving.
          </li>
        )}
        {issues.length === 0 && low.length === 0 && (
          <li className="flex items-center gap-2 text-sm text-muted">
            <CircleCheck className="size-4 text-ok" /> No issues recorded.
          </li>
        )}
      </ul>
      {why && Object.keys(why).length > 0 && (
        <p className="mt-3 flex items-start gap-1.5 text-xs text-muted">
          <Sparkles className="mt-0.5 size-3.5 shrink-0 text-accent" aria-hidden />
          <span>
            <span className="font-medium text-ink">Decide:</span> {Object.values(why).join(" · ")}
          </span>
        </p>
      )}
    </section>
  );
}
