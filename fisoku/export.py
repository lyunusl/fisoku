"""Downloads for a period (all, one month or one day): Excel, PDF and a ZIP archive.

Excel: one row per receipt with base and VAT split by rate, a totals row, and a summary
sheet (per rate, and per day). PDF: the same list as a printable report. ZIP: both files
plus the receipt photos, named by date.
"""
from __future__ import annotations

import os
import zipfile
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from .period import receipt_day

RATES = ("1", "10", "20")


def _num(v):
    try:
        return float(Decimal(str(v))) if v not in (None, "") else None
    except InvalidOperation:
        return None


def _dec(v) -> Decimal:
    try:
        return Decimal(str(v)) if v not in (None, "") else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def _done(receipts: list[dict]) -> list[dict]:
    return sorted((r for r in receipts if r["state"] == "done"), key=lambda r: (receipt_day(r), r["id"]))


def summary(receipts: list[dict]) -> dict:
    """Totals of the read receipts: per rate (base, VAT), overall, and per day."""
    done = _done(receipts)
    rates = {
        r: {"base": sum((_dec(x["data"].get("base_by_rate", {}).get(r)) for x in done), Decimal(0)),
            "vat": sum((_dec(x["data"].get("vat_by_rate", {}).get(r)) for x in done), Decimal(0))}
        for r in RATES
    }
    days: dict[str, dict] = {}
    for x in done:
        d = days.setdefault(receipt_day(x), {"count": 0, "vat": Decimal(0), "total": Decimal(0)})
        d["count"] += 1
        d["vat"] += _dec(x["data"].get("total_vat"))
        d["total"] += _dec(x["data"].get("total"))
    return {
        "count": len(done),
        "review": sum(1 for x in done if x["status"] != "ok"),
        "unread": sum(1 for x in receipts if x["state"] != "done"),
        "rates": rates,
        "vat": sum((_dec(x["data"].get("total_vat")) for x in done), Decimal(0)),
        "total": sum((_dec(x["data"].get("total")) for x in done), Decimal(0)),
        "days": dict(sorted(days.items())),
    }


def to_xlsx(receipts: list[dict], title: str = "Tüm fişler") -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Fişler"
    head = ["No", "Tarih", "Satıcı", "VKN/TCKN", "Fiş No"]
    for r in RATES:
        head += [f"Matrah %{r}", f"KDV %{r}"]
    head += ["Toplam KDV", "Toplam", "Ödeme", "Durum", "Kontrol notu", "Fotoğraf"]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)

    review_fill = PatternFill("solid", fgColor="FFF2CC")
    done = _done(receipts)
    for rec in done:
        d = rec["data"]
        row = [rec["id"], d.get("date") or receipt_day(rec), d.get("seller_name"), d.get("vkn"), d.get("receipt_no")]
        for r in RATES:
            row += [_num(d.get("base_by_rate", {}).get(r)), _num(d.get("vat_by_rate", {}).get(r))]
        row += [
            _num(d.get("total_vat")),
            _num(d.get("total")),
            d.get("payment"),
            "Tamam" if rec["status"] == "ok" else "Kontrol et",
            " ".join(rec["problems"]),
            rec["photo"],
        ]
        ws.append(row)
        if rec["status"] != "ok":
            for c in ws[ws.max_row]:
                c.fill = review_fill

    money_cols = range(6, 6 + 2 * len(RATES) + 2)
    if done:
        last = ws.max_row
        total_row = ["", "", "TOPLAM", "", ""] + [
            f"=SUM({get_column_letter(c)}2:{get_column_letter(c)}{last})" for c in money_cols
        ]
        ws.append(total_row)
        for c in ws[ws.max_row]:
            c.font = Font(bold=True)
    for col in money_cols:
        for cells in ws.iter_cols(min_col=col, max_col=col, min_row=2):
            for c in cells:
                c.number_format = "#,##0.00"
    widths = [6, 12, 34, 13, 9] + [12] * (2 * len(RATES)) + [12, 12, 8, 11, 50, 22]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"

    # Summary sheet: what an accountant needs for the period at a glance.
    s = summary(receipts)
    sm = wb.create_sheet("Özet")
    sm.append(["Dönem", title])
    sm.append(["Okunan fiş", s["count"]])
    sm.append(["Kontrol bekleyen", s["review"]])
    if s["unread"]:
        sm.append(["Okunmamış (dışa aktarılmadı)", s["unread"]])
    sm.append([])
    sm.append(["KDV oranı", "Matrah", "KDV"])
    for c in sm[sm.max_row]:
        c.font = Font(bold=True)
    for r in RATES:
        sm.append([f"%{r}", float(s["rates"][r]["base"]), float(s["rates"][r]["vat"])])
    sm.append(["Toplam", float(sum(v["base"] for v in s["rates"].values())), float(s["vat"])])
    sm.append(["Genel toplam (KDV dahil)", "", float(s["total"])])
    for c in (sm[sm.max_row - 1] + sm[sm.max_row]):
        c.font = Font(bold=True)
    if len(s["days"]) > 1:
        sm.append([])
        sm.append(["Gün", "Fiş", "KDV", "Toplam"])
        for c in sm[sm.max_row]:
            c.font = Font(bold=True)
        for d, v in s["days"].items():
            sm.append([d, v["count"], float(v["vat"]), float(v["total"])])
    for row in sm.iter_rows(min_row=1):
        for c in row[1:]:
            if isinstance(c.value, float):
                c.number_format = "#,##0.00"
    sm.column_dimensions["A"].width = 30
    for col in "BCD":
        sm.column_dimensions[col].width = 16

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- PDF

