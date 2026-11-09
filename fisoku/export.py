"""Excel export: one row per receipt, VAT and base split by rate."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

RATES = ("1", "10", "20")


def _num(v):
    try:
        return float(Decimal(str(v))) if v not in (None, "") else None
    except InvalidOperation:
        return None


def to_xlsx(receipts: list[dict]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Fisler"
    head = ["No", "Tarih", "Satici", "VKN/TCKN", "Fis No"]
    for r in RATES:
        head += [f"Matrah %{r}", f"KDV %{r}"]
    head += ["Toplam KDV", "Toplam", "Odeme", "Durum", "Kontrol notu", "Fotograf"]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)

    review_fill = PatternFill("solid", fgColor="FFF2CC")
    for rec in sorted(receipts, key=lambda x: (x["data"].get("date") or "", x["id"])):
        if rec["state"] != "done":
            continue
        d = rec["data"]
        row = [rec["id"], d.get("date"), d.get("seller_name"), d.get("vkn"), d.get("receipt_no")]
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
    for col in money_cols:
        for cell in ws.iter_cols(min_col=col, max_col=col, min_row=2):
            for c in cell:
                c.number_format = "#,##0.00"
    widths = [6, 12, 34, 13, 9] + [12] * (2 * len(RATES)) + [12, 12, 8, 11, 50, 22]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
