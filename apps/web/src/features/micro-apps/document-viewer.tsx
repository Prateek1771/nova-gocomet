"use client";

import { Loader2, Minus, Plus, ScanSearch } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";

import { cx } from "@/components/ui";
import type { Evidence, Issue } from "@/lib/types";

import { baseField, useTaskApp } from "./context";

pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();

type Props = { document_id?: string; evidence?: Evidence[]; issues?: Issue[] };

/** The source document with evidence boxes on top. bbox = PDF points, top-left origin (ADR-022), so a
 * box is placed at bbox * (rendered width / page width). Issue boxes are red; the focused field is
 * outlined and scrolled into view. */
export function DocumentViewer({ document_id, evidence = [], issues = [] }: Props) {
  const { focus, setFocus } = useTaskApp();
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [zoom, setZoom] = useState(1);
  const [pages, setPages] = useState(0);
  const [pageW, setPageW] = useState<Record<number, number>>({});

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(320, Math.floor(e.contentRect.width) - 24)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const issueFields = useMemo(() => new Set(issues.map((i) => i.field)), [issues]);
  const issueBases = useMemo(() => new Set(issues.map((i) => baseField(i.field))), [issues]);

  useEffect(() => {
    if (!focus) return;
    box.current?.querySelector(`[data-field="${CSS.escape(focus)}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [focus]);

  const w = Math.round(width * zoom);
  const scanned = evidence.length > 0 && evidence.every((e) => !e.bbox);

  if (!document_id) return <div className="p-6 text-sm text-muted">No document bound.</div>;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2 text-xs text-muted">
        <ScanSearch className="size-4" aria-hidden />
        <span className="font-medium text-ink">Source document</span>
        {scanned && <span className="rounded bg-warn-soft px-1.5 py-0.5 font-medium text-warn">scan · no text layer, read by vision</span>}
        <span className="ml-auto flex items-center gap-1">
          <button type="button" aria-label="Zoom out" onClick={() => setZoom((z) => Math.max(0.6, z - 0.15))} className="rounded p-1 hover:bg-canvas">
            <Minus className="size-3.5" />
          </button>
          <span className="num w-10 text-center">{Math.round(zoom * 100)}%</span>
          <button type="button" aria-label="Zoom in" onClick={() => setZoom((z) => Math.min(2.5, z + 0.15))} className="rounded p-1 hover:bg-canvas">
            <Plus className="size-3.5" />
          </button>
        </span>
      </div>
      <div ref={box} className="min-h-0 flex-1 overflow-auto bg-canvas p-3">
        <Document
          file={`/api/v1/documents/${document_id}/file`}
          onLoadSuccess={(d) => setPages(d.numPages)}
          loading={
            <div className="grid h-96 place-items-center text-sm text-muted">
              <Loader2 className="size-5 animate-spin" />
            </div>
          }
          error={<div className="p-6 text-sm text-bad">Couldn&apos;t load the document.</div>}
        >
          {Array.from({ length: pages }, (_, i) => i + 1).map((n) => {
            const scale = pageW[n] ? w / pageW[n] : 0;
            return (
              <div key={n} className="relative mx-auto mb-3 shadow-sm ring-1 ring-line" style={{ width: w }}>
                <Page
                  pageNumber={n}
                  width={w}
                  renderTextLayer={false}
                  renderAnnotationLayer={false}
                  onLoadSuccess={(p) => setPageW((m) => ({ ...m, [n]: p.originalWidth }))}
                />
                {scale > 0 &&
                  evidence
                    .filter((e) => e.page === n && e.bbox)
                    .map((e, k) => {
                      const [x0, top, x1, bottom] = e.bbox!;
                      const isIssue = issueFields.has(e.field) || (issueBases.has(baseField(e.field)) && !e.field.includes("["));
                      const isFocus = focus !== null && (e.field === focus || baseField(e.field) === focus);
                      const pad = 2;
                      return (
                        <button
                          type="button"
                          key={k}
                          data-field={e.field}
                          title={`${e.field}: ${e.text}${e.score != null ? ` · match ${Math.round(e.score * 100)}%` : ""}`}
                          aria-label={`Evidence for ${e.field}`}
                          onClick={() => setFocus(e.field)}
                          className={cx(
                            "absolute rounded-[3px] transition-all",
                            isIssue ? "bg-bad/20 ring-2 ring-bad" : "bg-accent/5 ring-1 ring-accent/30 hover:bg-accent/15",
                            isFocus && "z-10 bg-accent/25 ring-2 ring-accent ring-offset-1",
                          )}
                          style={{
                            left: x0 * scale - pad,
                            top: top * scale - pad,
                            width: (x1 - x0) * scale + pad * 2,
                            height: (bottom - top) * scale + pad * 2,
                          }}
                        />
                      );
                    })}
              </div>
            );
          })}
        </Document>
      </div>
    </div>
  );
}