def _tl(v: Decimal | float | str | None) -> str:
    """1234.5 -> '1.234,50'"""
    if v in (None, ""):
        return ""
    s = f"{_dec(v):,.2f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def _font() -> tuple[str, str]:
    """A font with Turkish letters: Windows' Arial; Helvetica (no ş/ğ/ı) elsewhere."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for regular, bold in (("arial.ttf", "arialbd.ttf"), ("segoeui.ttf", "segoeuib.ttf"), ("calibri.ttf", "calibrib.ttf")):
        if (fonts / regular).is_file() and (fonts / bold).is_file():
            try:
                pdfmetrics.registerFont(TTFont("Fisoku", str(fonts / regular)))
                pdfmetrics.registerFont(TTFont("Fisoku-Bold", str(fonts / bold)))
                return "Fisoku", "Fisoku-Bold"
            except Exception:
                continue
    return "Helvetica", "Helvetica-Bold"


def to_pdf(receipts: list[dict], title: str = "Tüm fişler") -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    regular, bold = _font()
    p = ParagraphStyle("p", fontName=regular, fontSize=9, leading=11)
    h = ParagraphStyle("h", fontName=bold, fontSize=16, leading=20, spaceAfter=2)
    small = ParagraphStyle("s", fontName=regular, fontSize=8, leading=10, textColor=colors.HexColor("#555555"))
    s = summary(receipts)
    done = _done(receipts)

    story = [
        Paragraph(f"Fiş dökümü: {title}", h),
        Paragraph(f"{s['count']} fiş · Toplam KDV {_tl(s['vat'])} ₺ · Genel toplam {_tl(s['total'])} ₺"
                  + (f" · <font color='#a16207'>{s['review']} fiş kontrol bekliyor</font>" if s["review"] else ""), p),
        Paragraph(f"Oluşturma: {datetime.now():%d.%m.%Y %H:%M} · Fisoku", small),
        Spacer(1, 5 * mm),
    ]

    head = ["Tarih", "Satıcı", "VKN/TCKN", "Fiş No"] + [f"KDV %{r}" for r in RATES] + ["Top. KDV", "Toplam", "Durum"]
    rows = [head]
    review_rows = []
    for i, rec in enumerate(done, start=1):
        d = rec["data"]
        day = d.get("date") or receipt_day(rec)
        rows.append([
            ".".join(reversed(day.split("-"))) if day else "",
            Paragraph((d.get("seller_name") or "")[:60], p),
            d.get("vkn") or "",
            d.get("receipt_no") or "",
            *[_tl(d.get("vat_by_rate", {}).get(r)) for r in RATES],
            _tl(d.get("total_vat")),
            _tl(d.get("total")),
            "Tamam" if rec["status"] == "ok" else "Kontrol et",
        ])
        if rec["status"] != "ok":
            review_rows.append(i)
    rows.append(["", "TOPLAM", "", "", *[_tl(s["rates"][r]["vat"]) for r in RATES], _tl(s["vat"]), _tl(s["total"]), ""])

    widths = [22 * mm, 70 * mm, 26 * mm, 18 * mm] + [20 * mm] * len(RATES) + [22 * mm, 25 * mm, 20 * mm]
    t = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("FONT", (0, 0), (-1, -1), regular, 8.5),
        ("FONT", (0, 0), (-1, 0), bold, 8.5),
        ("FONT", (0, -1), (-1, -1), bold, 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (4, 0), (-2, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#d4d4d8")),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for i in review_rows:
        style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fff2cc")))
    t.setStyle(TableStyle(style))
    story.append(t)

    # Per-rate summary (and per day for longer periods).
    story += [Spacer(1, 6 * mm), Paragraph("KDV özeti", ParagraphStyle("h2", parent=h, fontSize=11, leading=14))]
    rs = [["Oran", "Matrah", "KDV"]] + [[f"%{r}", _tl(s["rates"][r]["base"]), _tl(s["rates"][r]["vat"])] for r in RATES]
    rs.append(["Toplam", _tl(sum(v["base"] for v in s["rates"].values())), _tl(s["vat"])])
    rt = Table(rs, colWidths=[30 * mm, 35 * mm, 35 * mm], hAlign="LEFT")
    rt.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), regular, 9), ("FONT", (0, 0), (-1, 0), bold, 9), ("FONT", (0, -1), (-1, -1), bold, 9),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black),
        ("LINEABOVE", (0, -1), (-1, -1), 0.5, colors.black),
    ]))
    story.append(rt)
    if len(s["days"]) > 1:
        story += [Spacer(1, 5 * mm), Paragraph("Günlere göre", ParagraphStyle("h3", parent=h, fontSize=11, leading=14))]
        ds = [["Gün", "Fiş", "KDV", "Toplam"]] + [
            [".".join(reversed(d.split("-"))), str(v["count"]), _tl(v["vat"]), _tl(v["total"])] for d, v in s["days"].items()
        ]
        dt = Table(ds, colWidths=[30 * mm, 15 * mm, 30 * mm, 35 * mm], hAlign="LEFT", repeatRows=1)
        dt.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, -1), regular, 9), ("FONT", (0, 0), (-1, 0), bold, 9),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black),
        ]))
        story.append(dt)
    if s["unread"]:
        story += [Spacer(1, 4 * mm), Paragraph(f"Henüz okunmamış {s['unread']} fiş bu dökümde yok.", small)]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(regular, 8)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawRightString(doc.pagesize[0] - 12 * mm, 8 * mm, f"Sayfa {doc.page}")
        canvas.drawString(12 * mm, 8 * mm, f"Fisoku · {title}")
        canvas.restoreState()

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=14 * mm, title=f"Fiş dökümü: {title}", author="Fisoku")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def to_zip(receipts: list[dict], photos: Path, name: str, title: str) -> bytes:
    """Excel + PDF + the photos (named date_no.jpg) in one archive."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{name}.xlsx", to_xlsx(receipts, title))
        z.writestr(f"{name}.pdf", to_pdf(receipts, title))
        for rec in sorted(receipts, key=lambda r: (receipt_day(r), r["id"])):
            src = photos / rec["photo"]
            if src.is_file():
                z.write(src, f"fotograflar/{receipt_day(rec)}_{rec['id']:04d}.jpg", compress_type=zipfile.ZIP_STORED)
    return buf.getvalue()
