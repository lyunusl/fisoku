"""Turn OCR text of a Turkish receipt (fiş) into structured data and check it.

The parser works line by line on the text rebuilt from OCR boxes and is deliberately
forgiving: real thermal receipts are faded, and OCR confuses "%" with "7", drops
decimal commas and misses labels. The validator is strict: every number that can be
cross-checked is cross-checked, and anything that does not add up goes to review.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
TOLERANCE = Decimal("0.05")
RATES = ("0", "1", "8", "10", "18", "20")  # KDV rates in use since 2007 (8/18 until 2023)

_TR = str.maketrans({"İ": "I", "ı": "I", "Ş": "S", "ş": "S", "Ğ": "G", "ğ": "G",
                     "Ü": "U", "ü": "U", "Ö": "O", "ö": "O", "Ç": "C", "ç": "C", "$": "S"})

# 1.234,56 | 1234,56 | 1 234,56 ; OCR sometimes reads the comma as a dot.
_MONEY_BODY = r"\d{1,3}(?:[.\s]\d{3})+[,.]\d{2}|\d+[,.]\d{2}"
_MONEY = r"(" + _MONEY_BODY + r")(?!\d)"
_MARK = r"[*#xX]"  # printed in front of amounts; OCR often turns "*" into "#"
# "%10", "%.10", and "710"/"810" where OCR read the "%" as a 7 or an 8
_RATE = r"(?:%\s*\.?\s*|(?<![\d,.])[78]\.?|(?<![\d,.]))(1|8|10|18|20)"
# Rates in force at a receipt date: 8 and 18 became 10 and 20 on 10 July 2023.
_RATE_CHANGE = date(2023, 7, 10)
_TOTAL_LABELS = (
    r"GENEL\s*TOPLAM", r"F[I1]S\s*TOPLAMI?", r"TOPLAM\s*TUTAR", r"ODENECEK(?:\s*TUTAR)?",
    r"TOPLAM(?!\s*KDV)", r"TOP(?!\s*KDV|LAM)\b",
)
_VAT_LABELS = (r"[TI1L]OP\s*KDV", r"TOPLAM\s*KDV", r"KDV\s*TOPLAMI?", r"KDV(?!\s*%)")


@dataclass
class Line:
    name: str
    rate: int
    gross: str  # KDV dahil tutar


@dataclass
class Receipt:
    seller_name: str = ""
    vkn: str = ""
    date: str = ""  # ISO yyyy-mm-dd
    time: str = ""
    receipt_no: str = ""
    total: str = ""
    total_vat: str = ""
    vat_by_rate: dict[str, str] = field(default_factory=dict)
    base_by_rate: dict[str, str] = field(default_factory=dict)
    # KDV'li tutar per rate from the receipt's own VAT table ("KDV TUTARI / KDV'LI TOPLAM"),
    # when it has one; more reliable than item lines on long market receipts.
    gross_by_rate: dict[str, str] = field(default_factory=dict)
    lines: list[Line] = field(default_factory=list)
    payment: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Receipt":
        d = dict(d)
        d["lines"] = [Line(**ln) for ln in d.get("lines", [])]
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def money(s: str) -> Decimal | None:
    """'1.491,20' -> Decimal('1491.20'); tolerant to OCR dot/comma confusion."""
    s = s.strip().replace(" ", "")
    if not s:
        return None
    # The last separator before two digits is the decimal mark; the rest are thousands.
    s = s[:-3].replace(".", "").replace(",", "") + "." + s[-2:]
    try:
        return Decimal(s).quantize(CENT)
    except InvalidOperation:
        return None


def _amount(s: str) -> Decimal | None:
    """An amount after a '*' mark: '295,00', or '12000' when OCR dropped the comma."""
    if re.fullmatch(_MONEY, s.strip()):
        return money(s)
    if re.fullmatch(r"\d{3,7}", s.strip()):
        return (Decimal(s.strip()) / 100).quantize(CENT)
    return None


def normalize(text: str) -> str:
    """Upper-case ASCII, one space between words, one receipt line per text line."""
    t = unicodedata.normalize("NFKC", text)  # fullwidth "：" -> ":", ligatures, ...
    t = t.translate(_TR).upper()
    t = re.sub(r"[ \t]*_[ \t]*", " ", t)
    t = re.sub(r"[ \t]+([.,:])", r"\1", t)  # "V.D ." -> "V.D."
    t = re.sub(r"(\d),[ \t]+(\d{2})(?!\d)", r"\1,\2", t)  # "391, 08" -> "391,08"
    t = re.sub(r"[ \t]+", " ", t)
    return "\n".join(ln.strip() for ln in t.splitlines() if ln.strip())


def _last_money(line: str) -> Decimal | None:
    found = re.findall(_MONEY, line)
    if found:
        return money(found[-1])
    m = re.search(_MARK + r"\s*(\d{3,7})\s*$", line)
    return _amount(m.group(1)) if m else None


def _labelled(lines: list[str], labels: tuple[str, ...]) -> tuple[Decimal | None, int | None, str]:
    """Amount on the last line starting with one of `labels` (or on the next line).

    Returns (amount, line index, raw text after the label).
    """
    for pattern in labels:
        for i in range(len(lines) - 1, -1, -1):
            m = re.match(r"(?:" + pattern + r")\s*[:.]?\s*(.*)$", lines[i])
            if not m:
                continue
            rest = m.group(1)
            amt = _last_money(rest)
            if amt is None and i + 1 < len(lines) and re.fullmatch(r"\s*" + _MARK + r"?\s*" + _MONEY + r"\s*", lines[i + 1]):
                amt = _last_money(lines[i + 1])
            return amt, i, rest
    return None, None, ""


def parse(text: str) -> Receipt:
    t = normalize(text)
    lines = t.split("\n")
    r = Receipt()

    # --- seller: the first line that looks like a name (not noise, not mostly digits)
    for ln in lines[:6]:
        letters = sum(c.isalpha() for c in ln)
        if letters >= 4 and letters >= sum(c.isdigit() for c in ln) * 2:
            r.seller_name = ln.strip(" .,-[]|")
            break

    # --- tax number: VKN (10) or TCKN (11), preferring one next to V.D./VKN/VN
    m = re.search(r"(?:V\.?\s?D\.?|VKN|VN|VERGI\s*NO)\D{0,25}?(\d{10,11})(?!\d)", t)
    cand = [m.group(1)] if m else []
    cand += re.findall(r"(?<!\d)(\d{10,11})(?!\d)", t)
    # Printed in groups: "D.383 096 3941"
    cand += [re.sub(r"\s", "", x) for x in re.findall(r"(?<![\d.,])(\d{3}\s\d{3}\s\d{4}|\d{3}\s\d{3}\s\d{2}\s\d{2})(?![\d.,])", t)]
    # An 11-digit run that is not a valid TCKN may be a VKN with a stray digit from the
    # "V.D." text ("D.383..." -> "0383..."); the check digit makes this safe to try.
    cand += [x for c in cand if len(c) == 11 for x in (c[1:], c[:-1])]
    r.vkn = next((c for c in cand if valid_tax_no(c)), cand[0] if cand else "")

    # --- date / time / number
    for pattern in (r"(?<!\d)(\d{2}) ?[./-] ?(\d{2}) ?[./-]? ?(\d{4})(?!\d)",
                    # "/" read as 7 or 1 on faded print: "0770472025" = 07/04/2025
                    r"(?<!\d)(\d{2})[71](\d{2})[71](20\d{2})(?!\d)"):
        for m in re.finditer(pattern, t):
            d, mo, y = map(int, m.groups())
            try:
                r.date = date(y, mo, d).isoformat()
                break
            except ValueError:
                continue
        if r.date:
            break
    m = re.search(r"SAAT\s*[:.]?\s*(\d{2})\s*[:.]?\s*(\d{2})(?!\d)", t)
    if m:
        r.time = f"{m.group(1)}:{m.group(2)}"
    m = re.search(r"F[I1]S\s*NO\s*[:.]?\s*(\d+)", t)
    if m:
        r.receipt_no = m.group(1)

    # --- per-rate VAT rows: "KDV %10 *72,27" or "%10 KDV 72,27"
    used: set[int] = set()
    for i, ln in enumerate(lines):
        # The "%" is required here: without it "TOPKDV 89,43" would read as "%8: 9,43".
        explicit = r"%\s*\.?\s*(1|8|10|18|20)"
        m = re.search(r"KDV\s*" + explicit + r"\s*" + _MARK + r"?\s*" + _MONEY, ln) or \
            re.search(explicit + r"\s*KDV\s*" + _MARK + r"?\s*" + _MONEY, ln)
        if m:
            r.vat_by_rate[m.group(1)] = str(money(m.group(2)))
            used.add(i)

    # --- totals
    total, ti, _ = _labelled(lines, _TOTAL_LABELS)
    if total is None:
        total, ti, _ = _labelled(lines, (r"ARA\s*TOPLAM", r"ARAIOPLAM"))
    if total is not None:
        r.total = str(total)
        used.add(ti)
    vat, vi, vat_rest = _labelled(lines, _VAT_LABELS)
    if vat is not None and vi not in used:
        r.total_vat = str(vat)
        used.add(vi)
    for i, ln in enumerate(lines):
        if re.match(r"ARA\s*TOPLAM|ARAIOPLAM", ln):
            used.add(i)

    # --- the receipt's own VAT table: rows of (KDV, KDV'li tutar). Each row must satisfy
    # KDV = round(tutar * r / (100 + r)) to the kuruş for a rate in force at that date,
    # and the rows must add up to the total; then the table beats any single misread.
    table, rows = _vat_table(lines, r.date)
    total = _dec(r.total)
    if table and total is not None and abs(sum(g for _, g in table.values()) - total) <= TOLERANCE:
        r.vat_by_rate = {k: str(v) for k, (v, _) in table.items()}
        r.gross_by_rate = {k: str(g) for k, (_, g) in table.items()}
        r.total_vat = str(sum(v for v, _ in table.values()))
        used |= rows

    # --- item lines: "<name> %10 *295,00" (also "710 *", "10*", or a rate column "10 285,00")
    prev_name = ""
    for i, ln in enumerate(lines):
        if i in used or re.match(r"(?:TOP|KDV|ARA|NAKIT|KREDI|KART|PARA|FIS|TARIH|SAAT)", ln):
            prev_name = ""
            continue
        m = re.search(r"^(.*?)\s*" + _RATE + r"\s*" + _MARK + r"\s*(" + _MONEY_BODY + r"|\d{3,7})\s*$", ln)
        if not m:
            m = re.search(r"^(.*?)\s*%\s*\.?\s*(1|8|10|18|20)\s+" + _MONEY + r"\s*$", ln)
        if not m:
            m = re.search(r"^([A-Z].*?[A-Z].*?)\s+(?:\d+\s+)?(1|8|10|18|20)\s+" + _MONEY + r"\s*$", ln)
        if m:
            amt = _amount(m.group(3))
            if amt is not None:
                name = (m.group(1).strip(" -*#") or prev_name)[-40:]
                r.lines.append(Line(name=name, rate=int(m.group(2)), gross=str(amt)))
                prev_name = ""
                continue
        prev_name = ln if re.search(r"[A-Z]{3}", ln) and not re.search(r"\d+[,.]\d{2}", ln) else ""

    amounts = [v for v in (money(x) for x in re.findall(_MONEY, t)) if v is not None]

    # --- "TOPLAM" whose amount slid onto another line (crumpled paper): the item lines'
    # sum counts as the total only if it is printed somewhere else too (card slip, etc.).
    if not r.total and r.lines:
        s = sum((g for g in (_dec(ln.gross) for ln in r.lines) if g is not None), Decimal(0))
        if s > 0 and sum(1 for v in amounts if v == s) >= 2:
            r.total = str(s)

    # --- VAT the OCR read without its label, next to the wrong label, or only partly
    # ("KDV TOPLAMI: 25"): the VAT implied by the item lines wins only when that exact
    # amount is printed on the receipt.
    expected = {} if r.gross_by_rate else _vat_from_lines(r)
    if expected:
        exp_total = sum(expected.values(), Decimal(0))
        tv = _dec(r.total_vat)
        if tv is None or abs(tv - exp_total) > TOLERANCE:
            partial = re.fullmatch(r"\s*(\d+)\s*", vat_rest or "") if tv is None else None
            if any(abs(v - exp_total) <= CENT for v in amounts) or \
                    (partial and int(partial.group(1)) == int(exp_total)):
                r.total_vat = str(exp_total)
    if expected and not r.vat_by_rate and r.total_vat and len(expected) == 1:
        r.vat_by_rate = {next(iter(expected)): r.total_vat}

    # A rate whose "KDV %x" label was read but whose amount was not: if it is the only
    # gap, TOPKDV minus the other rates gives it. validate() still cross-checks it
    # against the item lines, so a wrong guess cannot pass silently.
    labels = {str(int(x)) for x in re.findall(r"KDV\s*%\s*(\d{1,2})", t)}
    labels |= {str(ln.rate) for ln in r.lines}
    missing = sorted(labels - set(r.vat_by_rate))
    tv = _dec(r.total_vat)
    if len(missing) == 1 and tv is not None and r.vat_by_rate:
        known = [_dec(v) for v in r.vat_by_rate.values()]
        if None not in known and tv - sum(known, Decimal(0)) > 0:
            r.vat_by_rate[missing[0]] = str(tv - sum(known, Decimal(0)))
    r.vat_by_rate = dict(sorted(r.vat_by_rate.items(), key=lambda kv: int(kv[0])))

    if not r.total_vat and r.vat_by_rate:
        # "TOPKDV" label missed by OCR: the per-rate amounts still give the total.
        vals = [_dec(v) for v in r.vat_by_rate.values()]
        if None not in vals:
            r.total_vat = str(sum(vals, Decimal(0)))

    for word in ("NAKIT", "KREDI KARTI", "K.KARTI", "KART", "BANKA", "POS"):
        if word in t:
            r.payment = "NAKIT" if word == "NAKIT" else "KART"
            break

    fill_bases(r)
    return r


MONEY_PROBLEM_WORDS = ("Toplam", "KDV", "Satır", "satır", "TOPKDV")


def merge(primary: Receipt, others: list[Receipt], today: date | None = None
          ) -> tuple[Receipt, list[str], list[str]]:
    """Fill gaps in the primary read with re-reads of the same photo through other filters.

    Values the primary read already has are never replaced. A gap is filled with a value
    that can be trusted on its own: a VKN/TCKN whose check digit holds, a money block
    (total, VAT, lines) from a re-read that passes every money check, or a date / receipt
    number that two re-reads agree on. A date or receipt number seen in only one re-read
    is filled in too, but reported as unverified so a person confirms it.
    Returns (merged receipt, fields filled, fields filled but unverified).
    """
    r = Receipt.from_dict(primary.to_dict())
    filled: list[str] = []
    unverified: list[str] = []

    if not valid_tax_no(r.vkn):
        for o in others:
            if valid_tax_no(o.vkn):
                r.vkn = o.vkn
                filled.append("vkn")
                break

    def money_ok(x: Receipt) -> bool:
        return bool(x.total) and bool(x.total_vat) and not any(
            w in p for p in validate(x, today) for w in MONEY_PROBLEM_WORDS)

    if not money_ok(r):
        for o in others:
            if money_ok(o):
                for k in ("total", "total_vat", "vat_by_rate", "gross_by_rate", "lines"):
                    setattr(r, k, getattr(o, k))
                filled.append("tutarlar")
                break

    for k in ("date", "receipt_no"):
        if getattr(r, k):
            continue
        seen = [getattr(o, k) for o in others if getattr(o, k)]
        if k == "date":
            seen = [v for v in seen if not [p for p in validate(Receipt(date=v), today) if "Tarih" in p]]
        if not seen:
            continue
        agreed = next((v for v in seen if seen.count(v) >= 2), None)
        setattr(r, k, agreed or seen[0])
        filled.append(k)
        if not agreed:
            unverified.append(k)

    fill_bases(r)
    return r, filled, unverified


UNVERIFIED_NOTE = {
    "date": "Tarih yalnızca bir yedek okumadan geldi; fotoğrafla karşılaştırın.",
    "receipt_no": "Fiş no yalnızca bir yedek okumadan geldi; fotoğrafla karşılaştırın.",
}


def _allowed_rates(iso_date: str) -> tuple[int, ...]:
    try:
        d = date.fromisoformat(iso_date)
    except ValueError:
        d = None
    return (1, 10, 20) if d is None or d >= _RATE_CHANGE else (1, 8, 18)


def _row_rate(vat: Decimal, gross: Decimal, rates: tuple[int, ...]) -> str | None:
    """The rate for which the printed VAT is exactly round(gross * r / (100 + r))."""
    if not (0 < vat < gross):
        return None
    for r in rates:
        if abs((gross * r / (100 + r)).quantize(CENT, ROUND_HALF_UP) - vat) <= CENT:
            return str(r)
    return None


def _vat_table(lines: list[str], iso_date: str) -> tuple[dict[str, tuple[Decimal, Decimal]], set[int]]:
    """Rows with two amounts that form a (VAT, gross) or (base, VAT) pair for a valid rate."""
    rates = _allowed_rates(iso_date)
    table: dict[str, tuple[Decimal, Decimal]] = {}
    rows: set[int] = set()
    pair = re.compile(r"(?:KDV|%\s*\d{1,2})?\s*" + _MARK + r"?\s*" + _MONEY + r"\s*" + _MARK + r"?\s*" + _MONEY + r"\s*")
    for i, ln in enumerate(lines):
        m = pair.fullmatch(ln)
        if not m:
            continue
        a, b = money(m.group(1)), money(m.group(2))
        if a is None or b is None:
            continue
        # (KDV, KDV'li) | (KDV, matrah) | (matrah, KDV) | (KDV'li, KDV)
        for vat, gross in ((a, b), (a, a + b), (b, a + b), (b, a)):
            rate = _row_rate(vat, gross, rates)
            if rate and rate not in table:
                table[rate] = (vat, gross)
                rows.add(i)
                break
    return table, rows


def _vat_from_lines(r: Receipt) -> dict[str, Decimal]:
    """Per-rate VAT implied by the item lines, only if the lines add up to the total."""
    total = _dec(r.total)
    gross = [(_dec(ln.gross), str(ln.rate)) for ln in r.lines]
    if not gross or total is None or any(g is None for g, _ in gross):
        return {}
    if abs(sum((g for g, _ in gross), Decimal(0)) - total) > TOLERANCE:
        return {}
    out: dict[str, Decimal] = {}
    for g, rate in gross:
        out[rate] = out.get(rate, Decimal(0)) + g
    return {k: (v * int(k) / (100 + int(k))).quantize(CENT, ROUND_HALF_UP) for k, v in out.items()}


def fill_bases(r: Receipt) -> None:
    """Matrah per rate = line gross for that rate - printed KDV for that rate.

    Item lines are only trusted when they add up to the receipt total; otherwise the
    base is derived from the VAT amount and rate.
    """
    if r.gross_by_rate and set(r.gross_by_rate) == set(r.vat_by_rate):
        g = {k: _dec(v) for k, v in r.gross_by_rate.items()}
        v = {k: _dec(x) for k, x in r.vat_by_rate.items()}
        if None not in g.values() and None not in v.values():
            r.base_by_rate = {k: str((g[k] - v[k]).quantize(CENT)) for k in g}
            return
    gross: dict[str, Decimal] = {}
    for ln in r.lines:
        g = _dec(ln.gross)
        if g is not None:
            gross[str(ln.rate)] = gross.get(str(ln.rate), Decimal(0)) + g
    total = _dec(r.total)
    if total is None or abs(sum(gross.values(), Decimal(0)) - total) > TOLERANCE:
        gross = {}
    r.base_by_rate = {}
    vats = {k: _dec(v) for k, v in r.vat_by_rate.items()}
    if not vats or None in vats.values():
        return
    if gross and set(vats) <= set(gross):
        for rate, v in vats.items():
            r.base_by_rate[rate] = str((gross[rate] - v).quantize(CENT))
        return
    # No trustworthy lines. Bases add up exactly to (total - total VAT); VAT/rate is
    # precise for high rates but loses up to 0.50 TL at %1, so the lowest rate takes
    # the remainder.
    rates = sorted(vats, key=int)
    for rate in rates:
        if int(rate) > 0:
            r.base_by_rate[rate] = str((vats[rate] * 100 / int(rate)).quantize(CENT, ROUND_HALF_UP))
    tv = _dec(r.total_vat)
    if total is not None and tv is not None and len(rates) > 1:
        rest = total - tv - sum(Decimal(r.base_by_rate[k]) for k in rates[1:])
        if rest >= 0:
            r.base_by_rate[rates[0]] = str(rest.quantize(CENT))


def _dec(s) -> Decimal | None:
    try:
        return Decimal(str(s)).quantize(CENT) if s not in (None, "", "None") else None
    except InvalidOperation:
        return None


# ------------------------------------------------------------------ validation
def valid_vkn(v: str) -> bool:
    if not re.fullmatch(r"\d{10}", v):
        return False
    total = 0
    for i, ch in enumerate(v[:9]):
        tmp = (int(ch) + 9 - i) % 10
        if tmp:
            x = (tmp * (2 ** (9 - i))) % 9
            total += 9 if x == 0 else x
    return (10 - total % 10) % 10 == int(v[9])


def valid_tckn(v: str) -> bool:
    if not re.fullmatch(r"[1-9]\d{10}", v):
        return False
    d = list(map(int, v))
    if ((sum(d[0:9:2]) * 7 - sum(d[1:8:2])) % 10) != d[9]:
        return False
    return sum(d[:10]) % 10 == d[10]


def valid_tax_no(v: str) -> bool:
    return valid_vkn(v) or valid_tckn(v)


def validate(r: Receipt, today: date | None = None) -> list[str]:
    """Return human-readable problems (Turkish). Empty list = the receipt adds up."""
    problems: list[str] = []
    today = today or date.today()

    total = _dec(r.total)
    if total is None:
        problems.append("Toplam tutar okunamadı.")
    if not r.date:
        problems.append("Tarih okunamadı.")
    else:
        d = date.fromisoformat(r.date)
        if d > today or d.year < 2000:
            problems.append(f"Tarih mantıksız: {r.date}.")
    if not r.vkn:
        problems.append("VKN/TCKN okunamadı.")
    elif not valid_tax_no(r.vkn):
        problems.append(f"VKN/TCKN kontrol hanesi tutmuyor: {r.vkn}.")

    vats = {k: _dec(v) for k, v in r.vat_by_rate.items()}
    total_vat = _dec(r.total_vat)
    if vats and None not in vats.values():
        s = sum(vats.values(), Decimal(0))
        if total_vat is not None and abs(s - total_vat) > TOLERANCE:
            problems.append(f"Oran bazlı KDV toplamı ({s}) TOPKDV ile ({total_vat}) tutmuyor.")
    elif total_vat is None:
        problems.append("KDV tutarı okunamadı.")
    if total_vat is not None and total is not None and total_vat >= total > 0:
        problems.append(f"KDV ({total_vat}) toplam tutardan ({total}) büyük olamaz.")
    expected = _vat_from_lines(r)
    if expected and total_vat is not None and not vats:
        e = sum(expected.values(), Decimal(0))
        if abs(e - total_vat) > TOLERANCE * max(len(r.lines), 1):
            problems.append(f"KDV ({total_vat}) satırlardan hesaplanan ({e}) ile tutmuyor.")

    table_ok = bool(r.gross_by_rate) and total is not None and abs(
        sum((_dec(g) or Decimal(0) for g in r.gross_by_rate.values()), Decimal(0)) - total) <= TOLERANCE
    if r.lines and total is not None and not table_ok:
        # A verified VAT table supersedes item lines (long receipts often have two-line
        # items such as "0,622 KG x 210,00 TL/KG" that OCR cannot pair up reliably).
        gross = [_dec(ln.gross) for ln in r.lines]
        if None in gross:
            problems.append("Bazı satır tutarları okunamadı.")
        else:
            s = sum(gross, Decimal(0))
            if abs(s - total) > TOLERANCE:
                problems.append(f"Satırların toplamı ({s}) fiş toplamını ({total}) tutmuyor.")
            for rate, vat in vats.items():
                rg = sum((g for ln, g in zip(r.lines, gross) if str(ln.rate) == rate), Decimal(0))
                expected = (rg * int(rate) / (100 + int(rate))).quantize(CENT, ROUND_HALF_UP)
                n = sum(1 for ln in r.lines if str(ln.rate) == rate)
                if vat is not None and abs(expected - vat) > TOLERANCE * max(n, 1):
                    problems.append(f"%{rate} KDV ({vat}) satırlardan hesaplanan ({expected}) ile tutmuyor.")
    return problems
