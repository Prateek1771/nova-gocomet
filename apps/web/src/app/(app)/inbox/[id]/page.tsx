import { ArrowLeft, ExternalLink } from "lucide-react";
import Link from "next/link";

import { StatusBadge } from "@/components/ui";
import { MicroAppIsland } from "@/features/micro-apps/island";
import { ApiError, apiGet, apiGetOrNotFound, getMe } from "@/lib/api";
import type { MicroApp, TaskOut } from "@/lib/types";

/** Apps without a definition (engine step failures, generic approvals) get a payload + decision view. */
function fallbackApp(task: TaskOut): MicroApp {
  const options = task.app_key === "step_failure" ? ["retry", "abort"] : ["approved", "rejected"];
  return {
    key: task.app_key,
    schemas: {},
    layout: { type: "stack", children: [{ type: "PayloadView" }, { type: "DecisionBar", options }] },
  };
}

export default async function TaskPage(props: PageProps<"/inbox/[id]">) {
  const { id } = await props.params;
  const [task, me] = await Promise.all([apiGetOrNotFound<TaskOut>(`/tasks/${id}`), getMe()]);
  const app = await apiGet<MicroApp>(`/apps/${task.app_key}`).catch((e) => {
    if (e instanceof ApiError && e.status === 404) return fallbackApp(task);
    throw e;
  });

  return (
    <div className="-m-4 flex h-[calc(100vh-3.5rem)] flex-col md:-m-6">
      <div className="flex items-center gap-3 border-b border-line bg-panel px-4 py-2.5">
        <Link href="/inbox" className="rounded p-1 text-muted hover:bg-canvas hover:text-ink" aria-label="Back to inbox">
          <ArrowLeft className="size-4" />
        </Link>
        <h1 className="truncate text-sm font-semibold">{task.title}</h1>
        <StatusBadge status={task.status} />
        <span className="text-xs text-muted">{app.title ?? task.app_key}</span>
        <Link href={`/runs/${task.run_id}`} className="ml-auto inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline">
          Run timeline <ExternalLink className="size-3" />
        </Link>
      </div>
      <div className="min-h-0 flex-1 bg-panel">
        <MicroAppIsland task={task} app={app} me={me.sub} />
      </div>
    </div>
  );
}
