"""Pengenal & normalisasi nilai (tanggal, uang, NPWP, nomor dokumen). Semua regex di sini tetap (bukan dari klien)
dan linear: tanpa quantifier bersarang, input dipotong per baris (MAX_TEXT)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

MAX_TEXT = 500  # panjang teks maksimum yang dicocokkan per baris

MONTHS = {
    "jan": 1, "januari": 1, "january": 1, "feb": 2, "februari": 2, "february": 2, "pebruari": 2,
    "mar": 3, "maret": 3, "march": 3, "apr": 4, "april": 4, "mei": 5, "may": 5,
    "jun": 6, "juni": 6, "june": 6, "jul": 7, "juli": 7, "july": 7, "agu": 8, "agt": 8, "ags": 8, "agus": 8,
    "agustus": 8, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "okt": 10, "oktober": 10,
    "oct": 10, "october": 10, "nov": 11, "nop": 11, "nopember": 11, "november": 11, "des": 12, "desember": 12,
    "dec": 12, "december": 12,
}
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))


@dataclass
class Match:
    value: object  # nilai ternormalisasi (str / list)
    start: int
    end: int
    raw: str


def _cut(text: str) -> str:
    return text[:MAX_TEXT]


# ------------------------------------------------------------------------------------------------ tanggal
_D_NUM = re.compile(r"(?<!\d)(\d{1,2})\s?[/.\-]\s?(\d{1,2})\s?[/.\-]\s?(\d{4}|\d{2})(?!\d)")
_D_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_D_TXT = re.compile(rf"(?<!\d)(\d{{1,2}})\s*[-\s.]?\s*({_MONTH_RE})\.?\s*[-\s.,]?\s*(\d{{4}})(?!\d)", re.I)
_D_TXT_US = re.compile(rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})(?!\d)", re.I)
_MONTH_YEAR = re.compile(rf"\b({_MONTH_RE})\.?\s+(\d{{4}})(?!\d)", re.I)


def _mk_date(y: int, m: int, d: int) -> str | None:
    if y < 100:
        y += 2000
    try:
        if not 1990 <= y <= 2100:
            return None
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def find_dates(text: str) -> list[Match]:
    text = _cut(text)
    out: list[Match] = []
    for m in _D_ISO.finditer(text):
        v = _mk_date(int(m[1]), int(m[2]), int(m[3]))
        if v:
            out.append(Match(v, m.start(), m.end(), m[0]))
    for m in _D_NUM.finditer(text):
        if any(o.start <= m.start() < o.end for o in out):
            continue
        a, b = int(m[1]), int(m[2])
        v = _mk_date(int(m[3]), b, a) or _mk_date(int(m[3]), a, b)  # Indonesia: hari/bulan; cadangan bulan/hari
        if v:
            out.append(Match(v, m.start(), m.end(), m[0]))
    for m in _D_TXT.finditer(text):
        v = _mk_date(int(m[3]), MONTHS[m[2].lower()], int(m[1]))
        if v:
            out.append(Match(v, m.start(), m.end(), m[0]))
    for m in _D_TXT_US.finditer(text):
        if any(o.start <= m.start() < o.end for o in out):
            continue
        v = _mk_date(int(m[3]), MONTHS[m[1].lower()], int(m[2]))
        if v:
            out.append(Match(v, m.start(), m.end(), m[0]))
    return sorted(out, key=lambda x: x.start)


def find_month_year(text: str) -> tuple[str, str] | None:
    """`Oktober 2026` → ("2026-10-01", "2026-10-31")."""
    import calendar

    m = _MONTH_YEAR.search(_cut(text))
    if not m:
        return None
    y, mo = int(m[2]), MONTHS[m[1].lower()]
    if not 1990 <= y <= 2100:
        return None
    return date(y, mo, 1).isoformat(), date(y, mo, calendar.monthrange(y, mo)[1]).isoformat()


def normalize_date(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    found = find_dates(value)
    return found[0].value if found else None  # type: ignore[return-value]


# ------------------------------------------------------------------------------------------------ uang
_PERCENT = re.compile(r"\d{1,3}(?:[.,]\d{1,2})?\s?%")
_MONEY = re.compile(r"(?<![\w.,/])(-?\d{1,3}(?:[.,\s]\d{3})+(?:[.,]\d{1,2})?|-?\d+(?:[.,]\d{1,2})?)"
                    r"(?:,-|\.-)?(?![\w/])")


def money_from_raw(raw: str) -> str | None:
    s = raw.strip().replace(" ", "")
    s = re.sub(r"[.,]-$", "", s)
    neg = s.startswith("-")
    s = s.lstrip("-")
    if not s:
        return None
    dots, commas = s.count("."), s.count(",")
    if dots and commas:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        thou = "," if dec == "." else "."
        s = s.replace(thou, "").replace(dec, ".")
    elif dots or commas:
        sep = "." if dots else ","
        parts = s.split(sep)
        if len(parts[-1]) == 3:  # 1.234.567 / 1,500 → pemisah ribuan
            s = s.replace(sep, "")
        elif len(parts) > 2 and all(len(p) == 3 for p in parts[1:-1]):  # 1.234.56 → ribuan + desimal terakhir
            s = "".join(parts[:-1]) + "." + parts[-1]
        elif len(parts) == 2:  # 1500,5 / 12.75 → desimal
            s = s.replace(sep, ".")
        else:  # pola pemisah tidak wajar: bukan nominal
            return None
    if len(s) > 22:  # > 10^18: bukan nominal invoice (dan melewati presisi Decimal bawaan)
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return format_money(-d if neg else d)


def format_money(d: Decimal) -> str | None:
    try:
        d = d.quantize(Decimal("0.01"))
        return str(d.quantize(Decimal(1))) if d == d.to_integral_value() else str(d)
    except InvalidOperation:
        return None


def find_money(text: str) -> list[Match]:
    """Nominal uang. Persentase (`PPN 11%`) dan angka yang menempel pada huruf (`PPh23`) dilewati."""
    text = _cut(text)
    masked = _PERCENT.sub(lambda m: " " * len(m[0]), text)
    out = []
    for m in _MONEY.finditer(masked):
        v = money_from_raw(m[1])
        if v is not None:
            out.append(Match(v, m.start(), m.end(), m[0]))
    return out


def normalize_money(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return format_money(Decimal(value)) if abs(value) < 10**18 else None
    if isinstance(value, str):
        v = value.strip()
        # sudah kanonik (kontrak: string desimal, titik + maks. 2 desimal). "260.000" bukan kanonik → ribuan
        if re.fullmatch(r"-?\d{1,18}(\.\d{1,2})?", v):
            try:
                return format_money(Decimal(v))
            except InvalidOperation:
                return None
        found = find_money(v)
        return found[0].value if found else None  # type: ignore[return-value]
    return None


def to_decimal(v: object) -> Decimal | None:
    try:
        return Decimal(str(v)) if v is not None else None
    except InvalidOperation:
        return None


# ------------------------------------------------------------------------------------------------ NPWP & digit
_NPWP = re.compile(r"(?<!\d)(\d{2}[.\s]?\d{3}[.\s]?\d{3}[.\s]?\d[-.\s]?\d{3}[.\s]?\d{3}|\d{16})(?!\d)")
_ACCOUNT = re.compile(r"(?<![\d\w])(\d(?:[\d\- ]{4,24})\d)(?!\d)")


def digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def find_npwp(text: str) -> list[Match]:
    text = _cut(text)
    out = []
    for m in _NPWP.finditer(text):
        d = digits(m[1])
        if len(d) in (15, 16):
            out.append(Match(d, m.start(), m.end(), m[0]))
    return out


def normalize_npwp(value: object) -> str | None:
    if not isinstance(value, str | int) or isinstance(value, bool):
        return None
    d = digits(str(value))
    return d if len(d) in (15, 16) else None


def find_account(text: str) -> list[Match]:
    text = _cut(text)
    out = []
    for m in _ACCOUNT.finditer(text):
        d = digits(m[1])
        if 6 <= len(d) <= 20 and not find_dates(m[1]):
            out.append(Match(d, m.start(), m.end(), m[0]))
    return out


def normalize_digits(value: object) -> str | None:
    if not isinstance(value, str | int) or isinstance(value, bool):
        return None
    d = digits(str(value))
    return d or None


# ------------------------------------------------------------------------------------------------ nomor dokumen
_DOCNO = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9][A-Za-z0-9/\-.#_]{2,48}[A-Za-z0-9])(?![A-Za-z0-9])")


def find_doc_numbers(text: str) -> list[Match]:
    text = _cut(text)
    out = []
    for m in _DOCNO.finditer(text):
        tok = m[1]
        whole_date = any(d.start == 0 and d.end == len(tok) for d in find_dates(tok))
        if any(c.isdigit() for c in tok) and not whole_date:  # "INV-15-10-26" nomor dokumen, "15-10-26" tanggal
            out.append(Match(tok, m.start(), m.end(), tok))
    return out


def shape_of(value: str) -> str:
    """`INV/2026/10/0042` → `AAA/9999/99/9999` (huruf A, angka 9, karakter lain apa adanya)."""
    return "".join("9" if c.isdigit() else "A" if c.isalpha() else c for c in value)[:40]


def shape_regex(shape: str) -> re.Pattern[str]:
    """Regex linear dari shape: kelas karakter berurutan dengan jumlah tetap, tanpa quantifier bersarang."""
    parts, prev, n = [], None, 0
    for c in shape[:40] + "\0":
        if c == prev:
            n += 1
            continue
        if prev is not None:
            cls = r"\d" if prev == "9" else "[A-Za-z]" if prev == "A" else re.escape(prev)
            parts.append(f"{cls}{{{n}}}")
        prev, n = c, 1
    return re.compile(r"(?<![A-Za-z0-9])" + "".join(parts) + r"(?![A-Za-z0-9])")


# ------------------------------------------------------------------------------------------------ teks
def norm_label(s: str) -> str:
    """Huruf kecil + alfanumerik saja, spasi dirapatkan: pembanding anchor yang tahan tanda baca/OCR."""
    return " ".join(re.sub(r"[^0-9a-z]+", " ", s.lower()).split())


def norm_text(s: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", s.upper())
