"use client";

import { TriangleAlert } from "lucide-react";

/** Anything else that fails while rendering a screen: say so plainly and offer a retry. */
export default function ScreenError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div className="mx-auto mt-16 max-w-md text-center">
      <TriangleAlert className="mx-auto size-10 text-warn" aria-hidden />
      <h1 className="mt-3 text-lg font-semibold">This screen couldn&apos;t load</h1>
      <p className="mt-1 text-sm text-muted">The API returned an error. It&apos;s logged with reference {error.digest ?? "n/a"}.</p>
      <button type="button" onClick={reset} className="mt-4 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-on-accent">
        Try again
      </button>
    </div>
  );
}
