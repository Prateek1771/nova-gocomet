import type { components } from "./api-types";

type S = components["schemas"];
export type DocumentOut = S["DocumentOut"];
export type DocumentDetail = S["DocumentDetail"];
export type UploadOut = S["UploadOut"];
export type RunOut = S["RunOut"];
export type RunDetail = S["RunDetail"];
export type StepOut = S["StepOut"];
export type TaskOut = S["TaskOut"];

/** Evidence the extractor attaches to a field: page + bbox in PDF points, top-left origin (ADR-022). */
export type Evidence = { field: string; page: number; bbox: [number, number, number, number] | null; text: string; score: number | null };
export type Issue = { code: string; field: string; severity: "high" | "medium" | "low"; message: string; evidence: Evidence[] };

export type LayoutNode = {
  type: string;
  children?: LayoutNode[];
  bind?: Record<string, string>;
  [prop: string]: unknown;
};
export type MicroApp = {
  key: string;
  title?: string;
  layout: LayoutNode;
  output_schema?: Record<string, unknown>;
  schemas: Record<string, JsonSchema>;
};
export type JsonSchema = {
  type?: string | string[];
  description?: string;
  enum?: unknown[];
  properties?: Record<string, JsonSchema>;
  items?: JsonSchema;
  required?: string[];
};
