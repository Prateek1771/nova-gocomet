import { FileText } from "lucide-react";
import Link from "next/link";

import { AutoRefresh } from "@/components/auto-refresh";
import { Card, Empty, PageHeader, Stat, StatusBadge, timeAgo } from "@/components/ui";
import { UploadZone } from "@/features/documents/upload-zone";
import { apiGet } from "@/lib/api";
import type { DocumentOut } from "@/lib/types";

const MOVING = new Set(["pending", "running"]);

export default async function DocumentsPage() {
  const docs = await apiGet<DocumentOut[]>("/documents");
  const count = (s: string) => docs.filter((d) => d.run_status === s).length;
  const done = count("completed");
  // cancelled runs were withdrawn, not processed: they don't count for or against touchless
  const settled = docs.filter((d) => d.run_status && !MOVING.has(d.run_status) && d.run_status !== "cancelled").length;
  const attention = count("needs_attention") + count("failed");

  return (
    <div className="mx-auto max-w-6xl">
      <AutoRefresh active={docs.some((d) => d.run_status && MOVING.has(d.run_status))} />
      <PageHeader
        title="Documents"
        sub="Uploads are validated, deduplicated by content hash, stored per tenant and processed by their doc type's workflow."
      />
      <UploadZone />

      <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Documents" value={docs.length} />
        <Stat label="Touchless" value={settled ? `${Math.round((done / settled) * 100)}%` : "—"} hint={`${done} completed without a human`} tone="ok" />
        <Stat label="In review" value={count("waiting_human")} hint="waiting in the inbox" tone="warn" />
        <Stat label="Needs attention" value={attention} hint="a step failed after retries" tone={attention ? "bad" : undefined} />
      </div>

      <Card className="mt-5 overflow-hidden">
        {docs.length === 0 ? (
          <Empty icon={<FileText className="size-8" />} title="No documents yet">
            Drop the generated BoLs from <code className="font-mono text-xs">data/seed/bol</code> above.
          </Empty>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-line bg-canvas/60 text-left text-xs text-muted">
              <tr>
                <th className="px-4 py-2 font-medium">File</th>
                <th className="px-4 py-2 font-medium">Type</th>
                <th className="px-4 py-2 font-medium">Pages</th>
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2 font-medium">Uploaded</th>
                <th className="px-4 py-2 font-medium">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {docs.map((d) => (
                <tr key={d.id} className="hover:bg-canvas/60">
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-2">
                      <FileText className="size-4 shrink-0 text-muted" aria-hidden />
                      <span className="truncate font-medium">{d.filename ?? d.id}</span>
                    </div>
                    <div className="mt-0.5 font-mono text-[11px] text-muted" title={d.sha256}>
                      sha256 {d.sha256.slice(0, 12)}…
                    </div>
                  </td>
                  <td className="px-4 py-2.5 text-muted">{d.doc_type.replaceAll("_", " ")}</td>
                  <td className="num px-4 py-2.5 text-muted">{d.pages ?? "—"}</td>
                  <td className="px-4 py-2.5">
                    <StatusBadge status={d.run_status} />
                  </td>
                  <td className="px-4 py-2.5 text-muted">{timeAgo(d.uploaded_at)}</td>
                  <td className="px-4 py-2.5 text-right">
                    {d.run_id && (
                      <Link href={`/runs/${d.run_id}`} className="text-xs font-medium text-accent hover:underline">
                        View run →
                      </Link>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
