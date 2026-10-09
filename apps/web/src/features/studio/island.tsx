"use client";

import { Loader2 } from "lucide-react";
import dynamic from "next/dynamic";
import type { ComponentProps } from "react";

// React Flow, Monaco and elkjs need `window`: client-only islands (CLAUDE.md conventions)
const loading = () => (
  <div className="grid h-full place-items-center text-muted">
    <Loader2 className="size-5 animate-spin" />
  </div>
);
const Studio = dynamic(() => import("./studio"), { ssr: false, loading });
const RunGraph = dynamic(() => import("./run-graph"), { ssr: false, loading });

export function StudioIsland(props: ComponentProps<typeof Studio>) {
  return <Studio {...props} />;
}

export function RunGraphIsland(props: ComponentProps<typeof RunGraph>) {
  return <RunGraph {...props} />;
}
