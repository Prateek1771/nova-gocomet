"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** Re-render the server page every `ms` while something is still moving. ponytail: SSE (FR-1.7) in M3. */
export function AutoRefresh({ active, ms = 2000 }: { active: boolean; ms?: number }) {
  const router = useRouter();
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => router.refresh(), ms);
    return () => clearInterval(t);
  }, [active, ms, router]);
  return null;
}
