import { AutoRefresh } from "@/components/auto-refresh";
import { PageHeader } from "@/components/ui";
import { ExceptionsView } from "@/features/exceptions/exceptions-view";
import { apiGet, getMe } from "@/lib/api";
import type { ExceptionOut, LagOut, MetricOut, NotificationOut, TenantConfigOut } from "@/lib/types";

/** W3 (docs/04): shipment exceptions, their triage, customer notices and pipeline health. */
export default async function ExceptionsPage() {
  const soft = <T,>(p: Promise<T>) => p.catch(() => null); // analytics are best effort: the data profile may be down
  const me = await getMe();
  const analytics = !!me.capabilities?.includes("can_view_analytics"); // OpenFGA decides; the UI only explains
  const none = Promise.resolve(null);
  const [exceptions, slips, touchless, cost, notifications, lag, config] = await Promise.all([
    apiGet<ExceptionOut[]>("/exceptions?limit=200"),
    analytics ? soft(apiGet<MetricOut>("/analytics/metrics/eta_slip_hours?limit=20")) : none,
    analytics ? soft(apiGet<MetricOut>("/analytics/metrics/touchless_rate?limit=30")) : none,
    analytics ? soft(apiGet<MetricOut>("/analytics/metrics/cost_per_run?limit=30")) : none,
    apiGet<NotificationOut[]>("/notifications?limit=50"),
    analytics ? soft(apiGet<LagOut[]>("/ops/kafka-lag")) : none,
    soft(apiGet<TenantConfigOut>("/tenant-config")),
  ]);
  const rules = ((config?.config.exceptions as { rules?: { type: string; threshold: number }[] } | undefined)?.rules ?? []);
  const slipThreshold = rules.find((r) => r.type === "ETA_SLIP")?.threshold ?? 24;
  return (
    <div className="mx-auto max-w-7xl">
      <AutoRefresh active ms={5000} />
      <PageHeader
        title="Exceptions"
        sub="Shipment monitoring: governed metrics on the live event stream, agent triage with cited SOPs, and the ops lead's decision."
      />
      <ExceptionsView
        exceptions={exceptions}
        slips={slips}
        touchless={touchless}
        cost={cost}
        notifications={notifications}
        lag={lag}
        slipThreshold={slipThreshold}
        analytics={analytics}
      />
    </div>
  );
}
