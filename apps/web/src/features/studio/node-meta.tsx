import { Bot, CircleDot, Clock, GitBranch, GitFork, Hand, Play, Square, Workflow, Zap } from "lucide-react";
import type { ComponentType } from "react";

/** How each DSL node type looks on the canvas, in the palette and in the run timeline. */
export const NODE_TYPES: Record<string, { label: string; icon: ComponentType<{ className?: string }>; tile: string; strip: string; blurb: string }> = {
  agent: { label: "Agent", icon: Bot, tile: "bg-violet-500/10 text-violet-700 dark:text-violet-300", strip: "bg-violet-500", blurb: "A governed AI agent: extracts or reasons, never decides" },
  decide: { label: "Decide", icon: Zap, tile: "bg-amber-500/10 text-amber-700 dark:text-amber-300", strip: "bg-amber-500", blurb: "Yes/no questions answered with a probability (Jev)" },
  rule: { label: "Rule", icon: GitBranch, tile: "bg-sky-500/10 text-sky-700 dark:text-sky-300", strip: "bg-sky-500", blurb: "Deterministic CEL branches, first match wins" },
  human_task: { label: "Human task", icon: Hand, tile: "bg-rose-500/10 text-rose-700 dark:text-rose-300", strip: "bg-rose-500", blurb: "A person decides in a micro-app, with an SLA" },
  action: { label: "Action", icon: Play, tile: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300", strip: "bg-emerald-500", blurb: "An idempotent side effect: TMS, ERP, notify" },
  wait: { label: "Wait", icon: Clock, tile: "bg-slate-500/10 text-slate-700 dark:text-slate-300", strip: "bg-slate-400", blurb: "Pause for a duration or an event" },
  parallel: { label: "Parallel", icon: GitFork, tile: "bg-slate-500/10 text-slate-700 dark:text-slate-300", strip: "bg-slate-400", blurb: "Run branches side by side" },
  subflow: { label: "Subflow", icon: Workflow, tile: "bg-slate-500/10 text-slate-700 dark:text-slate-300", strip: "bg-slate-400", blurb: "Run another published workflow" },
  end: { label: "End", icon: Square, tile: "bg-slate-500/10 text-slate-700 dark:text-slate-300", strip: "bg-slate-500", blurb: "Finish the run with a status" },
};

export const metaOf = (type: string) => NODE_TYPES[type] ?? { label: type, icon: CircleDot, tile: "bg-slate-500/10 text-slate-600", strip: "bg-slate-400", blurb: "" };

/** Palette order: the types worth dragging. parallel/subflow stay YAML-only for now. */
export const PALETTE_TYPES = ["agent", "decide", "rule", "human_task", "action", "wait", "end"] as const;
