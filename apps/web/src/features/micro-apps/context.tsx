"use client";

import { createContext, useContext } from "react";

import type { MicroApp, TaskOut } from "@/lib/types";

/** State every registry component of one task shares: the field in focus (drives the PDF highlight)
 * and the reviewer's edited copy of the extracted fields (what reaches the TMS on approve). */
export type TaskApp = {
  task: TaskOut;
  app: MicroApp;
  me: string;
  focus: string | null;
  setFocus: (f: string | null) => void;
  fields: Record<string, unknown>;
  setField: (k: string, v: unknown) => void;
  edited: Set<string>;
};

export const Ctx = createContext<TaskApp | null>(null);

export function useTaskApp(): TaskApp {
  const c = useContext(Ctx);
  if (!c) throw new Error("useTaskApp outside a micro-app");
  return c;
}

/** "container_numbers[1]" and "cargo_lines[0].weight_kg" both belong to their top-level field. */
export const baseField = (path: string) => path.split(/[[.]/)[0];
