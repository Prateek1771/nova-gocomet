"use client";

import { CheckCircle2, CircleAlert, FileUp, Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useRef, useState } from "react";

import { cx } from "@/components/ui";
import { api, ClientError } from "@/lib/client";
import type { UploadOut } from "@/lib/types";

type Item = { name: string; state: "uploading" | "done" | "duplicate" | "error"; message?: string };
// doc types with a default workflow (definitions/doc_types); the server validates the key
const DOC_TYPES = [
  { key: "bill_of_lading", label: "Bill of Lading", flow: "BoL intake" },
  { key: "invoice", label: "Freight invoice", flow: "invoice match & approval" },
] as const;

/** Drop PDFs → POST /documents each (server validates bytes, dedupes, starts the doc type's workflow). */
export function UploadZone() {
  const router = useRouter();
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [items, setItems] = useState<Item[]>([]);
  const [docType, setDocType] = useState<(typeof DOC_TYPES)[number]["key"]>("bill_of_lading");
  const current = DOC_TYPES.find((d) => d.key === docType)!;

  const upload = useCallback(
    async (files: File[]) => {
      if (!files.length) return;
      setItems((prev) => [...files.map((f) => ({ name: f.name, state: "uploading" as const })), ...prev].slice(0, 12));
      await Promise.all(
        files.map(async (f) => {
          const form = new FormData();
          form.append("file", f);
          form.append("doc_type", docType);
          let next: Item;
          try {
            const r = await api<UploadOut>("/documents", { method: "POST", body: form });
            next = { name: f.name, state: r.duplicate ? "duplicate" : "done", message: r.duplicate ? "already uploaded" : "run started" };
          } catch (e) {
            next = { name: f.name, state: "error", message: e instanceof ClientError ? e.message : "upload failed" };
          }
          setItems((prev) => prev.map((it) => (it.name === f.name && it.state === "uploading" ? next : it)));
        }),
      );
      router.refresh();
    },
    [router, docType],
  );

  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <span className="text-xs font-medium text-muted">Document type</span>
        <div className="flex rounded-md border border-line bg-panel p-0.5" role="radiogroup" aria-label="Document type">
          {DOC_TYPES.map((d) => (
            <button
              key={d.key}
              type="button"
              role="radio"
              aria-checked={docType === d.key}
              onClick={() => setDocType(d.key)}
              className={cx("rounded px-2.5 py-1 text-xs font-medium", docType === d.key ? "bg-accent-soft text-accent" : "text-muted hover:text-ink")}
            >
              {d.label}
            </button>
          ))}
        </div>
      </div>
      <button
        type="button"
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          void upload(Array.from(e.dataTransfer.files));
        }}
        className={cx(
          "group flex w-full flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-6 py-8 text-center transition-colors focus-visible:outline-2 focus-visible:outline-accent",
          over ? "border-accent bg-accent-soft" : "border-line bg-panel hover:border-accent/50 hover:bg-canvas",
        )}
      >
        <FileUp className={cx("size-7 transition-colors", over ? "text-accent" : "text-muted group-hover:text-accent")} aria-hidden />
        <span className="text-sm font-medium">Drop {current.label === "Bill of Lading" ? "Bills of Lading" : "freight invoices"} here, or click to choose</span>
        <span className="text-xs text-muted">PDF up to 20 MB · each upload starts the {current.flow} workflow</span>
      </button>
      <input
        ref={input}
        type="file"
        accept="application/pdf"
        multiple
        className="sr-only"
        aria-label="Upload PDF documents"
        onChange={(e) => {
          void upload(Array.from(e.target.files ?? []));
          e.target.value = "";
        }}
      />
      {items.length > 0 && (
        <ul className="mt-3 grid gap-1.5 sm:grid-cols-2 lg:grid-cols-3" aria-live="polite">
          {items.map((it, i) => (
            <li key={`${it.name}-${i}`} className="flex items-center gap-2 rounded-lg border border-line bg-panel px-3 py-2 text-sm">
              {it.state === "uploading" && <Loader2 className="size-4 animate-spin text-info" aria-hidden />}
              {it.state === "done" && <CheckCircle2 className="size-4 text-ok" aria-hidden />}
              {it.state === "duplicate" && <CheckCircle2 className="size-4 text-muted" aria-hidden />}
              {it.state === "error" && <CircleAlert className="size-4 text-bad" aria-hidden />}
              <span className="truncate font-medium">{it.name}</span>
              <span className={cx("ml-auto shrink-0 text-xs", it.state === "error" ? "text-bad" : "text-muted")}>{it.message ?? "uploading…"}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
