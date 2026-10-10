import { Gauge } from "lucide-react";

import { Badge, Card, cx } from "@/components/ui";
import type { Budget } from "@/lib/types";

const WARN_AT = 0.8; // 08 §6: alert at 80% of budget

/** Spend vs budget on the tenant's LiteLLM virtual key (ADR-028). At 100% model calls stop and steps
 * become needs_attention tasks. */
export function BudgetMeter({ budget }: { budget: Budget }) {
  const max = budget.max_budget;
  const ratio = max ? Math.min(budget.spend / max, 1) : 0;
  const tone = !budget.provisioned ? "bad" : ratio >= 1 ? "bad" : ratio >= WARN_AT ? "warn" : "ok";
  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <Gauge className="size-4 text-accent" aria-hidden />
          <h2 className="text-sm font-semibold">LLM budget</h2>
        </div>
        <Badge tone={tone}>
          {!budget.provisioned ? "Key not provisioned" : ratio >= 1 ? "Exhausted" : ratio >= WARN_AT ? "Above 80%" : "Healthy"}
        </Badge>
      </div>
      <div className="mt-4 flex items-baseline gap-1">
        <span className="num text-3xl font-semibold tracking-tight">${budget.spend.toFixed(4)}</span>
        <span className="text-sm text-muted">of {max == null ? "no limit" : `$${max.toFixed(2)}`}</span>
      </div>
      <div
        className="mt-3 h-2 overflow-hidden rounded-full bg-canvas"
        role="meter"
        aria-label="LLM spend"
        aria-valuemin={0}
        aria-valuemax={max ?? 0}
        aria-valuenow={budget.spend}
      >
        <div
          className={cx("h-full rounded-full transition-all", tone === "ok" && "bg-ok", tone === "warn" && "bg-warn", tone === "bad" && "bg-bad")}
          style={{ width: `${Math.max(ratio * 100, budget.spend > 0 ? 2 : 0)}%` }}
        />
      </div>
      <p className="mt-3 text-xs text-muted">
        Per-tenant key on the model gateway; resets every {budget.period}
        {budget.resets_at ? ` (next ${new Date(budget.resets_at).toLocaleDateString()})` : ""}. When it runs out, model steps stop and
        land in the inbox as <b className="text-ink">needs attention</b>. Change the budget in the configuration.
      </p>
    </Card>
  );
}
