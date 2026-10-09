import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";
import { parse as parseYaml } from "yaml";

import { addEdge, addNode, moveNode, removeNode, setField, toGraph } from "./yaml-graph";

// LF here (git normalises the repo); CRLF has its own test below
const W1 = readFileSync(join(__dirname, "../../../../../definitions/workflows/acme/bol_intake.yaml"), "utf8").replace(/\r\n/g, "\n");

/** Lines of `before` that must appear unchanged, in order, in `after`. Returns the first missing one. */
function keptInOrder(before: string, after: string, skip: (l: string) => boolean = () => false) {
  const out = after.split("\n");
  let j = 0;
  for (const line of before.split("\n")) {
    if (skip(line)) continue;
    while (j < out.length && out[j] !== line) j++;
    if (j === out.length) return line;
    j++;
  }
  return null;
}

const diffLines = (a: string, b: string) => {
  const al = a.split("\n");
  return b.split("\n").filter((l, i) => l !== al[i]);
};

describe("YAML ↔ graph round-trip (rule 7)", () => {
  it("reads nodes and edges for the canvas", () => {
    const g = toGraph(W1);
    expect(g.nodes.map((n) => n.id)).toEqual(["extract", "validate", "material", "route", "review", "push", "done", "rejected"]);
    expect(g.edges).toContainEqual({ from: "review", to: "push", on: "approved" });
  });

  it("editing one field changes exactly that line (folded scalars, comments untouched)", () => {
    expect(diffLines(W1, setField(W1, "review", "sla", "2h"))).toEqual(["    sla: 2h"]);
    expect(diffLines(W1, setField(W1, "material", "timeout", "30s"))).toEqual(["    timeout: 30s"]);
  });

  it("adding a missing key adds exactly one line inside the node", () => {
    const out = setField(W1, "push", "timeout", "30s");
    expect(keptInOrder(W1, out)).toBeNull();
    expect(out.split("\n").length).toBe(W1.split("\n").length + 1);
    expect(toGraph(out).nodes.find((n) => n.id === "push")).toBeTruthy();
    expect(((parseYaml(out) as Record<string, unknown>).nodes as { id: string; timeout?: string }[]).find((n) => n.id === "push")?.timeout).toBe("30s");
  });

  it("moving nodes only appends a layout block, then updates it in place", () => {
    const once = moveNode(W1, "review", 420.4, 180);
    expect(once.startsWith(W1)).toBe(true);
    const twice = moveNode(moveNode(once, "extract", 0, 0), "review", 440, 200);
    expect(twice).toContain("review: {x: 440, y: 200}");
    expect(twice.startsWith(W1)).toBe(true);
    expect(toGraph(twice).nodes.find((n) => n.id === "review")).toMatchObject({ x: 440, y: 200 });
  });

  it("adding a node + edge inserts new lines only", () => {
    let out = addNode(W1, { id: "notify", type: "action", action: "notify.log", with: { message: "pushed" } });
    out = addEdge(out, { from: "notify", to: "done" });
    expect(keptInOrder(W1, out)).toBeNull();
    const g = toGraph(out);
    expect(g.nodes.at(-1)).toMatchObject({ id: "notify", type: "action" });
    expect(g.edges.at(-1)).toEqual({ from: "notify", to: "done" });
    expect(() => addNode(out, { id: "notify", type: "end" })).toThrow(/already exists/);
  });

  it("removing a node drops its lines, edges and layout; every other line is untouched", () => {
    const out = removeNode(moveNode(W1, "rejected", 0, 0), "rejected");
    const g = toGraph(out);
    expect(g.nodes.some((n) => n.id === "rejected")).toBe(false);
    expect(g.edges.some((e) => e.to === "rejected")).toBe(false);
    const gone = new Set(["  - id: rejected", "    type: end", "    status: rejected", "  - {from: review, to: rejected, on: rejected}"]);
    expect(keptInOrder(W1, out, (l) => gone.has(l))).toBeNull();
    expect(out.split("\n").length).toBe(W1.split("\n").length - 4 + 1); // -4 node/edge lines; +1 the now-empty "layout:"
  });

  it("keeps CRLF line endings", () => {
    const crlf = W1.replace(/\n/g, "\r\n");
    const out = setField(crlf, "review", "sla", "2h");
    expect(out).toBe(crlf.replace("    sla: 4h", "    sla: 2h"));
  });

  it("rejects invalid YAML instead of guessing", () => {
    expect(() => setField("nodes: [ :::", "x", "a", 1)).toThrow(/invalid YAML/);
  });
});
