"""W3 seed scenario (M6, docs/04): 50 shipments (Acme 30, Bolt 20) on 6 lanes, each with a timeline of carrier
milestones at sim-day offsets, plus 6 scripted incidents. Deterministic (fixed seed, uuid5 ids), so the
committed definitions/seed/shipments.json is reproducible: `uv run python scripts/gen_shipments.py`.

Normal ETA jitter stays within +-6 h, under every threshold (Bolt's ETA rule is 12 h), so only the scripted
incidents become exceptions. The simulator turns day offsets into sim time (1 sim-day = 1 real minute)."""

import json
import random
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "definitions/seed/shipments.json"
NS = uuid.UUID("6f1c3c0e-3a51-4c2a-9f0e-7d1b6b0a5e00")
CARRIERS = {"MSCU": "MSC", "MAEU": "MAERSK", "CMAU": "CMA CGM", "HLCU": "HAPAG"}

# (code, pol, ts, pod, days pol->ts or pol->pod, days ts->pod, carrier prefix)
LANES = [
    ("CNSHA-NLRTM", "CNSHA", "SGSIN", "NLRTM", 6.0, 22.0, "MAEU"),
    ("CNNGB-GBFXT", "CNNGB", None, "GBFXT", 30.0, 0.0, "CMAU"),
    ("VNSGN-USNYC", "VNSGN", "LKCMB", "USNYC", 5.0, 21.0, "MSCU"),
    ("INNSA-DEHAM", "INNSA", None, "DEHAM", 19.0, 0.0, "HLCU"),
    ("KRPUS-USLAX", "KRPUS", None, "USLAX", 11.0, 0.0, "MAEU"),
    ("CNSZX-NLRTM", "CNSZX", "MYPKG", "NLRTM", 5.0, 23.0, "MSCU"),
]
VESSELS = {
    "MAEU": ["MAERSK ESSEN", "MAERSK EMDEN", "MAERSK ELBA"],
    "CMAU": ["CMA CGM MARCO POLO", "CMA CGM ANTOINE", "CMA CGM TAGE"],
    "MSCU": ["MSC GULSUN", "MSC ISABELLA", "MSC OSCAR"],
    "HLCU": ["HAMBURG EXPRESS", "BERLIN EXPRESS", "ESSEN EXPRESS"],
}


def check_digit(owner_serial: str) -> str:
    """ISO 6346: letters map to 10..38 skipping multiples of 11, weights 2^i, mod 11 (10 -> 0)."""
    vals, v = {}, 10
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if v % 11 == 0:
            v += 1
        vals[ch] = v
        v += 1
    total = sum((vals[c] if c.isalpha() else int(c)) * 2**i for i, c in enumerate(owner_serial))
    return str(total % 11 % 10)


def container(prefix: str, n: int) -> str:
    base = f"{prefix}U{(270000 + n * 7919) % 10**6:06d}"
    return base + check_digit(base)


def ev(day: float, event_type: str, location: str, role: str, **kw: Any) -> dict[str, Any]:
    return {"day": round(day, 3), "event_type": event_type, "location": location, "port_role": role, **kw}


def timeline(sh: dict[str, Any], rnd: random.Random, incident: str | None) -> list[dict[str, Any]]:
    """Milestones for one shipment. Every event carries the current ETA (as a day offset) and the booked
    vessel; `incident` bends the timeline for the scripted cases."""
    s, lane = sh["start"], sh["lane_def"]
    _, pol, ts, pod, leg1, leg2, _ = lane
    v1, v2 = sh["vessel"], sh["ts_vessel"]
    eta = sh["eta_day"]
    booked = v1
    out: list[dict[str, Any]] = []

    def add(day: float, kind: str, loc: str, role: str, vessel: str | None = None) -> None:
        out.append(ev(day, kind, loc, role, vessel=vessel, voyage=sh["voyage"] if vessel else None,
                      booked_vessel=booked, eta=round(eta, 3)))  # fmt: skip

    add(s - 0.5, "gate_in", pol, "pol")
    if incident == "ROLLOVER":  # rolled to the next sailing before loading; ETA only +20 h (under 24 h)
        booked, eta = sh["rollover_vessel"], eta + 20 / 24
        add(s - 0.3, "eta_update", pol, "pol")
        v1 = booked
    add(s - 0.1, "loaded", pol, "pol", v1)
    add(s, "departed", pol, "pol", v1)
    jitter = rnd.uniform(-6, 6) / 24  # normal carrier noise, never over a threshold
    eta += jitter
    add(s + 1.0, "eta_update", pol, "pol")
    if incident and incident.startswith("ETA_SLIP"):
        slip_h = {"ETA_SLIP_36": 36, "ETA_SLIP_52": 52, "ETA_SLIP_18": 18}[incident]
        eta += slip_h / 24
        add(s + 2.0, "eta_update", pol, "pol")
    t = s + leg1
    if ts:
        add(t, "arrived", ts, "ts", v1)
        add(t + 0.2, "discharged", ts, "ts", v1)
        if incident == "MISSED_TS":  # the connecting vessel sails without the box; next one 3 days later
            add(t + 1.2, "departed", ts, "ts", v2)
            eta += 10 / 24  # carrier hasn't re-planned yet: +10 h, under Bolt's 12 h
            add(t + 1.3, "eta_update", ts, "ts")
            dwell = 2.9  # reloaded after 2.7 days on the quay: under DWELL's 72 h, so only MISSED_TS fires
            v2 = sh["rollover_vessel"]
        elif incident == "DWELL":  # 4 days on the quay; the carrier still shows the old ETA
            dwell = 4.2
        else:
            dwell = rnd.uniform(1.0, 2.0)
        add(t + dwell, "loaded", ts, "ts", v2)
        add(t + dwell + 0.1, "departed", ts, "ts", v2)
        t = t + dwell + 0.1 + leg2
        last_vessel = v2
    else:
        last_vessel = v1
    add(t, "arrived", pod, "pod", last_vessel)
    add(t + 0.3, "discharged", pod, "pod", last_vessel)
    return sorted(out, key=lambda e: e["day"])


