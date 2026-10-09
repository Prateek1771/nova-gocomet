"use client";

import { Loader2, Plus } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ClientError, postJson } from "@/lib/client";

const template = (key: string, title: string) => `apiVersion: nova/v1
kind: Workflow
metadata:
  key: ${key}
  title: ${title}
trigger: {type: manual}
inputs: {}
nodes:
  - id: review
    type: human_task
    title: "Review"
    assignee: {role: ops_exec}
    app: generic_review
    sla: 4h
    outputs: [approved, rejected]
  - id: done
    type: end
  - id: declined
    type: end
    status: rejected
edges:
  - {from: review, to: done, on: approved}
  - {from: review, to: declined, on: rejected}
`;

/** Creates a draft from a minimal valid template, then opens it in the Studio. */
export function NewWorkflow() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = title.toLowerCase().trim().replace(/[^a-z0-9]+/g, "_").replace(/^[^a-z]+|_+$/g, "").slice(0, 64);

  if (!open)
    return (
      <button type="button" onClick={() => setOpen(true)} className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-on-accent hover:opacity-90">
        <Plus className="size-4" /> New workflow
      </button>
    );
  return (
    <form
      className="flex flex-wrap items-center gap-2"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!key) return;
        setBusy(true);
        setError(null);
        try {
          await postJson("/workflows", { key, yaml: template(key, title.trim()) });
          router.push(`/studio/${key}`);
        } catch (err) {
          setError(err instanceof ClientError ? err.message : "Could not create it");
          setBusy(false);
        }
      }}
    >
      <input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Workflow name, e.g. Invoice approval" aria-label="Workflow name" className="w-72 rounded-md border border-line bg-panel px-2.5 py-1.5 text-sm outline-none focus:border-accent focus:ring-2 focus:ring-accent/20" />
      {key && <code className="font-mono text-xs text-muted">{key}</code>}
      <button type="submit" disabled={!key || busy} className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-on-accent disabled:opacity-50">
        {busy && <Loader2 className="size-4 animate-spin" />} Create
      </button>
      <button type="button" onClick={() => setOpen(false)} className="rounded-md px-2 py-1.5 text-sm text-muted hover:text-ink">
        Cancel
      </button>
      {error && <p role="alert" className="w-full text-xs text-bad">{error}</p>}
    </form>
  );
}
