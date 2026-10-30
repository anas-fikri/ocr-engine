"""Template issuer (layer 4): belajar dari koreksi pengguna dan menyimpan aturan per field.

Aturan field (JSON di invoice_templates.rules):
    {"kind": "date", "anchor": "jatuh tempo", "anchor_box": [..], "same_line": true, "offset": [dx, dy],
     "value_box": [..], "page": 0, "nth": 0, "shape": "AAA/9999/99"}

`anchor` diambil dari teks dokumen (label di kiri/atas nilai), BUKAN dari klien. `shape` hanya untuk nomor dokumen.
"""

from __future__ import annotations

from typing import Any

from engine.invoice import parsing as P
from engine.invoice.document import Block, Document
from engine.invoice.extract import FIELD_KINDS, _remainder, anchor_regex, finder

MAX_ANCHOR_WORDS = 4


def normalize_value(field: str, value: Any) -> Any:
    """Normalisasi nilai koreksi sesuai jenis field. None = tidak valid."""
    kind = FIELD_KINDS.get(field)
    if kind == "date":
        return P.normalize_date(value)
    if kind == "money":
        return P.normalize_money(value)
    if kind == "npwp":
        return P.normalize_npwp(value)
    if kind == "account":
        return P.normalize_digits(value)
    if kind == "currency":
        return value.strip().upper() if isinstance(value, str) and len(value.strip()) == 3 else None
    if kind in ("docno", "text"):
        return " ".join(value.split())[:200] if isinstance(value, str) and value.strip() else None
    return None


def _same(kind: str, found: Any, target: Any) -> bool:
    if kind in ("docno", "text"):
        return P.norm_text(str(found)) == P.norm_text(str(target))
    return found == target


NOT_ANCHOR = {"rp", "idr", "usd", "sgd", "eur", "a", "n", "an", "no", "ke", "di", "the", "of"}


def _anchor_text(text: str) -> str | None:
    """Label yang layak jadi anchor: kata (bukan angka/mata uang/kata sambung), minimal 3 huruf. "Rp" di sel
    sebelah nilai bukan label: anchor seperti itu cocok dengan semua baris uang dan memilih nilai yang salah."""
    words = [w for w in P.norm_label(text).split() if not w.isdigit() and w not in NOT_ANCHOR]
    a = " ".join(words[-MAX_ANCHOR_WORDS:])
    return a if sum(c.isalpha() for c in a) >= 3 else None


def _prefilter(kind: str, target: Any):
    """Cek substring murah sebelum regex per blok (belajar template tetap cepat pada dokumen ribuan blok)."""
    t = str(target)
    if kind in ("docno", "text", "account", "npwp"):
        key = P.norm_text(t)
        return lambda text: key in P.norm_text(text)
    if kind == "money":
        key = P.digits(t.split(".")[0]).lstrip("0") or "0"
        return lambda text: key in P.digits(text)
    if kind == "date":
        return lambda text: t[:4] in text or t[2:4] in text
    return lambda text: True


def _locate(doc: Document, kind: str, target: Any) -> list[tuple[Block, P.Match, int]]:
    out = []
    keep = _prefilter(kind, target)
    for b in doc.blocks:
        if not keep(b.text):
            continue
        if kind == "text":  # teks bebas: harus sebagian besar isi blok, bukan potongan pendek yang kebetulan ada
            nt, nb = P.norm_text(str(target)), P.norm_text(b.text)
            if len(nt) >= 4 and nt in nb and len(nt) >= 0.5 * len(nb):
                idx = b.text.upper().find(str(target).upper()[:3])
                out.append((b, P.Match(str(target), max(0, idx), len(b.text), b.text), 0))
            continue
        for i, m in enumerate(finder(kind)(b.text)):
            if _same(kind, m.value, target):
                out.append((b, m, i))
    return out


def learn_rule(doc: Document, field: str, corrected: Any, extracted: Any = None) -> tuple[dict | None, str | None]:
    """Buat aturan template dari nilai yang benar. Return (aturan, None) atau (None, alasan)."""
    kind = FIELD_KINDS.get(field)
    if kind is None:
        return None, "FIELD_NOT_LEARNABLE"
    hits = _locate(doc, kind, corrected)
    if not hits:
        return None, "VALUE_NOT_FOUND_IN_DOCUMENT"

    def label_prefix(h: tuple[Block, P.Match, int]) -> str | None:
        return _anchor_text(h[0].text[:h[1].start]) if kind != "text" else None

    hits.sort(key=lambda h: (label_prefix(h) is None, h[0].page, h[0].bbox[1]))
    b, m, nth = hits[0]
    rule: dict[str, Any] = {"kind": kind, "page": b.page, "value_box": [round(v, 4) for v in b.bbox], "nth": nth}
    if kind == "docno":
        rule["shape"] = P.shape_of(str(corrected))
    prefix = label_prefix(hits[0])
    if prefix:
        # nilai di blok yang sama dengan label: nth = urutan nilai di sisa teks setelah label (apply_rule sama)
        a = anchor_regex(prefix).search(P._cut(b.text))
        if a is not None:
            rest = _remainder(b.text, a.end())
            same = [i for i, x in enumerate(finder(kind)(rest)) if _same(kind, x.value, corrected)]
            rule.update(anchor=prefix, anchor_box=rule["value_box"], same_line=True, nth=same[0] if same else 0)
            return rule, None
    label = _label_block(doc, b)
    if label is not None:
        rule.update(anchor=_anchor_text(label.text), anchor_box=[round(v, 4) for v in label.bbox], same_line=False,
                    offset=[round(b.cx - label.cx, 4), round(b.cy - label.cy, 4)])
    return rule, None


def _label_block(doc: Document, b: Block) -> Block | None:
    """Label terdekat di kiri (baris sama) atau di atas (kolom sama) yang berisi kata."""
    left = [o for o in doc.blocks if o.id != b.id and b.same_row(o) and o.bbox[2] <= b.bbox[0] + 0.01
            and _anchor_text(o.text)]
    if left:
        return max(left, key=lambda o: o.bbox[2])
    above = [o for o in doc.blocks if o.page == b.page and o.id != b.id and o.bbox[3] <= b.bbox[1] + 0.005
             and b.bbox[1] - o.bbox[3] <= 3 * b.height and o.bbox[0] <= b.bbox[2] and o.bbox[2] >= b.bbox[0]
             and _anchor_text(o.text)]
    return max(above, key=lambda o: o.bbox[3]) if above else None
