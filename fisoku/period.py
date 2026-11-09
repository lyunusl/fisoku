"""Day / month archive: which receipts belong to a period, and the archive overview.

A receipt belongs to the day printed on it. Until that date is read (or when it cannot
be read) it is filed under the day it was photographed, so nothing disappears from the
archive.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

MONTHS_TR = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
             "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH = re.compile(r"^\d{4}-\d{2}$")


def receipt_day(rec: dict) -> str:
    """YYYY-MM-DD the receipt is filed under."""
    d = (rec.get("data") or {}).get("date") or ""
    if _DAY.match(d):
        try:
            date.fromisoformat(d)
            return d
        except ValueError:
            pass
    return (rec.get("created_at") or "")[:10]


def check_period(day: str | None, month: str | None) -> tuple[str | None, str | None]:
    """Validated (day, month); at most one of them is set. Raises ValueError otherwise."""
    if day and month:
        raise ValueError("Gün ya da ay seçin, ikisini birden değil.")
    if day:
        if not _DAY.match(day):
            raise ValueError("Gün YYYY-AA-GG biçiminde olmalı.")
        date.fromisoformat(day)
    if month:
        if not _MONTH.match(month):
            raise ValueError("Ay YYYY-AA biçiminde olmalı.")
        date.fromisoformat(month + "-01")
    return day or None, month or None


def select(receipts: list[dict], day: str | None = None, month: str | None = None) -> list[dict]:
    if day:
        return [r for r in receipts if receipt_day(r) == day]
    if month:
        return [r for r in receipts if receipt_day(r).startswith(month + "-")]
    return list(receipts)


def label(day: str | None = None, month: str | None = None) -> str:
    """Human title: '7 Nisan 2025', 'Nisan 2025' or 'Tüm fişler'."""
    if day:
        y, m, d = (int(x) for x in day.split("-"))
        return f"{d} {MONTHS_TR[m - 1]} {y}"
    if month:
        y, m = (int(x) for x in month.split("-"))
        return f"{MONTHS_TR[m - 1]} {y}"
    return "Tüm fişler"


def slug(day: str | None = None, month: str | None = None) -> str:
    """File-name part: fisler_2025-04-07, fisler_2025-04, fisler_tumu."""
    return "fisler_" + (day or month or "tumu")


def _dec(v) -> Decimal:
    try:
        return Decimal(str(v)) if v not in (None, "") else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def _sum(recs: list[dict]) -> dict:
    done = [r for r in recs if r.get("state") == "done"]
    return {
        "count": len(recs),
        "review": sum(1 for r in recs if r.get("state") == "error" or (r.get("state") == "done" and r.get("status") != "ok")),
        "total": str(sum((_dec(r["data"].get("total")) for r in done), Decimal(0))),
        "vat": str(sum((_dec(r["data"].get("total_vat")) for r in done), Decimal(0))),
    }


def archive(receipts: list[dict]) -> list[dict]:
    """Months (newest first), each with its days (newest first) and their totals."""
    by_day: dict[str, list[dict]] = {}
    for r in receipts:
        by_day.setdefault(receipt_day(r), []).append(r)
    months: dict[str, list[str]] = {}
    for d in by_day:
        months.setdefault(d[:7], []).append(d)
    out = []
    for m in sorted(months, reverse=True):
        days = sorted(months[m], reverse=True)
        out.append({
            "month": m,
            "label": label(month=m),
            **_sum([r for d in days for r in by_day[d]]),
            "days": [{"day": d, "label": label(day=d), **_sum(by_day[d])} for d in days],
        })
    return out
