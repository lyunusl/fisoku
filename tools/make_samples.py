"""Generate synthetic Turkish receipt images for tests (no real people, no real tax numbers).

Usage: python tools/make_samples.py
Writes samples/fis_XX.png and samples/fis_XX.json (expected values).
"""
from __future__ import annotations

import json
import random
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples"
FONT = "C:/Windows/Fonts/consola.ttf"

SHOPS = [
    ("ORNEK MARKET GIDA LTD. STI.", "Ataturk Cad. No:12 Kadikoy/ISTANBUL", "Kadikoy V.D."),
    ("DENEME KIRTASIYE", "Cumhuriyet Mah. 45. Sok. No:3 Cankaya/ANKARA", "Cankaya V.D."),
    ("TEST AKARYAKIT A.S.", "E-5 Yan Yol No:88 Gebze/KOCAELI", "Gebze V.D."),
]
ITEMS = [
    ("EKMEK", 1), ("SUT 1L", 1), ("PEYNIR 500G", 1), ("DEFTER A4", 20), ("KALEM", 20),
    ("KAGIT HAVLU", 20), ("SU 5L", 1), ("CAY 1KG", 1), ("BENZIN", 20), ("YAG 1L", 1),
]


def q(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def vkn_with_checksum(first9: str) -> str:
    """Turkish VKN check digit (10 digits)."""
    total = 0
    for i, ch in enumerate(first9):
        d = int(ch)
        tmp = (d + 9 - i) % 10
        if tmp:
            v = (tmp * (2 ** (9 - i))) % 9
            total += 9 if v == 0 else v
    return first9 + str((10 - total % 10) % 10)


def tr_money(x: Decimal) -> str:
    s = f"{x:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def make(idx: int, rng: random.Random) -> None:
    shop, addr, vd = SHOPS[idx % len(SHOPS)]
    vkn = vkn_with_checksum("".join(str(rng.randint(0, 9)) for _ in range(9)))
    day, month = rng.randint(1, 28), rng.randint(1, 10)
    date = f"{day:02d}.{month:02d}.2026"
    time = f"{rng.randint(8, 21):02d}:{rng.randint(0, 59):02d}"
    fis_no = rng.randint(100, 9999)

    lines = []
    for name, rate in rng.sample(ITEMS, rng.randint(3, 6)):
        gross = q(Decimal(rng.randint(500, 45000)) / 100)
        lines.append({"name": name, "rate": rate, "gross": gross})

    vat: dict[int, Decimal] = {}
    base: dict[int, Decimal] = {}
    for ln in lines:
        r = ln["rate"]
        net = q(ln["gross"] * 100 / (100 + r))
        base[r] = base.get(r, Decimal(0)) + net
        vat[r] = vat.get(r, Decimal(0)) + (ln["gross"] - net)
    total = sum((ln["gross"] for ln in lines), Decimal(0))
    total_vat = sum(vat.values(), Decimal(0))

    W = 560
    font = ImageFont.truetype(FONT, 22)
    big = ImageFont.truetype(FONT, 26)
    rows: list[tuple[str, str, ImageFont.FreeTypeFont]] = [
        (shop, "c", big), (addr, "c", font), (f"{vd} {vkn}", "c", font), ("", "", font),
        (f"TARIH: {date}", "l", font), (f"SAAT : {time}", "l", font), (f"FIS NO: {fis_no}", "l", font),
        ("-" * 40, "l", font),
    ]
    for ln in lines:
        rows.append((f"{ln['name']:<20}%{ln['rate']:<3}*{tr_money(ln['gross']):>12}", "l", font))
    rows.append(("-" * 40, "l", font))
    rows.append((f"{'TOPKDV':<24}*{tr_money(total_vat):>12}", "l", font))
    for r in sorted(vat):
        rows.append((f"{'KDV %' + str(r):<24}*{tr_money(vat[r]):>12}", "l", font))
    rows.append((f"{'TOPLAM':<24}*{tr_money(total):>12}", "l", big))
    rows.append(("", "", font))
    rows.append(("NAKIT", "l", font))
    rows.append(("MALI DEGERI YOKTUR - ORNEK FIS", "c", font))

    H = 40 + 34 * len(rows)
    img = Image.new("L", (W, H), 250)
    d = ImageDraw.Draw(img)
    y = 20
    for text, align, f in rows:
        if text:
            w = d.textlength(text, font=f)
            x = (W - w) / 2 if align == "c" else 24
            d.text((x, y), text, fill=20, font=f)
        y += 34

    # Photo look: paper on a table, slight rotation and blur.
    bg = Image.new("RGB", (W + 240, H + 240), (96, 78, 60))
    paper = img.convert("RGB").rotate(rng.uniform(-3, 3), expand=True, fillcolor=(96, 78, 60))
    bg.paste(paper, (120 - (paper.width - W) // 2, 120 - (paper.height - H) // 2))
    bg = bg.filter(ImageFilter.GaussianBlur(0.6))

    OUT.mkdir(parents=True, exist_ok=True)
    name = f"fis_{idx + 1:02d}"
    bg.save(OUT / f"{name}.jpg", quality=88)
    expected = {
        "seller_name": shop,
        "vkn": vkn,
        "date": f"2026-{month:02d}-{day:02d}",
        "receipt_no": str(fis_no),
        "total": str(total),
        "total_vat": str(q(total_vat)),
        "vat_by_rate": {str(r): str(q(vat[r])) for r in sorted(vat)},
        "base_by_rate": {str(r): str(q(base[r])) for r in sorted(base)},
    }
    (OUT / f"{name}.json").write_text(json.dumps(expected, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    rng = random.Random(7)
    for i in range(6):
        make(i, rng)
    print(f"wrote {OUT}")
