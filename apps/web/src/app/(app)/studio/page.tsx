import { ArrowRight, Workflow } from "lucide-react";
import Link from "next/link";

import { Badge, Card, Empty, PageHeader } from "@/components/ui";
import { NewWorkflow } from "@/features/studio/new-workflow";
import { apiGet } from "@/lib/api";
import type { WorkflowSummary } from "@/lib/types";

export default async function StudioList() {
  const wfs = await apiGet<WorkflowSummary[]>("/workflows");
  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Studio" sub="Model a process visually or in YAML, validate it, publish an immutable version." actions={<NewWorkflow />} />
      <Card className="overflow-hidden">
        {wfs.length === 0 ? (
          <Empty icon={<Workflow className="size-6" />} title="No workflows yet">
            Create one to start modelling.
          </Empty>
        ) : (
          <ul className="divide-y divide-line">
            {wfs.map((w) => (
              <li key={w.key}>
                <Link href={`/studio/${w.key}`} className="flex items-center gap-3 px-4 py-3 hover:bg-canvas/60">
                  <span className="grid size-9 place-items-center rounded-lg bg-accent-soft text-accent">
                    <Workflow className="size-4" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">{w.title ?? w.key}</span>
                    <span className="font-mono text-xs text-muted">{w.key}</span>
                  </span>
                  {w.latest_version ? <Badge tone="ok">v{w.latest_version} live</Badge> : <Badge>unpublished</Badge>}
                  {w.has_draft && <Badge tone="warn">draft</Badge>}
                  <ArrowRight className="size-4 text-muted" />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
