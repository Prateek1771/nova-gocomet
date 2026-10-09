"use client";

import { Search } from "lucide-react";
import { useState } from "react";

import { cx } from "@/components/ui";
import type { Catalog } from "@/lib/types";

import { DND_MIME, type PaletteDrop } from "./canvas";
import { metaOf, PALETTE_TYPES } from "./node-meta";

type Item = PaletteDrop & { title: string; sub?: string };

/** Drag a block onto the canvas (or press Enter on it to add it under the selection). */
export function Palette({ catalog, onAdd }: { catalog: Catalog; onAdd: (d: PaletteDrop) => void }) {
  const [q, setQ] = useState("");
  const groups: [string, Item[]][] = PALETTE_TYPES.map((type) => {
    const fromCatalog = type === "agent" ? catalog.agents : type === "action" ? catalog.actions : type === "human_task" ? catalog.apps : null;
    const items: Item[] = fromCatalog
      ? fromCatalog.map((e) => ({ type, ref: e.key, title: e.title, sub: e.description }))
      : [{ type, title: metaOf(type).label, sub: metaOf(type).blurb }];
    return [type, items.filter((i) => !q || `${i.title} ${i.ref ?? ""} ${i.sub ?? ""}`.toLowerCase().includes(q.toLowerCase()))];
  });

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-line p-2">
        <label className="flex items-center gap-1.5 rounded-md border border-line bg-canvas px-2 py-1 text-xs focus-within:border-accent">
          <Search className="size-3.5 text-muted" aria-hidden />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search blocks" aria-label="Search blocks" className="w-full bg-transparent outline-none placeholder:text-muted" />
        </label>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-2">
        {groups.map(([type, items]) =>
          items.length ? (
            <section key={type}>
              <h3 className="mb-1 px-1 text-[10px] font-semibold uppercase tracking-wider text-muted">{metaOf(type).label}</h3>
              <ul className="space-y-1">
                {items.map((i) => {
                  const m = metaOf(i.type);
                  const Icon = m.icon;
                  return (
                    <li key={`${i.type}:${i.ref ?? ""}`}>
                      <button
                        type="button"
                        draggable
                        onDragStart={(e) => {
                          e.dataTransfer.setData(DND_MIME, JSON.stringify({ type: i.type, ref: i.ref }));
                          e.dataTransfer.effectAllowed = "copy";
                        }}
                        onClick={() => onAdd({ type: i.type, ref: i.ref })}
                        title={i.sub}
                        className="flex w-full cursor-grab items-center gap-2 rounded-lg border border-line bg-panel px-2 py-1.5 text-left hover:border-accent/50 hover:shadow-sm focus-visible:outline-2 focus-visible:outline-accent active:cursor-grabbing"
                      >
                        <span className={cx("grid size-7 shrink-0 place-items-center rounded-md", m.tile)}>
                          <Icon className="size-3.5" />
                        </span>
                        <span className="min-w-0">
                          <span className="block truncate text-xs font-medium">{i.title}</span>
                          {i.ref && <span className="block truncate font-mono text-[10px] text-muted">{i.ref}</span>}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </section>
          ) : null,
        )}
      </div>
    </div>
  );
}
