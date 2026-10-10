"""Synthetic Bills of Lading for W1 (docs/04): renders definitions/seed/bol_cases.json to PDFs.
Case 10 is rasterised, blurred and saved image-only (no text layer) to force the vision path.

Run: uv run python scripts/gen_bols.py [out_dir]   (default: data/seed/bol)
"""

import csv
import io
import json
import random
import sys
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "definitions/seed/bol_cases.json").read_text(encoding="utf-8"))["cases"]
with (ROOT / "definitions/reference/locodes.csv").open(encoding="utf-8") as f:
    PORTS = {r["locode"]: r["name"] for r in csv.DictReader(f)}
PORTS.setdefault("INXNP", "Nagpur ICD")
CARRIERS = {
    "MAEU": "MAERSK LINE",
    "MSCU": "MEDITERRANEAN SHIPPING CO.",
    "CMDU": "CMA CGM",
    "HLCU": "HAPAG-LLOYD AG",
}
W, H = A4
M = 36  # margin
MONTHS = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()


def _date(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{int(d):02d} {MONTHS[int(m) - 1]} {y}"


def _box(c: canvas.Canvas, x: float, y: float, w: float, h: float, label: str) -> None:
    c.setStrokeColor(colors.HexColor("#1f2937"))
    c.setLineWidth(0.6)
    c.rect(x, y - h, w, h)
    c.setFont("Helvetica-Bold", 6.5)
    c.setFillColor(colors.HexColor("#475569"))
    c.drawString(x + 4, y - 9, label.upper())
    c.setFillColor(colors.black)


def _party(c: canvas.Canvas, y: float, label: str, value: str) -> float:
    name, _, addr = value.partition(", ")
    _box(c, M, y, W - 2 * M, 44, label)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(M + 6, y - 22, name)
    c.setFont("Helvetica", 8.5)
    c.drawString(M + 6, y - 35, addr)
    return y - 44


def render(case: dict, remarks: str | None = None) -> bytes:
    f = case["fields"]
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Bill of Lading {f['bol_number']}")
    # header
    c.setFillColor(colors.HexColor("#0f172a"))
    c.rect(0, H - 64, W, 64, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(M, H - 40, "BILL OF LADING")
    c.setFont("Helvetica", 8)
    c.drawString(M, H - 54, "Shipped on board in apparent good order and condition - non negotiable copy")
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(W - M, H - 36, CARRIERS[f["carrier_scac"]])
    c.setFont("Helvetica", 8.5)
    c.drawRightString(W - M, H - 50, f"SCAC: {f['carrier_scac']}")
    c.setFillColor(colors.black)

    y = H - 80
    _box(c, M, y, 260, 30, "B/L No.")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(M + 6, y - 24, f["bol_number"])
    _box(c, M + 270, y, W - 2 * M - 270, 30, "Booking Ref.")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(M + 276, y - 24, f["booking_ref"])
    y -= 40
    y = _party(c, y, "Shipper", f["shipper"]) - 6
    y = _party(c, y, "Consignee", f["consignee"]) - 6
    y = _party(c, y, "Notify Party", f["notify_party"]) - 10

    third = (W - 2 * M - 20) / 3
    for i, (label, val) in enumerate(
        (
            ("Vessel", f["vessel"]),
            ("Voyage No.", f["voyage"]),
            ("Freight", f"FREIGHT {f['freight_terms'].upper()}"),
        )
    ):
        _box(c, M + i * (third + 10), y, third, 30, label)
        c.setFont("Helvetica", 9)
        c.drawString(M + i * (third + 10) + 6, y - 23, val)
    y -= 40
    half = (W - 2 * M - 10) / 2
    for i, (label, code) in enumerate((("Port of Loading", f["pol"]), ("Port of Discharge", f["pod"]))):
        _box(c, M + i * (half + 10), y, half, 30, label)
        c.setFont("Helvetica", 9)
        c.drawString(M + i * (half + 10) + 6, y - 23, f"{code} - {PORTS.get(code, 'Unknown')}")
    y -= 44

    # cargo table
    cols = [
        ("Container No.", 78),
        ("Seal No.", 58),
        ("Pkgs", 36),
        ("Description of Goods", 205),
        ("HS Code", 52),
        ("Gross Wt (kg)", 0),
    ]
    cols[-1] = (cols[-1][0], W - 2 * M - sum(w for _, w in cols[:-1]))
    rows = len(f["container_numbers"])
    th = 16 + 16 * rows
    _box(c, M, y, W - 2 * M, th, "")
    x = M
    c.setFont("Helvetica-Bold", 7)
    for label, w in cols:
        c.drawString(x + 4, y - 11, label)
        x += w
    c.line(M, y - 15, W - M, y - 15)
    c.setFont("Helvetica", 8)
    for r in range(rows):
        line = f["cargo_lines"][r]
        vals = [
            f["container_numbers"][r],
            f["seal_numbers"][r],
            str(line["packages"]),
            line["description"],
            line["hs_code"],
            f"{line['weight_kg']:,.1f}",
        ]
        x, ry = M, y - 27 - 16 * r
        for (_, w), v in zip(cols, vals, strict=True):
            c.drawString(x + 4, ry, v)
            x += w
    y -= th + 8
    c.setFont("Helvetica-Bold", 9)
    c.drawString(M, y - 10, f"Total packages: {f['package_count']}")
    c.drawString(M + 200, y - 10, f"Total gross weight: {f['gross_weight_kg']:,.1f} KGS")
    y -= 30
    c.setFont("Helvetica", 9)
    issue_place = PORTS.get(f["pol"], f["pol"])
    c.drawString(M, y, f"Place and date of issue: {issue_place}, {_date(f['issue_date'])}")
    if remarks:  # red-team: text printed on the document itself (scripts/redteam_bols.py)
        c.setFont("Helvetica", 8)
        for i, line in enumerate(remarks.splitlines()):
            c.drawString(M, y - 18 - 11 * i, ("Remarks: " if i == 0 else "") + line)
    c.setFont("Helvetica-Oblique", 7)
    c.setFillColor(colors.HexColor("#64748b"))
    c.drawString(M, 40, "Synthetic document generated for the Nova prototype. Not a negotiable instrument.")
    c.showPage()
    c.save()
    return buf.getvalue()


def scan(pdf_bytes: bytes, smudge: list[str] | None = None) -> bytes:
    """Photocopy look: rasterise at ~110 dpi, tilt, blur, grain. No text layer survives. `smudge`: printed
    strings covered by an ink stain first, so the copy genuinely can't be read there (the planted scan)."""
    scale = 1.55
    pdf = pdfium.PdfDocument(pdf_bytes)
    page, blots = pdf[0], []
    text, height = page.get_textpage(), page.get_height()
    for target in smudge or []:
        hit = text.search(target).get_next()
        if hit:
            boxes = [
                text.get_charbox(i) for i in range(hit[0], hit[0] + hit[1])
            ]  # (left, bottom, right, top)
            left, right = min(b[0] for b in boxes), max(b[2] for b in boxes)
            top, bottom = height - max(b[3] for b in boxes), height - min(b[1] for b in boxes)
            blots.append((left * scale - 6, top * scale - 5, right * scale + 6, bottom * scale + 5))
    img = page.render(scale=scale).to_pil().convert("L")
    pdf.close()
    draw = ImageDraw.Draw(img)
    for blot in blots:
        draw.ellipse(blot, fill=55)
    img = img.rotate(0.8, expand=True, fillcolor=255).filter(ImageFilter.GaussianBlur(1.1))
    rnd = random.Random(10)  # noqa: S311 (deterministic speckle, not crypto)
    px = img.load()
    for _ in range(img.width * img.height // 40):  # sparse speckle
        x, y = rnd.randrange(img.width), rnd.randrange(img.height)
        px[x, y] = rnd.choice((90, 140, 200))
    out = io.BytesIO()
    img.save(out, "PDF", resolution=110)
    return out.getvalue()


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data/seed/bol"
    out.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        data = render(case)
        if case["scan"]:
            data = scan(data, case.get("smudge"))
        (out / case["file"]).write_bytes(data)
    manifest = [
        {k: case[k] for k in ("file", "planted", "expected_issues", "expect_review", "scan")}
        for case in CASES
    ]
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"wrote {len(CASES)} BoLs + manifest.json to {out}")


if __name__ == "__main__":
    Image.MAX_IMAGE_PIXELS = None
    main()
