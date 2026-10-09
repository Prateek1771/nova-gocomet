import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";
import { parse as parseYaml } from "yaml";

import { addEdge, addNode, appendItem, freshId, moveNode, moveNodes, removeEdge, removeItem, removeNode, setField, setPath, toGraph, yamlErrors } from "./yaml-graph";

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
    expect(g.edges).toContainEqual({ from: "notify", to: "done" });
    expect(() => addNode(out, { id: "notify", type: "end" })).toThrow(/already exists/);
  });

  it("removing a node drops its lines, edges and layout; every other line is untouched", () => {
    const out = removeNode(moveNode(W1, "rejected", 0, 0), "rejected");
    const g = toGraph(out);
    expect(g.nodes.some((n) => n.id === "rejected")).toBe(false);
    expect(g.edges.some((e) => e.to === "rejected")).toBe(false);
    const gone = new Set(["  - id: rejected", "    type: end", "    status: rejected", "  - {from: review, to: rejected, on: rejected}"]);
    expect(keptInOrder(W1, out, (l) => gone.has(l))).toBeNull();
    expect(out.split("\n").length).toBe(W1.split("\n").length - 4); // -4 node/edge lines; the emptied layout block goes too
    expect(moveNode(out, "done", 1, 2).match(/^layout:/gm)).toHaveLength(1);
  });

  it("keeps CRLF line endings", () => {
    const crlf = W1.replace(/\n/g, "\r\n");
    const out = setField(crlf, "review", "sla", "2h");
    expect(out).toBe(crlf.replace("    sla: 4h", "    sla: 2h"));
  });

  it("rejects invalid YAML instead of guessing", () => {
    expect(() => setField("nodes: [ :::", "x", "a", 1)).toThrow(/invalid YAML/);
    expect(() => toGraph("nodes: [ :::")).toThrow(/invalid YAML/);
    expect(yamlErrors("a: 1\nb: [\n")[0]).toMatchObject({ line: expect.any(Number) });
    expect(yamlErrors(W1)).toEqual([]);
  });

  it("derives rule branches as implicit edges and labels nodes by what they reference", () => {
    const g = toGraph(W1);
    expect(g.edges).toContainEqual(expect.objectContaining({ from: "route", to: "review", on: "case 1", implicit: true }));
    expect(g.edges).toContainEqual({ from: "route", to: "push", on: "default", implicit: true });
    expect(g.nodes.find((n) => n.id === "extract")?.ref).toBe("doc_extractor");
    expect(g.hasLayout).toBe(false);
  });

  it("setPath edits nested values: block maps, flow maps, sequences", () => {
    // block map inside a sequence item (decide question)
    expect(diffLines(W1, setPath(W1, "material", ["questions", 0, "threshold"], 0.5))).toEqual(["        threshold: 0.5"]);
    // flow map: re-inlined as one line, quoting the expression correctly
    const flow = setPath(W1, "extract", ["with", "schema"], "bol_v2");
    expect(diffLines(W1, flow)).toEqual(['    with: {document_id: "${{ input.document_id }}", schema: bol_v2}']);
    // missing nested key on a block map → one new line at the right indent
    const added = setPath(W1, "validate", ["with", "note"], "x");
    expect(keptInOrder(W1, added)).toBeNull();
    expect(added).toContain("      severity: \"${{ tenant.check_severity ?? {} }}\"\n      note: x\n");
    // delete
    expect(diffLines(setPath(W1, "review", ["sla"], undefined), W1).length).toBeGreaterThan(0);
    expect(parseYaml(setPath(W1, "review", ["sla"], undefined)).nodes[4].sla).toBeUndefined();
  });

  it("removeEdge drops only that edge line; moveNodes writes many positions at once", () => {
    const out = removeEdge(W1, "review", "rejected", "rejected");
    expect(diffLines(out, W1).length).toBeGreaterThan(0);
    expect(toGraph(out).edges.some((e) => e.from === "review" && e.to === "rejected")).toBe(false);
    expect(keptInOrder(W1, out, (l) => l === "  - {from: review, to: rejected, on: rejected}")).toBeNull();
    const laid = moveNodes(W1, { extract: { x: 1, y: 2 }, done: { x: 3, y: 4 } });
    expect(toGraph(laid).nodes.find((n) => n.id === "done")).toMatchObject({ x: 3, y: 4 });
    expect(moveNodes(laid, { extract: { x: 9, y: 9 }, push: { x: 5, y: 5 } }).match(/^layout:/gm)).toHaveLength(1);
  });

  it("appendItem / removeItem touch only the item's own lines", () => {
    const added = appendItem(W1, "route", ["cases"], { when: "false", goto: "done" });
    expect(removedLines(W1, added)).toEqual([]);
    expect(added).toContain("        goto: review\n      - {when: \"false\", goto: done}\n    default: push");
    expect(toGraph(added).edges).toContainEqual(expect.objectContaining({ from: "route", to: "done", on: "case 3" }));
    const back = removeItem(added, "route", ["cases"], 2);
    expect(back).toBe(W1);
    const flowOut = appendItem(W1, "review", ["outputs"], "escalated");
    expect(diffLines(W1, flowOut)).toEqual(["    outputs: [approved, rejected, escalated]"]);
  });

  it("freshId never collides", () => {
    expect(freshId(W1, "notify")).toBe("notify");
    expect(freshId(W1, "review")).toBe("review_2");
  });
});