def main() -> None:
    rnd = random.Random(42)  # noqa: S311 (deterministic scenario, not crypto)
    # incidents land on early shipments so the whole set fires within the first ~13 sim-days
    plan = {("acme", 1): "ETA_SLIP_36", ("acme", 3): "ETA_SLIP_52", ("bolt", 1): "ETA_SLIP_18",
            ("acme", 0): "DWELL", ("bolt", 0): "MISSED_TS", ("acme", 2): "ROLLOVER"}  # fmt: skip
    expect = {"ETA_SLIP_36": "ETA_SLIP", "ETA_SLIP_52": "ETA_SLIP", "ETA_SLIP_18": "ETA_SLIP",
              "DWELL": "DWELL", "MISSED_TS": "MISSED_TS", "ROLLOVER": "ROLLOVER"}  # fmt: skip
    shipments, incidents = [], []
    n_global = 0
    for tenant, count in (("acme", 30), ("bolt", 20)):
        for i in range(count):
            incident = plan.get((tenant, i))
            ts_lanes = [ln for ln in LANES if ln[2]]
            # incidents that need a transhipment port get a TS lane; the rest rotate through all six
            lane = ts_lanes[i % len(ts_lanes)] if incident in ("DWELL", "MISSED_TS") else LANES[n_global % 6]
            code, pol, ts, pod, leg1, leg2, carrier = lane
            fleet = VESSELS[carrier]
            start = 0.6 + 0.09 * n_global
            sid = uuid.uuid5(NS, f"{tenant}:{i}")
            sh: dict[str, Any] = {
                "id": str(sid),
                "tenant": tenant,
                "bol_number": f"{carrier}SIM{tenant[0].upper()}{i:03d}",
                "container_no": container(carrier[:3], n_global),
                "carrier": CARRIERS[carrier],
                "lane": code,
                "pol": pol,
                "ts_port": ts,
                "pod": pod,
                "vessel": fleet[n_global % 3],
                "ts_vessel": fleet[(n_global + 1) % 3] if ts else None,
                "rollover_vessel": fleet[(n_global + 2) % 3],
                "voyage": f"{(n_global * 37) % 900 + 100}W",
                "start": round(start, 3),
                "lane_def": lane,
            }
            sh["eta_day"] = round(start + leg1 + (0.1 + 1.5 + leg2 if ts else 0.0), 3)
            sh["events"] = timeline(sh, rnd, incident)
            for k, e in enumerate(sh["events"]):
                e["event_id"] = str(uuid.uuid5(sid, f"{k}:{e['event_type']}:{e['day']}"))
            del sh["lane_def"]
            shipments.append(sh)
            if incident:
                incidents.append({"tenant": tenant, "shipment_id": sh["id"],
                                  "container_no": sh["container_no"], "scripted": incident,
                                  "expect": expect[incident]})  # fmt: skip
            n_global += 1
    doc = {
        "_doc": "W3 seed (docs/04, M6). Generated by scripts/gen_shipments.py; do not edit by hand. "
        "Days are sim-day offsets from sim_epoch; 1 sim-day = 1 real minute in the simulator.",
        "sim_epoch": "2026-10-01T00:00:00Z",
        "lanes": [{"code": c, "pol": p, "ts": t, "pod": d} for c, p, t, d, *_ in LANES],
        "incidents": incidents,
        "shipments": shipments,
    }
    OUT.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    n_events = sum(len(s["events"]) for s in shipments)
    print(f"wrote {len(shipments)} shipments, {n_events} events, {len(incidents)} incidents -> {OUT}")


if __name__ == "__main__":
    main()
