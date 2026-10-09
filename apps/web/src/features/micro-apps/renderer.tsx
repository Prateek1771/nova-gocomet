"use client";

import type { ComponentType } from "react";

import type { LayoutNode } from "@/lib/types";

import { useTaskApp } from "./context";
import { DecisionBar } from "./decision-bar";
import { DocumentViewer } from "./document-viewer";
import { FieldForm } from "./field-form";
import { IssueList } from "./issue-list";
import { PayloadView } from "./payload-view";

// The component registry (FR-3.2). A micro-app definition can only name what's here.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const REGISTRY: Record<string, ComponentType<any>> = { DocumentViewer, IssueList, FieldForm, DecisionBar, PayloadView };

/** `$.a.b` over the task's run-context snapshot (LLD §9). ponytail: dotted paths only, no filters. */
export function resolve(path: string, root: unknown): unknown {
  if (!path.startsWith("$")) return path;
  return path
    .slice(1)
    .split(".")
    .filter(Boolean)
    .reduce<unknown>((v, k) => (v && typeof v === "object" ? (v as Record<string, unknown>)[k] : undefined), root);
}

/** Recursive layout renderer: split / stack containers, registry components with JSONPath binds. */
export function Render({ node }: { node: LayoutNode }) {
  const { task } = useTaskApp();
  if (node.type === "split")
    return (
      <div className="grid h-full min-h-0 grid-cols-1 lg:grid-cols-[minmax(0,1.15fr)_minmax(380px,1fr)]">
        {node.children?.map((c, i) => (
          <div key={i} className="min-h-0 overflow-hidden border-line lg:[&:not(:last-child)]:border-r">
            <Render node={c} />
          </div>
        ))}
      </div>
    );
  if (node.type === "stack")
    return (
      <div className="flex h-full min-h-0 flex-col overflow-y-auto">
        {node.children?.map((c, i) => (
          <Render key={i} node={c} />
        ))}
      </div>
    );
  const C = REGISTRY[node.type];
  if (!C) return <div className="p-4 text-sm text-bad">Unknown component {node.type}</div>;
  const { bind = {}, ...rest } = node;
  const props = Object.fromEntries(Object.entries(rest).filter(([k]) => k !== "type" && k !== "children"));
  const bound = Object.fromEntries(Object.entries(bind).map(([k, p]) => [k, resolve(p, task.payload)]));
  return <C {...props} {...bound} />;
}
