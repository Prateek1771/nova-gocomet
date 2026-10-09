"use client";

import { Ban, Check, Loader2, RotateCcw, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import { cx, Kbd } from "@/components/ui";
import { ClientError, postJson } from "@/lib/client";
import type { TaskOut } from "@/lib/types";

import { useTaskApp } from "./context";

/** How each engine outcome is shown. Unknown outcomes still render (the engine validates them). */
const OUTCOMES: Record<string, { label: string; icon: ReactNode; key?: string; primary?: boolean; danger?: boolean }> = {
  approved: { label: "Approve & push", icon: <Check className="size-4" />, key: "a", primary: true },
  rejected: { label: "Reject", icon: <X className="size-4" />, key: "r", danger: true },
  retry: { label: "Retry step", icon: <RotateCcw className="size-4" />, primary: true },
  abort: { label: "Abort run", icon: <Ban className="size-4" />, danger: true },
};

/** Decide a task. Claims first when needed (the engine only accepts completion from the claimer);
 * the server validates the output against the app's output_schema, so errors are shown verbatim. */
export function DecisionBar({ options = ["approved", "rejected"], requireReasonFor = [] }: { options?: string[]; requireReasonFor?: string[] }) {
  const { task, me, fields, edited } = useTaskApp();
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const done = task.status === "done";
  const claimedByOther = task.status === "claimed" && task.assignee_user && task.assignee_user !== me;
  const sendsFields = options.includes("approved") && Object.keys(fields).length > 0;

  const submit = useCallback(
    async (decision: string) => {
      if (requireReasonFor.includes(decision) && !reason.trim()) {
        setAsking(true);
        setTimeout(() => reasonRef.current?.focus(), 0);
        return;
      }
      setBusy(decision);
      setError(null);
      try {
        if (task.status !== "claimed") await postJson<TaskOut>(`/tasks/${task.id}/claim`);
        const payload: Record<string, unknown> = decision === "approved" && sendsFields ? { fields } : {};
        if (reason.trim()) payload.reason = reason.trim();
        await postJson<TaskOut>(`/tasks/${task.id}/complete`, { decision, payload });
        router.push("/inbox");
        router.refresh();
      } catch (e) {
        setError(e instanceof ClientError ? `${e.message}${e.details.length ? `: ${JSON.stringify(e.details[0])}` : ""}` : "Something went wrong");
        setBusy(null);
      }
    },
    [fields, reason, requireReasonFor, router, sendsFields, task.id, task.status],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (done || busy || e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return;
      const hit = options.find((o) => OUTCOMES[o]?.key === e.key);
      if (hit) void submit(hit);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, done, options, submit]);

  if (done) return <div className="border-t border-line p-4 text-sm text-muted">Completed: {task.decision}</div>;
  return (
    <div className="sticky bottom-0 border-t border-line bg-panel/95 p-4 backdrop-blur">
      {claimedByOther && <p className="mb-2 text-xs text-warn">Claimed by another reviewer. You can read it, but only they can decide.</p>}
      {asking && (
        <textarea
          ref={reasonRef}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          aria-label="Reason"
          placeholder="Why? (required, goes to the audit log)"
          className="mb-2 h-16 w-full resize-none rounded-md border border-line bg-panel px-2 py-1.5 text-sm outline-none focus:border-accent focus:ring-2 focus:ring-accent/20"
        />
      )}
      {error && (
        <p role="alert" className="mb-2 rounded-md bg-bad-soft px-2 py-1.5 text-xs text-bad">
          {error}
        </p>
      )}
      <div className="flex items-center gap-2">
        <span className="mr-auto text-xs text-muted">
          {sendsFields ? (edited.size ? `${edited.size} field edit${edited.size > 1 ? "s" : ""} will be sent to the TMS` : "Fields go to the TMS as extracted") : ""}
        </span>
        {[...options]
          .sort((a, b) => Number(!!OUTCOMES[a]?.primary) - Number(!!OUTCOMES[b]?.primary)) // primary last (right)
          .map((o) => {
            const m = OUTCOMES[o] ?? { label: o.replaceAll("_", " "), icon: null };
            return (
              <button
                key={o}
                type="button"
                disabled={!!busy || !!claimedByOther}
                onClick={() => void submit(o)}
                className={cx(
                  "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium disabled:opacity-50",
                  m.primary ? "bg-accent text-on-accent hover:opacity-90" : "border border-line hover:bg-canvas",
                  m.danger && "hover:bg-bad-soft hover:text-bad",
                  m.danger && asking && requireReasonFor.includes(o) && "border-bad text-bad",
                )}
              >
                {busy === o ? <Loader2 className="size-4 animate-spin" /> : m.icon}
                {m.label}
                {m.key && (m.primary ? <kbd className="rounded border border-current/30 px-1 font-mono text-[10px]">{m.key}</kbd> : <Kbd>{m.key}</Kbd>)}
              </button>
            );
          })}
      </div>
    </div>
  );
}
