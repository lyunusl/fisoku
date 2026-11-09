"""Parser/validator tests on OCR text captured from AugeLab Studio (samples/*.ocr.txt)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from fisoku.receipt import Receipt, money, parse, valid_tckn, valid_vkn, validate

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
TODAY = date(2026, 11, 9)
CASES = sorted(p.stem.removesuffix(".ocr") for p in SAMPLES.glob("*.ocr.txt"))


def test_money():
    assert str(money("1.491,20")) == "1491.20"
    assert str(money("43,73")) == "43.73"
    assert str(money("43.73")) == "43.73"
    assert str(money("12.345.678,90")) == "12345678.90"


def test_tax_numbers():
    assert valid_vkn("5260181599")
    assert not valid_vkn("5260181598")
    assert valid_tckn("10000000146")
    assert not valid_tckn("10000000147")


@pytest.mark.parametrize("name", CASES)
def test_sample(name):
    text = (SAMPLES / f"{name}.ocr.txt").read_text(encoding="utf-8")
    exp = json.loads((SAMPLES / f"{name}.json").read_text(encoding="utf-8"))
    r = parse(text)
    problems = validate(r, today=TODAY)

    assert r.vkn == exp["vkn"]
    assert r.date == exp["date"]
    assert r.receipt_no == exp["receipt_no"]
    assert r.total == exp["total"]
    assert r.total_vat == exp["total_vat"]
    assert r.vat_by_rate == exp["vat_by_rate"]
    if not problems:
        # A receipt that passes validation must be fully right.
        assert r.base_by_rate == exp["base_by_rate"]


def test_bad_ocr_goes_to_review():
    text = (SAMPLES / "fis_01.ocr.txt").read_text(encoding="utf-8").replace("1.491,20", "1.481,20")
    problems = validate(parse(text), today=TODAY)
    assert any("toplam" in p.lower() for p in problems)


@pytest.mark.parametrize("name", CASES)
def test_layout_rebuilds_lines(name):
    from fisoku.layout import lines_from_words

    raw = json.loads((SAMPLES / f"{name}.words.json").read_text(encoding="utf-8"))
    lines = lines_from_words(raw["words"], raw["boxes"])
    assert "\n".join(lines) == (SAMPLES / f"{name}.ocr.txt").read_text(encoding="utf-8")
    assert any(ln.startswith("TOPLAM") for ln in lines)


def test_missing_rate_amount_is_derived_and_checked():
    text = "\n".join([
        "DENEME KIRTASIYE", "Cankaya 4974650753", "TARIH: 06.10.2026",
        "KALEM %20 261,21", "PEYNIR 5006 %1 330,39", "DEFTER A4 %20 57,80", "CAY 1KG %1 114, 02",
        "TOPKDV 57,56", "KDV %1 4,40", "KDV %20 16", "TOPLAM 763,42",
    ])
    r = parse(text)
    assert r.vat_by_rate == {"1": "4.40", "20": "53.16"}
    assert validate(r, today=TODAY) == []
    # A wrong TOPKDV gives a wrong derived amount, which the item lines then reject.
    bad = parse(text.replace("TOPKDV 57,56", "TOPKDV 67,56"))
    assert validate(bad, today=TODAY)


def test_bases_without_lines_add_up_to_net_total():
    from decimal import Decimal

    from fisoku.receipt import fill_bases

    r = Receipt(total="1054.24", total_vat="136.79", vat_by_rate={"1": "2.46", "20": "134.33"})
    fill_bases(r)
    assert r.base_by_rate == {"1": "245.80", "20": "671.65"}
    assert sum(Decimal(v) for v in r.base_by_rate.values()) == Decimal("1054.24") - Decimal("136.79")


def test_real_receipt_formats():
    """Patterns seen on phone photos of thermal receipts (texts are made up)."""
    text = "\n".join([
        "ORNEK KAFE", "ORNEK VERGI DRSI：5260181599", "TARIH ：27/12/2024", "FIS NO ：00023",
        "YIYECEK 810 *350,00",          # "%10" read as "810"
        "IOPKDV *31,82",                # "TOPKDV" read as "IOPKDV"
        "TOPLAM",                       # amount slid to the card slip
        "BANKA/KREDI KARTI *350,00",
    ])
    r = parse(text)
    assert (r.vkn, r.date, r.receipt_no, r.total, r.total_vat) == ("5260181599", "2024-12-27", "00023", "350.00", "31.82")
    assert validate(r, today=TODAY) == []


def test_vat_on_wrong_label_is_replaced_only_by_printed_evidence():
    base = ["ORNEK", "V.D. 5260181599", "12.02.2025", "DIVANE %10 *530,00"]
    good = parse("\n".join(base + ["*48,18", "KDV *530,00", "TOPLAM *530,00"]))
    assert good.total_vat == "48.18" and validate(good, today=TODAY) == []
    bad = parse("\n".join(base + ["KDV *530,00", "TOPLAM *530,00"]))  # 48,18 printed nowhere
    assert any("KDV" in p for p in validate(bad, today=TODAY))


def test_vkn_with_stray_digit():
    assert parse("V.D. 05260181599\nTOPLAM *10,00").vkn == "5260181599"


def test_duplicate_found_even_if_one_copy_misses_fields():
    from fisoku.server import same_receipt

    a = {"vkn": "3830963941", "date": "", "receipt_no": "0003", "total": "530.00"}
    b = {"vkn": "", "date": "", "receipt_no": "0003", "total": "530.00"}
    assert same_receipt(a, b)
    assert not same_receipt(a, {**b, "vkn": "5260181599"})
    assert not same_receipt(a, {**b, "total": "531.00"})


def test_merge_fills_only_trustworthy_gaps():
    from fisoku.receipt import merge

    head = ["ORNEK", "TARIH: 12.02.2025"]
    money_lines = ["DIVANE %10 *530,00", "KDV *48,18", "TOPLAM *530,00"]
    primary = parse("\n".join(head + ["FIS NO: 0003"] + money_lines))                  # no VKN
    bad_vkn = parse("\n".join(["V.D. 6821058495"] + head + money_lines))              # check digit fails
    good_vkn = parse("\n".join(["V.D. 5260181599"] + head + ["FIS NO: 0081"] + money_lines))
    merged, filled, unverified = merge(primary, [bad_vkn, good_vkn], today=TODAY)
    assert merged.vkn == "5260181599" and filled == ["vkn"] and unverified == []
    assert merged.receipt_no == "0003"  # the primary value is never replaced

    no_date = parse("\n".join(["V.D. 5260181599", "FIS NO: 0003"] + money_lines))
    one = parse("\n".join(["TARIH: 07.04.2025"] + money_lines))
    other = parse("\n".join(["TARIH: 01.04.2025"] + money_lines))
    merged, filled, unverified = merge(no_date, [one, other], today=TODAY)
    assert merged.date == "2025-04-07" and unverified == ["date"]  # filled, but a person confirms
    merged, filled, unverified = merge(no_date, [one, one], today=TODAY)
    assert merged.date == "2025-04-07" and "date" in filled and unverified == []


def test_faded_date_and_grouped_vkn():
    r = parse("DOMINO'S\nD383 096 3941\n0770472025 FIS NO:0003\nTOPLAM *10,00")
    assert (r.vkn, r.date, r.receipt_no) == ("3830963941", "2025-04-07", "0003")


def test_roundtrip():
    r = parse((SAMPLES / "fis_01.ocr.txt").read_text(encoding="utf-8"))
    assert Receipt.from_dict(r.to_dict()) == r