/** Seeded PRNG so a failure reproduces (mulberry32). */
function rng(seed: number) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Lines in `before` that are missing from `after` (multiset difference). */
function removedLines(before: string, after: string) {
  const left = new Map<string, number>();
  for (const l of after.split("\n")) left.set(l, (left.get(l) ?? 0) + 1);
  return before.split("\n").filter((l) => {
    const n = left.get(l) ?? 0;
    if (n) left.set(l, n - 1);
    return !n;
  });
}

describe("YAML round-trip property test (docs/07 M3)", () => {
  const COMMENTS = W1.split("\n").filter((l) => l.trim().startsWith("#"));

  it.each([false, true])("125 random op sequences keep comments, unrelated lines and line endings (crlf=%s)", (crlf) => {
    const rand = rng(crlf ? 7 : 42);
    const pick = <T,>(xs: T[]) => xs[Math.floor(rand() * xs.length)];
    for (let run = 0; run < 125; run++) {
      let text = crlf ? W1.replace(/\n/g, "\r\n") : W1;
      const added: string[] = [];
      for (let step = 0; step < 8; step++) {
        const ids = toGraph(text).nodes.map((n) => n.id);
        const id = pick(ids);
        const ops: [string, () => string, (l: string) => boolean][] = [
          ["move", () => moveNode(text, id, rand() * 900, rand() * 900), (l) => l.trim().startsWith(`${id}: {x:`)],
          ["timeout", () => setField(text, id, "timeout", `${1 + Math.floor(rand() * 99)}s`), (l) => l.trim().startsWith("timeout:")],
          ["add", () => {
            const nid = freshId(text, "added");
            added.push(nid);
            return addNode(text, { id: nid, type: "action", action: "noop", with: { n: Math.floor(rand() * 9) } });
          }, () => false],
          ["edge", () => addEdge(text, { from: id, to: pick(ids) }), () => false],
        ];
        if (added.length) {
          const victim = pick(added);
          ops.push(["remove", () => {
            added.splice(added.indexOf(victim), 1);
            return removeNode(text, victim);
          }, (l) => l.includes(victim) || /^\s+(type: action|action: noop|with:|n: \d|timeout: \d+s)$/.test(l)]);
        }
        const [name, op, allowed] = pick(ops);
        const prev = text;
        text = op();
        const lf = (s: string) => s.replace(/\r\n/g, "\n");
        const gone = removedLines(lf(prev), lf(text));
        expect(gone.filter((l) => !allowed(l) && l !== "layout:"), `${name} on ${id}, run ${run}`).toEqual([]);
        expect(yamlErrors(text)).toEqual([]);
        if (crlf) expect(text.replace(/\r\n/g, "")).not.toContain("\n");
      }
      const out = text.replace(/\r\n/g, "\n");
      for (const c of COMMENTS) expect(out).toContain(c);
      expect(toGraph(text).nodes.length).toBeGreaterThanOrEqual(8);
    }
  }, 60_000);
});
