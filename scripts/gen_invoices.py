"""Synthetic freight invoices for W2 (docs/04): renders definitions/seed/invoice_cases.json to PDFs.
INV-5 reuses INV-1's invoice number on a different document (the duplicate), so upload in file order.

Run: uv run python scripts/gen_invoices.py [out_dir]   (default: data/seed/invoice)
"""

import io
import json
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
SEED = json.loads((ROOT / "definitions/seed/invoice_cases.json").read_text(encoding="utf-8"))
CASES = SEED["cases"]
CARRIERS = {
    "MAEU": ("MAERSK A/S", "Esplanaden 50, 1098 Copenhagen K, Denmark"),
    "HLCU": ("HAPAG-LLOYD AG", "Ballindamm 25, 20095 Hamburg, Germany"),
}
W, H = A4
M = 40


def _money(v: float) -> str:
    return f"{v:,.2f}"


def render(case: dict, remarks: str | None = None) -> bytes:
    f = case["fields"]
    name, addr = CARRIERS.get(f["carrier_scac"], (f["carrier_scac"], ""))
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Invoice {f['invoice_no']}")
    # header band
    c.setFillColor(colors.HexColor("#0f172a"))
    c.rect(0, H - 90, W, 90, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(M, H - 50, "FREIGHT INVOICE")
    c.setFont("Helvetica", 9)
    c.drawString(M, H - 68, f"{name} · SCAC {f['carrier_scac']}")
    c.drawRightString(W - M, H - 50, name)
    c.drawRightString(W - M, H - 64, addr)
    c.setFillColor(colors.black)

    # reference block
    y = H - 125
    refs = [
        ("Invoice No", f["invoice_no"]),
        ("Invoice Date", f.get("invoice_date") or ""),
        ("PO Number", f.get("po_number") or ""),
        ("B/L Number", f.get("bol_number") or ""),
        ("Currency", f["currency"]),
    ]
    for i, (k, v) in enumerate(refs):
        x = M + (i % 3) * 175
        yy = y - (i // 3) * 34
        c.setFont("Helvetica-Bold", 7)
        c.setFillColor(colors.HexColor("#475569"))
        c.drawString(x, yy, k.upper())
        c.setFillColor(colors.black)
        c.setFont("Helvetica", 10)
        c.drawString(x, yy - 13, v)
    y -= 80
    c.setFont("Helvetica-Bold", 7)
    c.setFillColor(colors.HexColor("#475569"))
    c.drawString(M, y, "BILL TO")
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 9.5)
    c.drawString(M, y - 13, "Accounts Payable, Customer Logistics Ltd.")
    c.setFont("Helvetica", 8.5)
    c.drawString(M, y - 26, "Containers: " + ", ".join(f.get("container_numbers") or []))

    # charges table
    y -= 55
    cols = [M, M + 50, M + 270, M + 330, M + 420]
    c.setFillColor(colors.HexColor("#e2e8f0"))
    c.rect(M, y - 4, W - 2 * M, 16, fill=1, stroke=0)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 8)
    for x, h in zip(cols, ["Code", "Description", "Qty", "Unit rate", "Amount"], strict=True):
        c.drawString(x + 2, y, h)
    c.setFont("Helvetica", 9)
    for ln in f["lines"]:
        y -= 18
        c.drawString(cols[0] + 2, y, ln["charge_code"])
        c.drawString(cols[1] + 2, y, ln["description"])
        c.drawRightString(cols[2] + 40, y, f"{ln['qty']:g}")
        c.drawRightString(cols[3] + 70, y, _money(ln["unit_rate"]))
        c.drawRightString(W - M - 4, y, _money(ln["amount"]))
    c.setStrokeColor(colors.HexColor("#94a3b8"))
    c.line(M, y - 8, W - M, y - 8)
    y -= 26
    c.setFont("Helvetica", 9)
    c.drawRightString(cols[4] + 40, y, "Subtotal")
    c.drawRightString(W - M - 4, y, _money(f["subtotal"]))
    y -= 18
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(cols[4] + 40, y, "Amount Due")
    c.drawRightString(W - M - 4, y, f"{f['currency']} {_money(f['total'])}")

    c.setFont("Helvetica", 7.5)
    c.setFillColor(colors.HexColor("#64748b"))
    c.drawString(
        M, 60, "Payment terms: 30 days net. Charges as per the rate contract in force on the invoice date."
    )
    if remarks:
        c.drawString(M, 46, f"Remarks: {remarks}")
    c.showPage()
    c.save()
    return buf.getvalue()


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data/seed/invoice"
    out.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        (out / case["file"]).write_bytes(render(case))
    manifest = [{k: case.get(k) for k in ("file", "planted", "expect", "cited_clause")} for case in CASES]
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"wrote {len(CASES)} invoices + manifest.json to {out}")


if __name__ == "__main__":
    main()
