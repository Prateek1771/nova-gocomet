import type { components } from "./api-types";

type S = components["schemas"];
export type DocumentOut = S["DocumentOut"];
export type DocumentDetail = S["DocumentDetail"];
export type UploadOut = S["UploadOut"];
export type RunOut = S["RunOut"];
export type RunDetail = S["RunDetail"];
export type StepOut = S["StepOut"];
export type TaskOut = S["TaskOut"];
export type ExceptionOut = S["ExceptionOut"];
export type MetricOut = S["MetricOut"];
export type NotificationOut = S["NotificationOut"];
export type LagOut = S["LagOut"];

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

/** Studio (M3): catalog entries from definitions/catalog.yaml, served by /catalog/*. */
export type CatalogParam = { type: "string" | "expression" | "number"; required?: boolean; hint?: string };
export type CatalogEntry = { key: string; title: string; description?: string; tier?: string; builtin?: boolean; with?: Record<string, CatalogParam>; outputs?: string[] };
export type Catalog = { agents: CatalogEntry[]; actions: CatalogEntry[]; apps: CatalogEntry[] };
export type ValidationIssue = { code: string; message: string; node_id: string | null };
export type Validation = { valid: boolean; issues: ValidationIssue[] };
export type VersionBrief = S["VersionBrief"];
export type WorkflowSummary = S["WorkflowSummary"];

/** M4 governance. */
export type Budget = { provisioned: boolean; max_budget: number | null; spend: number; period: string; resets_at: string | null };
export type AuditEntry = {
  seq: number;
  at: string;
  actor_type: string;
  actor_id: string;
  action: string;
  subject: string;
  evidence: unknown;
  definition_version: number | null;
  config_version: number | null;
  hash: string;
};
export type AuditVerification = { valid: boolean; entries: number; broken: { seq: number; id: number; reason: string }[] };
export type TenantConfigOut = { version: number; config: Record<string, unknown>; published_by: string; published_at: string };
