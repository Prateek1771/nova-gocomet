import { describe, expect, it } from "vitest";

import { loginCookieName, needsRefresh, safeReturnTo, upstreamPath } from "./auth-utils";

describe("needsRefresh", () => {
  it("refreshes inside the 60 s margin", () => {
    expect(needsRefresh(1_000_000 + 59_999, 1_000_000)).toBe(true);
    expect(needsRefresh(1_000_000 + 60_000, 1_000_000)).toBe(false);
    expect(needsRefresh(0, 1_000_000)).toBe(true);
  });
});

describe("safeReturnTo", () => {
  it.each([
    [null, "/"],
    ["/inbox?x=1", "/inbox?x=1"],
    ["//evil.com", "/"],
    ["/\\evil.com", "/"],
    ["https://evil.com", "/"],
  ])("%s → %s", (input, out) => expect(safeReturnTo(input)).toBe(out));
});

describe("upstreamPath", () => {
  it("encodes segments and rejects dot segments", () => {
    expect(upstreamPath(["runs", "a b"])).toBe("/api/v1/runs/a%20b");
    expect(upstreamPath(["..", "admin"])).toBeNull();
    expect(upstreamPath(["runs", "."])).toBeNull();
  });
});

describe("loginCookieName", () => {
  it("is unique per state and cookie-name safe", () => {
    expect(loginCookieName("abc_-9")).toBe("nova_oidc_abc_-9");
    expect(loginCookieName("a")).not.toBe(loginCookieName("b"));
    expect(loginCookieName("x;y=z")).toBe("nova_oidc_xyz");
  });
});
