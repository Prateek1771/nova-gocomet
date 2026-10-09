"""Deterministic validator checks (09 §5): pure functions over extracted fields + context, registered by
code. They run before any LLM; codes double as eval labels (ADR-013). Severity is the default here and
TenantConfig `check_severity` may override it per code."""

import csv
import os
import string
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from nova_core.registry import Registry

DEFINITIONS = Path(os.environ.get("DEFINITIONS_DIR", Path(__file__).resolve().parents[4] / "definitions"))


@dataclass
class Issue:
    code: str
    field: str
    severity: str
    message: str
    evidence: list[dict[str, Any]] = dc_field(default_factory=list)


@dataclass
class Ctx:
    booking: dict[str, Any] | None = None
    locodes: frozenset[str] = frozenset()


@dataclass
class Check:
    code: str
    severity: str
    fn: Callable[[dict[str, Any], Ctx], list[tuple[str, str]]]  # -> [(field, message)]


CHECKS: Registry[Check] = Registry("check")


def check(code: str, severity: str) -> Callable[..., Any]:
    def deco(fn: Callable[[dict[str, Any], Ctx], list[tuple[str, str]]]) -> Any:
        CHECKS.register(code)(Check(code, severity, fn))
        return fn

    return deco


@lru_cache
def locodes() -> frozenset[str]:
    with (DEFINITIONS / "reference" / "locodes.csv").open(encoding="utf-8") as f:
        return frozenset(row["locode"].strip().upper() for row in csv.DictReader(f))


# --- ISO 6346 ---------------------------------------------------------------------------------------


def _letter_values() -> dict[str, int]:
    vals, v = {}, 10
    for ch in string.ascii_uppercase:
        if v % 11 == 0:  # multiples of 11 are skipped
            v += 1
        vals[ch] = v
        v += 1
    return vals


_LETTERS = _letter_values()


def iso6346_ok(number: str) -> bool:
    n = number.replace(" ", "").replace("-", "").upper()
    if len(n) != 11 or not n[:4].isalpha() or not n[4:].isdigit():
        return False
    total = sum((_LETTERS[c] if c.isalpha() else int(c)) * 2**i for i, c in enumerate(n[:10]))
    return bool(total % 11 % 10 == int(n[10]))


@check("CNTR_CHECK_DIGIT", "high")
def cntr_check_digit(f: dict[str, Any], ctx: Ctx) -> list[tuple[str, str]]:
    return [
        (f"container_numbers[{i}]", f"{c} fails the ISO 6346 check digit")
        for i, c in enumerate(f.get("container_numbers") or [])
        if not iso6346_ok(c)
    ]


@check("LOCODE_UNKNOWN", "high")
def locode_unknown(f: dict[str, Any], ctx: Ctx) -> list[tuple[str, str]]:
    known = ctx.locodes or locodes()
    return [
        (k, f"{f[k]!r} is not a known UN/LOCODE")
        for k in ("pol", "pod")
        if f.get(k) and str(f[k]).replace(" ", "").upper() not in known
    ]


def _party_name(s: str) -> str:
    return s.split(",")[0]  # extraction holds "name, address"; bookings hold the name


@check("BOOKING_MISMATCH", "high")
def booking_mismatch(f: dict[str, Any], ctx: Ctx) -> list[tuple[str, str]]:
    if not f.get("booking_ref"):
        return []
    b = ctx.booking
    if b is None:
        return [("booking_ref", f"booking {f['booking_ref']} not found")]
    out = []
    n = len(f.get("container_numbers") or [])
    if n != b["container_count"]:
        out.append(("container_numbers", f"{n} containers on the BoL, booking has {b['container_count']}"))
    if f.get("pod") and str(f["pod"]).upper() != str(b["pod"]).upper():
        out.append(("pod", f"POD {f['pod']} differs from booking POD {b['pod']}"))
    if f.get("consignee") and fuzz.token_set_ratio(_party_name(f["consignee"]), b["consignee"]) < 85:
        out.append(("consignee", f"consignee differs from booking ({b['consignee']})"))
    return out


@check("WEIGHT_SUM", "medium")
def weight_sum(f: dict[str, Any], ctx: Ctx) -> list[tuple[str, str]]:
    lines = [ln.get("weight_kg") for ln in f.get("cargo_lines") or []]
    gross = f.get("gross_weight_kg")
    if not lines or gross is None or any(w is None for w in lines):
        return []
    total = sum(lines)
    if abs(total - gross) > max(1.0, 0.005 * gross):  # tolerance: rounding on the printed lines
        return [("gross_weight_kg", f"line weights sum to {total:,.1f} kg, gross says {gross:,.1f} kg")]
    return []


@check("DATE_ORDER", "medium")
def date_order(f: dict[str, Any], ctx: Ctx) -> list[tuple[str, str]]:
    if not f.get("issue_date") or not ctx.booking:
        return []
    issued, booked = date.fromisoformat(f["issue_date"]), ctx.booking["booking_date"]
    if isinstance(booked, str):
        booked = date.fromisoformat(booked)
    if issued < booked:
        return [("issue_date", f"issued {issued} before the booking date {booked}")]
    return []


@check("HS_FORMAT", "low")
def hs_format(f: dict[str, Any], ctx: Ctx) -> list[tuple[str, str]]:
    out = []
    for i, code in enumerate(f.get("hs_codes") or []):
        digits = str(code).replace(".", "").replace(" ", "")
        if not (digits.isdigit() and 6 <= len(digits) <= 10):
            out.append((f"hs_codes[{i}]", f"HS code {code!r} is not 6-10 digits"))
    return out


def run(codes: list[str], f: dict[str, Any], ctx: Ctx, severity: dict[str, str] | None = None) -> list[Issue]:
    """Run the deterministic checks in `codes` (unknown or LLM codes are skipped by the caller)."""
    severity = severity or {}
    issues = []
    for code in codes:
        if code not in CHECKS:
            continue
        c = CHECKS[code]
        for fld, msg in c.fn(f, ctx):
            issues.append(Issue(code, fld, severity.get(code, c.severity), msg))
    return issues
