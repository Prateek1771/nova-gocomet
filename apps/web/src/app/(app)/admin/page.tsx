import { ShieldCheck, ShieldX } from "lucide-react";

import { Badge, Card, Empty, PageHeader, timeAgo } from "@/components/ui";
import { BudgetMeter } from "@/features/admin/budget-meter";
import { ConfigEditor } from "@/features/admin/config-editor";
import { apiGet, getMe } from "@/lib/api";
import type { AuditEntry, AuditVerification, Budget, TenantConfigOut } from "@/lib/types";

/** Tenant governance (M4): LLM budget, approval limits / thresholds (TenantConfig), tamper-evident audit. */
export default async function AdminPage() {
  const me = await getMe();
  const can = (c: string) => me.capabilities?.includes(c);
  if (!can("can_edit_config") && !can("can_manage_budgets") && !can("can_read_audit")) {
    return (
      <Card className="mx-auto max-w-lg">
        <Empty title="Admin is for tenant admins and auditors">Your role can&apos;t change budgets, limits or read the audit log.</Empty>
      </Card>
    );
  }
  const [budget, config, audit, verify] = await Promise.all([
    can("can_manage_budgets") ? apiGet<Budget>("/admin/llm-budget").catch(() => null) : null,
    apiGet<TenantConfigOut>("/tenant-config"),
    can("can_read_audit") ? apiGet<AuditEntry[]>("/audit?limit=40") : null,
    can("can_read_audit") ? apiGet<AuditVerification>("/audit/verify") : null,
  ]);

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <PageHeader title="Admin" sub={`${me.tenant?.name} · governance: what the tenant may spend, who may approve how much, and proof of every action.`} />

      <div className="grid gap-5 lg:grid-cols-[1fr_1.4fr]">
        {budget && <BudgetMeter budget={budget} />}
        <ConfigEditor initial={config} editable={!!can("can_edit_config")} />
      </div>

      {audit && verify && (
        <Card className="overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
            <div>
              <h2 className="text-sm font-semibold">Audit log</h2>
              <p className="text-xs text-muted">Every row is hash-chained to the one before it; editing any row breaks the chain from there on.</p>
            </div>
            {verify.valid ? (
              <Badge tone="ok" className="px-2 py-1 text-xs">
                <ShieldCheck className="size-3.5" /> Chain verified · {verify.entries} entries
              </Badge>
            ) : (
              <Badge tone="bad" className="px-2 py-1 text-xs">
                <ShieldX className="size-3.5" /> Broken at #{verify.broken[0]?.seq}: {verify.broken[0]?.reason}
              </Badge>
            )}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-canvas text-left text-xs text-muted">
                <tr>
                  <th className="px-4 py-2 font-medium">#</th>
                  <th className="px-4 py-2 font-medium">When</th>
                  <th className="px-4 py-2 font-medium">Actor</th>
                  <th className="px-4 py-2 font-medium">Action</th>
                  <th className="px-4 py-2 font-medium">Subject</th>
                  <th className="px-4 py-2 font-medium">Def / config</th>
                  <th className="px-4 py-2 font-medium">Hash</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {audit.map((a) => {
                  const broken = verify.broken.some((b) => b.seq === a.seq);
                  return (
                    <tr key={a.seq} className={broken ? "bg-bad-soft" : "hover:bg-canvas"}>
                      <td className="num px-4 py-2 text-muted">{a.seq}</td>
                      <td className="px-4 py-2 whitespace-nowrap text-muted" title={a.at}>
                        {timeAgo(a.at)}
                      </td>
                      <td className="px-4 py-2">
                        <Badge tone={a.actor_type === "user" ? "accent" : "neutral"}>{a.actor_type}</Badge>{" "}
                        <span className="font-mono text-[11px] text-muted">{a.actor_id.slice(0, 8)}</span>
                      </td>
                      <td className="px-4 py-2 font-medium">{a.action}</td>
                      <td className="max-w-56 truncate px-4 py-2 font-mono text-[11px] text-muted" title={a.subject}>
                        {a.subject}
                      </td>
                      <td className="num px-4 py-2 text-xs text-muted">
                        {a.definition_version ? `v${a.definition_version}` : "—"} / {a.config_version ? `c${a.config_version}` : "—"}
                      </td>
                      <td className="px-4 py-2 font-mono text-[11px] text-muted" title={a.hash}>
                        {a.hash.slice(0, 10)}…
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
