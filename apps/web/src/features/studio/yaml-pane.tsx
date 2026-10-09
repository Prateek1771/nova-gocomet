"use client";

import { DiffEditor, Editor, loader, type OnMount } from "@monaco-editor/react";
// monaco-editor pinned to 0.54: 0.55+ adds an exports map that breaks monaco-yaml's worker import (monaco-worker-manager)
// ponytail: the full monaco bundle (all languages); trim to editor.api + yaml if the island gets heavy
import * as monaco from "monaco-editor";
import { configureMonacoYaml } from "monaco-yaml";
import { useEffect, useRef, useSyncExternalStore } from "react";

import schema from "@/dsl/workflow.schema.json"; // copied by `pnpm gen:dsl` (the image only sees apps/web)

import type { YamlError } from "./yaml-graph";

// self-hosted Monaco (no CDN): workers come from our own bundle, like the pdf.js worker
globalThis.MonacoEnvironment = {
  getWorker(_id: string, label: string) {
    if (label === "yaml") return new Worker(new URL("monaco-yaml/yaml.worker", import.meta.url));
    return new Worker(new URL("monaco-editor/esm/vs/editor/editor.worker", import.meta.url));
  },
};
loader.config({ monaco });
configureMonacoYaml(monaco, {
  enableSchemaRequest: false,
  schemas: [{ uri: "inmemory://nova/workflow.schema.json", fileMatch: ["*"], schema: schema as never }],
});

export type Marker = YamlError & { severity: "error" | "warning"; endLine?: number };

const darkQuery = () => window.matchMedia("(prefers-color-scheme: dark)");
function useDark() {
  return useSyncExternalStore(
    (on) => {
      const q = darkQuery();
      q.addEventListener("change", on);
      return () => q.removeEventListener("change", on);
    },
    () => darkQuery().matches,
    () => false,
  );
}

const OPTIONS = {
  minimap: { enabled: false },
  fontSize: 12.5,
  fontFamily: "var(--font-geist-mono), ui-monospace, monospace",
  lineNumbersMinChars: 3,
  scrollBeyondLastLine: false,
  tabSize: 2,
  renderWhitespace: "boundary",
  automaticLayout: true,
  padding: { top: 8 },
} as const;

/** The canonical YAML. Markers come from the parser (syntax) and the API (validation issues). */
export function YamlPane({
  value,
  onChange,
  markers,
  readOnly,
  reveal,
}: {
  value: string;
  onChange: (v: string) => void;
  markers: Marker[];
  readOnly?: boolean;
  reveal?: { line: number; n: number } | null;
}) {
  const dark = useDark();
  const ed = useRef<Parameters<OnMount>[0] | null>(null);

  useEffect(() => {
    const model = ed.current?.getModel();
    if (!model) return;
    monaco.editor.setModelMarkers(
      model,
      "nova",
      markers.map((m) => ({
        message: m.message,
        severity: m.severity === "error" ? monaco.MarkerSeverity.Error : monaco.MarkerSeverity.Warning,
        startLineNumber: m.line,
        startColumn: m.col,
        endLineNumber: m.endLine ?? m.line,
        endColumn: model.getLineMaxColumn(Math.min(m.endLine ?? m.line, model.getLineCount())),
      })),
    );
  }, [markers, value]);

  useEffect(() => {
    if (!reveal || !ed.current) return;
    ed.current.revealLineInCenter(reveal.line);
    ed.current.setSelection({ startLineNumber: reveal.line, startColumn: 1, endLineNumber: reveal.line, endColumn: 1 });
  }, [reveal]);

  return (
    <Editor
      language="yaml"
      path="workflow.yaml"
      value={value}
      theme={dark ? "vs-dark" : "light"}
      onChange={(v) => onChange(v ?? "")}
      onMount={(e) => {
        ed.current = e;
      }}
      options={{ ...OPTIONS, readOnly, ariaLabel: "Workflow YAML" }}
      loading={<div className="p-4 text-xs text-muted">Loading editor…</div>}
    />
  );
}

/** Side-by-side YAML diff (published version ↔ draft). */
export function YamlDiff({ original, modified, labels }: { original: string; modified: string; labels: [string, string] }) {
  const dark = useDark();
  return (
    <div className="flex h-full flex-col">
      <div className="grid grid-cols-2 border-b border-line text-[11px] font-medium text-muted">
        <span className="px-3 py-1">{labels[0]}</span>
        <span className="px-3 py-1">{labels[1]}</span>
      </div>
      <div className="min-h-0 flex-1">
        <DiffEditor language="yaml" original={original} modified={modified} keepCurrentOriginalModel keepCurrentModifiedModel theme={dark ? "vs-dark" : "light"} options={{ ...OPTIONS, readOnly: true, renderSideBySide: true }} />
      </div>
    </div>
  );
}
