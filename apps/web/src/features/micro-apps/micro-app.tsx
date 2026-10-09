"use client";

import { useMemo, useState } from "react";

import type { MicroApp as App, TaskOut } from "@/lib/types";

import { Ctx } from "./context";
import { Render } from "./renderer";

export default function MicroApp({ task, app, me }: { task: TaskOut; app: App; me: string }) {
  const initial = useMemo(() => ((task.payload as { fields?: Record<string, unknown> }).fields ?? {}) as Record<string, unknown>, [task.payload]);
  const [fields, setFields] = useState(initial);
  const [focus, setFocus] = useState<string | null>(null);
  const edited = useMemo(
    () => new Set(Object.keys(fields).filter((k) => JSON.stringify(fields[k]) !== JSON.stringify(initial[k]))),
    [fields, initial],
  );
  const value = {
    task,
    app,
    me,
    focus,
    setFocus,
    fields,
    setField: (k: string, v: unknown) => setFields((f) => ({ ...f, [k]: v })),
    edited,
  };
  return (
    <Ctx.Provider value={value}>
      <Render node={app.layout} />
    </Ctx.Provider>
  );
}
