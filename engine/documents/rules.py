"""Mesin aturan generik untuk semua plugin jenis dokumen: jenis nilai (date, money, npwp, account, docno, text,
currency), pencarian nilai di dekat label, dan penerapan aturan template (layer 4: anchor + pola + posisi).

Tanpa AI/ML. Semua regex tetap (di sini / parsing.py); template hanya berisi teks anchor (dicocokkan sebagai kata yang
di-escape) dan `shape` (diterjemahkan ke regex linear). Plugin (mis. documents/invoice) menyusun label & validasinya
di atas modul ini.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from engine.documents import parsing as P
from engine.documents.document import Block, Document
from engine.documents.plugins import Field

# faktor keyakinan metode (dikalikan confidence teks sumber)
C_SAME_BLOCK, C_RIGHT, C_BELOW = 0.85, 0.82, 0.72
C_TEMPLATE_ANCHOR, C_TEMPLATE_POS = 0.95, 0.6
CURRENCIES = ("IDR", "USD", "SGD", "EUR", "JPY", "CNY", "AUD", "MYR", "HKD", "GBP")
DOC_WORDS = re.compile(r"^(?:invoice|faktur(?:\s*pajak)?|tagihan|kwitansi|receipt|nota|bill|tax\s*invoice)$", re.I)


@dataclass
class Spec:
    kind: str
    labels: list[str]
    exclude: str | None = None
    pick: str = "first"  # first | last | max (total: nilai terbesar di antara baris "total" yang setara)


MAX_LABEL_HITS = 30  # blok berlabel sama yang diperiksa per label (dokumen berisi ribuan "Total" tidak jadi lambat)
_PHONE = re.compile(r"\b(?:telp|tel|phone|hubungi|hp|wa|whatsapp|fax|faks|email|cs)\b", re.I)
_NOISE = re.compile(r"\b(?:rp|idr|usd)\b\.?|[^0-9a-z%]+", re.I)  # "Diskon 10%": persen = isi, bukan kosong


def finder(kind: str) -> Callable[[str], list[P.Match]]:
    return {"date": P.find_dates, "money": P.find_money, "npwp": P.find_npwp, "account": _find_account,
            "docno": P.find_doc_numbers, "text": _find_text, "currency": _find_currency}[kind]


def _find_account(text: str) -> list[P.Match]:
    m = _PHONE.search(text)
    return P.find_account(text[: m.start()] if m else text)


def _find_text(text: str) -> list[P.Match]:
    t = text.strip(" :.-#\t")
    if sum(c.isalpha() for c in t) < 3 or DOC_WORDS.match(t):
        return []
    start = text.find(t)
    return [P.Match(t[:200], start, start + len(t), t)]


def _find_currency(text: str) -> list[P.Match]:
    out = []
    for m in re.finditer(r"\b(" + "|".join(CURRENCIES) + r")\b|\bRp\.?(?=\s?\d)|\bUS\$", P._cut(text)):
        code = m[1] or ("USD" if m[0].startswith("US") else "IDR")
        out.append(P.Match(code, m.start(), m.end(), m[0]))
    return out


def _value_in(text: str, kind: str, shape: str | None = None, nth: int = 0) -> P.Match | None:
    if shape and kind == "docno":
        ms = [P.Match(m[0], m.start(), m.end(), m[0]) for m in P.shape_regex(shape).finditer(P._cut(text))]
        if ms:
            return ms[min(nth, len(ms) - 1)]
    ms = finder(kind)(text)
    return ms[min(nth, len(ms) - 1)] if ms else None


def _remainder(text: str, end: int) -> str:
    return text[end:].lstrip(" :;=-#.\t")


# ------------------------------------------------------------------------------------------------ aturan umum
def _by_label(doc: Document, spec: Spec, skip: set[int] | None = None) -> Field | None:
    for label in spec.labels:
        rx = re.compile(label, re.I)
        hits = []
        for b in doc.blocks:
            if skip and b.id in skip:
                continue
            m = rx.search(P._cut(b.text))
            if m and not (spec.exclude and re.search(spec.exclude, P._cut(b.text), re.I)):
                hits.append((b, m))
        hits = hits[:MAX_LABEL_HITS] if spec.pick == "first" else hits[-MAX_LABEL_HITS:]
        found = [f for b, m in hits if (f := _value_near(doc, b, m.end(), spec.kind))]
        if found:
            if spec.pick == "max":
                return max(found, key=lambda f: P.to_decimal(f.value) or 0)
            return found[-1] if spec.pick == "last" else found[0]
    return None


def _value_near(doc: Document, b: Block, label_end: int, kind: str) -> Field | None:
    rest = _remainder(b.text, label_end)
    v = _value_in(rest, kind)
    if v:
        return Field(v.value, b.conf * C_SAME_BLOCK, b.source, "label", [b.id])
    for o in doc.right_of(b)[:2]:
        v = _value_in(o.text, kind)
        if v:
            return Field(v.value, min(b.conf, o.conf) * C_RIGHT, o.source, "label", [b.id, o.id])
    # label sudah diikuti isi yang bukan nilai (mis. "Diskon 10%", "No. Rek: a.n. PT X"): jangan ambil nilai baris
    # di bawahnya, karena itu milik label lain
    if kind != "text" and not _NOISE.sub("", rest):
        for o in doc.below(b)[:1]:
            v = _value_in(o.text, kind)
            if v and not re.search(r"[a-z]{4,}\s*:", o.text, re.I):  # baris bawah adalah label lain
                return Field(v.value, min(b.conf, o.conf) * C_BELOW, o.source, "label", [b.id, o.id])
    return None


# ------------------------------------------------------------------------------------------------ template (layer 4)
def anchor_regex(anchor: str) -> re.Pattern[str]:
    words = [re.escape(w) for w in anchor.split()[:6]]
    return re.compile(r"(?<![0-9a-z])" + r"[^0-9a-z]{0,5}".join(words) + r"(?![a-z])", re.I)


def apply_rule(doc: Document, rule: dict[str, Any]) -> Field | None:
    kind, shape, nth = rule.get("kind", "text"), rule.get("shape"), int(rule.get("nth", 0))
    anchor = rule.get("anchor")
    if anchor:
        rx = anchor_regex(anchor)
        hits = [(b, m) for b in doc.blocks for m in [rx.search(P._cut(b.text))] if m]
        ab = rule.get("anchor_box")
        if ab:
            hits.sort(key=lambda h: (h[0].page != rule.get("page", 0),
                                     abs(h[0].cx - (ab[0] + ab[2]) / 2) + abs(h[0].cy - (ab[1] + ab[3]) / 2)))
        for b, m in hits[:3]:
            # PDF memisah label & nilai menjadi blok berbeda, OCR sering menggabungkannya dalam satu baris: template
            # yang dipelajari dari satu jenis tetap berlaku untuk jenis lain (coba kedua cara, urutan sesuai aturan)
            ways = (_rule_same_line, _rule_offset) if rule.get("same_line") else (_rule_offset, _rule_same_line)
            for way in ways:
                f = way(doc, b, m, rule, kind, shape, nth)
                if f:
                    return f
    box = rule.get("value_box")
    if box:
        pad = 0.01
        inside = [o for o in doc.blocks if o.page == rule.get("page", 0)
                  and o.bbox[0] <= box[2] + pad and o.bbox[2] >= box[0] - pad
                  and o.bbox[1] <= box[3] + pad and o.bbox[3] >= box[1] - pad]
        inside.sort(key=lambda o: abs(o.cy - (box[1] + box[3]) / 2) + abs(o.bbox[2] - box[2]))
        for o in inside:
            v = _value_in(o.text, kind, shape, nth)
            if v:  # tanpa anchor: posisi saja (layout bisa bergeser), hanya mengisi field yang kosong
                return Field(v.value, o.conf * C_TEMPLATE_POS, o.source, "position", [o.id])
    return None


def _rule_same_line(doc: Document, b: Block, m: re.Match[str], rule: dict, kind: str, shape: str | None,
                    nth: int) -> Field | None:
    v = _value_in(_remainder(b.text, m.end()), kind, shape, nth)
    if v:
        return Field(v.value, b.conf * C_TEMPLATE_ANCHOR, b.source, "template", [b.id])
    for o in doc.right_of(b)[:2]:
        v = _value_in(o.text, kind, shape, 0)
        if v:
            return Field(v.value, min(b.conf, o.conf) * C_TEMPLATE_ANCHOR, o.source, "template", [b.id, o.id])
    return None


def _rule_offset(doc: Document, b: Block, m: re.Match[str], rule: dict, kind: str, shape: str | None,
                 nth: int) -> Field | None:
    if "offset" not in rule:
        return None
    dx, dy = rule["offset"]
    tx, ty = b.cx + dx, b.cy + dy
    near = [o for o in doc.blocks if o.page == b.page and o.id != b.id
            and o.bbox[0] - 0.05 <= tx <= o.bbox[2] + 0.05 and abs(o.cy - ty) <= 1.5 * o.height]
    near.sort(key=lambda o: abs(o.cx - tx) + abs(o.cy - ty))
    for o in near[:2]:
        v = _value_in(o.text, kind, shape, nth)
        if v:
            return Field(v.value, min(b.conf, o.conf) * C_TEMPLATE_ANCHOR, o.source, "template", [b.id, o.id])
    return None
