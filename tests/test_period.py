import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from fisoku import period
from fisoku.export import summary, to_pdf, to_xlsx, to_zip
from fisoku.server import Worker, create_app
from fisoku.store import Store


def rec(i, day=None, created="2025-04-30T10:00:00", total="100.00", vat="16.67", status="ok", state="done"):
    data = {"total": total, "total_vat": vat, "vat_by_rate": {"20": vat}, "base_by_rate": {"20": "83.33"},
            "seller_name": f"Satıcı {i} ŞĞİ", "receipt_no": str(i)}
    if day:
        data["date"] = day
    return {"id": i, "created_at": created, "photo": f"p{i}.jpg", "state": state, "status": status,
            "data": data, "problems": [] if status == "ok" else ["Kontrol"], "ocr_text": "", "error": ""}


RECS = [
    rec(1, "2025-04-07"),
    rec(2, "2025-04-07", total="50.00", vat="8.33", status="review"),
    rec(3, "2025-04-09"),
    rec(4, None, created="2025-05-02T09:00:00"),  # date unreadable: filed under the upload day
    rec(5, "2024-12-27"),
    rec(6, None, created="2025-05-02T09:30:00", state="queued", total=None, vat=None),
]


def test_receipt_day_falls_back_to_upload_day():
    assert period.receipt_day(RECS[0]) == "2025-04-07"
    assert period.receipt_day(RECS[3]) == "2025-05-02"
    assert period.receipt_day({"data": {"date": "2025-02-30"}, "created_at": "2025-03-01T00:00:00"}) == "2025-03-01"


def test_select_and_labels():
    assert [r["id"] for r in period.select(RECS, day="2025-04-07")] == [1, 2]
    assert [r["id"] for r in period.select(RECS, month="2025-04")] == [1, 2, 3]
    assert [r["id"] for r in period.select(RECS, month="2025-05")] == [4, 6]
    assert period.label(day="2025-04-07") == "7 Nisan 2025"
    assert period.label(month="2025-08") == "Ağustos 2025"
    assert period.slug(month="2025-04") == "fisler_2025-04"


def test_check_period_rejects_bad_input():
    for day, month in (("2025-4-7", None), (None, "2025-13"), ("2025-04-07", "2025-04"), ("../x", None)):
        with pytest.raises(ValueError):
            period.check_period(day, month)


def test_archive_groups_months_and_days_newest_first():
    a = period.archive(RECS)
    assert [m["month"] for m in a] == ["2025-05", "2025-04", "2024-12"]
    april = a[1]
    assert april["count"] == 3 and april["review"] == 1
    assert april["total"] == "250.00"
    assert [d["day"] for d in april["days"]] == ["2025-04-09", "2025-04-07"]
    assert april["days"][1]["count"] == 2


def test_summary_per_rate_and_day():
    s = summary(period.select(RECS, month="2025-04"))
    assert s["count"] == 3 and s["review"] == 1
    assert str(s["total"]) == "250.00"
    assert list(s["days"]) == ["2025-04-07", "2025-04-09"]


def test_xlsx_has_totals_and_summary_sheet():
    wb = load_workbook(io.BytesIO(to_xlsx(period.select(RECS, month="2025-04"), "Nisan 2025")))
    assert wb.sheetnames == ["Fişler", "Özet"]
    ws = wb["Fişler"]
    assert ws.cell(ws.max_row, 3).value == "TOPLAM"
    assert ws.max_row == 1 + 3 + 1
    oz = wb["Özet"]
    assert oz["B1"].value == "Nisan 2025"


def test_pdf_and_zip():
    pdf = to_pdf(period.select(RECS, month="2025-04"), "Nisan 2025")
    assert pdf.startswith(b"%PDF") and len(pdf) > 2000


def test_zip_contains_reports_and_photos(tmp_path):
    (tmp_path / "p1.jpg").write_bytes(b"jpg1")
    (tmp_path / "p2.jpg").write_bytes(b"jpg2")
    data = to_zip(period.select(RECS, day="2025-04-07"), tmp_path, "fisler_2025-04-07", "7 Nisan 2025")
    names = sorted(zipfile.ZipFile(io.BytesIO(data)).namelist())
    assert names == ["fisler_2025-04-07.pdf", "fisler_2025-04-07.xlsx",
                     "fotograflar/2025-04-07_0001.jpg", "fotograflar/2025-04-07_0002.jpg"]


def test_export_endpoints(tmp_path):
    store = Store(tmp_path)
    rid = store.add("a.jpg")
    store.update(rid, state="done", status="ok", data={"date": "2025-04-07", "total": "10.00", "total_vat": "1.67"})
    app = create_app(store, Worker(store, "http://127.0.0.1:9/mcp"), 8787)
    c = TestClient(app, client=("127.0.0.1", 5000))
    assert c.get("/api/archive").json()[0]["month"] == "2025-04"
    r = c.get("/api/export.pdf?month=2025-04")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert 'filename="fisler_2025-04.pdf"' in r.headers["content-disposition"]
    assert c.get("/api/export.xlsx?day=2025-04-07").status_code == 200
    assert c.get("/api/export.zip?day=2025-04-07").status_code == 200
    assert c.get("/api/export.xlsx?month=2025-4").status_code == 400
    phone = TestClient(app, client=("192.168.1.20", 5000))
    assert phone.get("/api/export.pdf").status_code == 403
