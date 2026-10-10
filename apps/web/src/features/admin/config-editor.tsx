"use client";

import { Infinity as InfinityIcon, Loader2, Save, SlidersHorizontal } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Badge, Card, cx, timeAgo } from "@/components/ui";
import { api, ClientError } from "@/lib/client";
import type { TenantConfigOut } from "@/lib/types";

const APPROVERS = [
  ["ops_lead", "Ops lead"],
  ["finance", "Finance"],
  ["controller", "Controller"],
] as const;
const NUMBERS = [
  ["llm_budget_usd", "LLM budget (USD / 30 days)", 0.5],
  ["auto_approve_below", "Auto-approve below", 100],
  ["bol_min_confidence", "BoL min. extraction confidence", 0.01],
] as const;

type Limits = Record<string, number | null>;

/** TenantConfig editor: every save is a new immutable version (runs in flight keep the version they
 * pinned). Approval limits feed OpenFGA `within_limit`; the budget re-provisions the tenant's LLM key. */
export function ConfigEditor({ initial, editable }: { initial: TenantConfigOut; editable: boolean }) {
  const router = useRouter();
  const [cfg, setCfg] = useState<Record<string, unknown>>(initial.config);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const limits = (cfg.approval_limits ?? {}) as Limits;
  const dirty = JSON.stringify(cfg) !== JSON.stringify(initial.config);

  const setLimit = (role: string, v: number | null) => setCfg((c) => ({ ...c, approval_limits: { ...limits, [role]: v } }));
  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await api("/tenant-config", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(cfg) });
      router.refresh();
    } catch (e) {
      setError(e instanceof ClientError ? e.message : "save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <SlidersHorizontal className="size-4 text-accent" aria-hidden />
          <h2 className="text-sm font-semibold">Tenant configuration</h2>
          <Badge tone="accent">v{initial.version}</Badge>
          <span className="text-xs text-muted">published {timeAgo(initial.published_at)}</span>
        </div>
        {editable ? (
          <button
            type="button"
            disabled={!dirty || saving}
            onClick={() => void save()}
            className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-on-accent hover:opacity-90 disabled:opacity-40"
          >
            {saving ? <Loader2 className="size-3.5 animate-spin" /> : <Save className="size-3.5" />}
            Publish v{initial.version + 1}
          </button>
        ) : (
          <Badge>read-only</Badge>
        )}
      </div>

      <fieldset disabled={!editable} className="mt-4">
        <legend className="text-xs font-medium text-muted">Approval limits per role (USD)</legend>
        <div className="mt-2 grid gap-2 sm:grid-cols-3">
          {APPROVERS.map(([role, label]) => {
            const v = limits[role];
            const unlimited = v === null || v === undefined;
            return (
              <label key={role} className="block rounded-lg border border-line p-2.5">
                <span className="flex items-center justify-between text-xs font-medium">
                  {label}
                  <button
                    type="button"
                    onClick={() => setLimit(role, unlimited ? 10000 : null)}
                    aria-pressed={unlimited}
                    title="No upper limit"
                    className={cx("rounded p-0.5", unlimited ? "bg-accent-soft text-accent" : "text-muted hover:text-ink")}
                  >
                    <InfinityIcon className="size-3.5" />
                    <span className="sr-only">Unlimited</span>
                  </button>
                </span>
                <input
                  type="number"
                  min={0}
                  step={1000}
                  value={unlimited ? "" : v}
                  placeholder="unlimited"
                  onChange={(e) => setLimit(role, e.target.value === "" ? null : Number(e.target.value))}
                  aria-label={`${label} approval limit`}
                  className="num mt-1 w-full rounded-md border border-line bg-panel px-2 py-1 text-right text-sm outline-none focus:border-accent focus:ring-2 focus:ring-accent/20"
                />
              </label>
            );
          })}
        </div>

        <div className="mt-4 grid gap-2 sm:grid-cols-3">
          {NUMBERS.map(([key, label, step]) => (
            <label key={key} className="block">
              <span className="text-xs font-medium text-muted">{label}</span>
              <input
                type="number"
                min={0}
                step={step}
                value={typeof cfg[key] === "number" ? (cfg[key] as number) : ""}
                onChange={(e) => setCfg((c) => ({ ...c, [key]: e.target.value === "" ? undefined : Number(e.target.value) }))}
                className="num mt-1 w-full rounded-md border border-line bg-panel px-2 py-1 text-right text-sm outline-none focus:border-accent focus:ring-2 focus:ring-accent/20"
              />
            </label>
          ))}
        </div>
      </fieldset>

      {error && (
        <p role="alert" className="mt-3 rounded-md bg-bad-soft px-2 py-1.5 text-xs text-bad">
          {error}
        </p>
      )}
      <p className="mt-3 text-xs text-muted">
        New runs use the new version; runs in flight keep the one they started with. An approver can act up to their role&apos;s limit,
        and roles with a higher limit can step in.
      </p>
    </Card>
  );
}
