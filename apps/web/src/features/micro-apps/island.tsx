"use client";

import { Loader2 } from "lucide-react";
import dynamic from "next/dynamic";

import type { MicroApp, TaskOut } from "@/lib/types";

// pdf.js and the micro-app need `window`: a client-only island (ADR-017)
const App = dynamic(() => import("./micro-app"), {
  ssr: false,
  loading: () => (
    <div className="grid h-full place-items-center text-muted">
      <Loader2 className="size-5 animate-spin" />
    </div>
  ),
});

export function MicroAppIsland(props: { task: TaskOut; app: MicroApp; me: string }) {
  return <App {...props} />;
}
