"use client";

import { useTaskApp } from "./context";

/** Fallback body for tasks without a micro-app definition: the run context the engine attached. */
export function PayloadView() {
  const { task } = useTaskApp();
  return (
    <section className="p-4">
      <h2 className="text-sm font-semibold">Task context</h2>
      {task.app_key === "step_failure" && (
        <p className="mt-2 max-w-2xl text-sm text-muted">
          A workflow step failed after its automatic retries; the error is in the title. <b className="text-ink">Retry step</b> runs it
          again (fix the cause first, e.g. a model gateway outage); <b className="text-ink">Abort run</b> ends the run as failed.
        </p>
      )}
      {Object.keys(task.payload).length > 0 && (
        <pre className="mt-2 max-h-[60vh] overflow-auto rounded-lg border border-line bg-canvas p-3 font-mono text-xs leading-relaxed">
          {JSON.stringify(task.payload, null, 2)}
        </pre>
      )}
    </section>
  );
}
