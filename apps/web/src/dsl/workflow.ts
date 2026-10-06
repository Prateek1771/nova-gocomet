// Generated from nova_dsl (pnpm gen:dsl). Do not edit.

export type Apiversion = "nova/v1";
export type Kind = "Workflow";
export type Key = string;
export type Tenant = string | null;
export type Title = string | null;
export type Version = number | null;
export type Type = "manual" | "document_upload" | "event" | "schedule";
export type DocType = string | null;
export type Event = string | null;
export type Cron = string | null;
/**
 * @minItems 1
 */
export type Nodes = [
  AgentNode | DecideNode | RuleNode | HumanTaskNode | ActionNode | ParallelNode | WaitNode | SubflowNode | EndNode,
  ...(
    AgentNode | DecideNode | RuleNode | HumanTaskNode | ActionNode | ParallelNode | WaitNode | SubflowNode | EndNode
  )[]
];
export type Id = string;
export type Title1 = string | null;
export type Timeout = string | null;
export type Type1 = "agent";
export type Agent = string;
export type MaxAttempts = number;
export type Id1 = string;
export type Title2 = string | null;
export type Timeout1 = string | null;
export type Type2 = "decide";
/**
 * @minItems 1
 */
export type Questions = [Question, ...Question[]];
export type Id2 = string;
export type Ask = string;
export type Id3 = string;
export type Title3 = string | null;
export type Timeout2 = string | null;
export type Type3 = "rule";
/**
 * @minItems 1
 */
export type Cases = [Case, ...Case[]];
export type When = string;
export type Goto = string;
export type Default = string;
export type Id4 = string;
export type Title4 = string;
export type Timeout3 = string | null;
export type Type4 = "human_task";
export type Role = string | null;
export type User = string | null;
export type App = string;
export type Sla = string | null;
export type After = string;
/**
 * @minItems 1
 */
export type Outputs = [string, ...string[]];
export type Id5 = string;
export type Title5 = string | null;
export type Timeout4 = string | null;
export type Type5 = "action";
export type Action = string;
export type Id6 = string;
export type Title6 = string | null;
export type Timeout5 = string | null;
export type Type6 = "parallel";
/**
 * @minItems 2
 */
export type Branches = [string[], string[], ...string[][]];
export type Join = "all" | "any";
export type Id7 = string;
export type Title7 = string | null;
export type Timeout6 = string | null;
export type Type7 = "wait";
export type Duration = string | null;
export type Event1 = string | null;
export type Id8 = string;
export type Title8 = string | null;
export type Timeout7 = string | null;
export type Type8 = "subflow";
export type Workflow1 = string;
export type Id9 = string;
export type Title9 = string | null;
export type Timeout8 = string | null;
export type Type9 = "end";
export type Status = "completed" | "rejected" | "cancelled";
export type From = string;
export type To = string;
export type On = string | null;
export type Edges = Edge[];
export type Layout = {
  [k: string]: unknown;
} | null;

export interface Workflow {
  apiVersion?: Apiversion;
  kind?: Kind;
  metadata: Metadata;
  trigger?: Trigger;
  inputs?: Inputs;
  nodes: Nodes;
  edges?: Edges;
  layout?: Layout;
}
export interface Metadata {
  key: Key;
  tenant?: Tenant;
  title?: Title;
  version?: Version;
}
export interface Trigger {
  type?: Type;
  doc_type?: DocType;
  event?: Event;
  cron?: Cron;
}
export interface Inputs {
  [k: string]: unknown;
}
export interface AgentNode {
  id: Id;
  title?: Title1;
  timeout?: Timeout;
  type: Type1;
  agent: Agent;
  with?: With;
  retry?: Retry | null;
}
export interface With {
  [k: string]: unknown;
}
export interface Retry {
  max_attempts?: MaxAttempts;
}
export interface DecideNode {
  id: Id1;
  title?: Title2;
  timeout?: Timeout1;
  type: Type2;
  questions: Questions;
}
export interface Question {
  id: Id2;
  ask: Ask;
  context?: Context;
}
export interface Context {
  [k: string]: unknown;
}
export interface RuleNode {
  id: Id3;
  title?: Title3;
  timeout?: Timeout2;
  type: Type3;
  cases: Cases;
  default: Default;
}
export interface Case {
  when: When;
  goto: Goto;
}
export interface HumanTaskNode {
  id: Id4;
  title: Title4;
  timeout?: Timeout3;
  type: Type4;
  assignee: Assignee;
  app: App;
  sla?: Sla;
  escalate?: Escalate | null;
  outputs?: Outputs;
  with?: With1;
}
export interface Assignee {
  role?: Role;
  user?: User;
}
export interface Escalate {
  after: After;
  to: Assignee;
}
export interface With1 {
  [k: string]: unknown;
}
export interface ActionNode {
  id: Id5;
  title?: Title5;
  timeout?: Timeout4;
  type: Type5;
  action: Action;
  with?: With2;
  retry?: Retry | null;
}
export interface With2 {
  [k: string]: unknown;
}
export interface ParallelNode {
  id: Id6;
  title?: Title6;
  timeout?: Timeout5;
  type: Type6;
  branches: Branches;
  join?: Join;
}
export interface WaitNode {
  id: Id7;
  title?: Title7;
  timeout?: Timeout6;
  type: Type7;
  duration?: Duration;
  event?: Event1;
}
export interface SubflowNode {
  id: Id8;
  title?: Title8;
  timeout?: Timeout7;
  type: Type8;
  workflow: Workflow1;
  with?: With3;
}
export interface With3 {
  [k: string]: unknown;
}
export interface EndNode {
  id: Id9;
  title?: Title9;
  timeout?: Timeout8;
  type: Type9;
  status?: Status;
}
export interface Edge {
  from: From;
  to: To;
  on?: On;
}
