"""Extractor invoice (plugin bawaan): aturan umum label ID/EN, template issuer (layer 4), dan validasi silang.

Tanpa AI/ML: label + pola + posisi. Semua regex tetap (didefinisikan di sini / parsing.py); template hanya berisi
teks anchor (dicocokkan sebagai kata yang di-escape) dan `shape` (diterjemahkan ke regex linear).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from engine.invoice import EXTRACTOR_VERSION
from engine.invoice import parsing as P
from engine.invoice.document import Block, Document
from engine.invoice.plugins import DocumentExtractor, Field

FIELD_KINDS = {
    "vendor_npwp": "npwp", "vendor_name": "text", "bank_account": "account", "invoice_number": "docno",
    "invoice_date": "date", "due_date": "date", "period_start": "date", "period_end": "date",
    "currency": "currency", "subtotal": "money", "discount": "money", "dpp": "money", "vat_amount": "money",
    "wht_amount": "money", "total": "money",
}
FIELDS = (*FIELD_KINDS, "lines")
MONEY_FIELDS = tuple(f for f, k in FIELD_KINDS.items() if k == "money")

# faktor keyakinan metode (dikalikan confidence teks sumber)
C_SAME_BLOCK, C_RIGHT, C_BELOW = 0.85, 0.82, 0.72
C_TEMPLATE_ANCHOR, C_TEMPLATE_POS = 0.95, 0.8
CURRENCIES = ("IDR", "USD", "SGD", "EUR", "JPY", "CNY", "AUD", "MYR", "HKD", "GBP")

BUYER = re.compile(r"pembeli|buyer|customer|pelanggan|kepada|bill\s*to|ship\s*to|lawan\s*transaksi|penerima|"
                   r"dibayar\s*oleh|sold\s*to", re.I)
DOC_WORDS = re.compile(r"^(?:invoice|faktur(?:\s*pajak)?|tagihan|kwitansi|receipt|nota|bill|tax\s*invoice)$", re.I)
ENTITY = re.compile(r"\b(?:PT|CV|UD|Tbk|PD|Koperasi|Yayasan|Firma|Ltd|Inc|Pte)\b\.?", re.I)


@dataclass
class Spec:
    kind: str
    labels: list[str]
    exclude: str | None = None
    pick: str = "first"  # first | last (mis. "Total" polos: ambil yang paling bawah)


SPECS: dict[str, Spec] = {
    "invoice_number": Spec("docno", [
        r"no(?:mor)?\.?\s*(?:invoice|inv|faktur|tagihan|kwitansi|nota|bill)\b",
        r"(?:invoice|faktur|tagihan|bill)\s*(?:no|number|nomor|num)\b\.?|invoice\s*#",
        r"\bref(?:erence)?\.?\s*(?:no|number)\b",
    ], exclude=r"pajak|npwp|rekening|account|po\b|order"),
    "invoice_date": Spec("date", [
        r"tanggal\s*(?:invoice|faktur|tagihan|dokumen|kwitansi)|tgl\.?\s*(?:invoice|faktur|tagihan|dokumen)",
        r"invoice\s*date|date\s*of\s*invoice|billing\s*date",
        r"\btanggal\b|\btgl\b|\bdate\b",
    ], exclude=r"jatuh|tempo|\bdue\b|periode|period|masa|cetak\s*ulang"),
    "due_date": Spec("date", [
        r"jatuh\s*tempo|due\s*date|payment\s*due|batas\s*(?:waktu\s*)?pembayaran",
        r"\bdue\b",
    ]),
    "subtotal": Spec("money", [r"sub\s*-?\s*total|total\s*harga|jumlah\s*harga"]),
    "discount": Spec("money", [r"diskon|discount|potongan(?!\s*pph)"]),
    "dpp": Spec("money", [r"\bdpp\b|dasar\s*pengenaan\s*pajak|tax\s*base"]),
    "vat_amount": Spec("money", [r"\bppn\b(?!\s*bm)|\bvat\b|pajak\s*pertambahan\s*nilai"],
                       exclude=r"\bdpp\b|dasar\s*pengenaan|npwp|termasuk|include|incl"),
    "wht_amount": Spec("money", [r"\bpph(?![a-z])(?:\s*(?:pasal|ps)?\.?\s*(?:4\s*\(?2\)?|15|21|22|23|26)(?!\d))?"
                                r"|withholding|\bwht\b|pemotongan\s*pajak"], exclude=r"npwp"),
    "total": Spec("money", [
        r"grand\s*total|total\s*(?:tagihan|bayar|pembayaran|invoice|amount|due|keseluruhan)|"
        r"jumlah\s*(?:tagihan|yang\s*harus\s*dibayar|dibayar|total)|amount\s*due|balance\s*due|total\s*akhir",
        r"\btotal\b",
    ], exclude=r"sub\s*-?\s*total|total\s*(?:harga|dpp|ppn|pph|diskon|qty|item|berat)", pick="last"),
    "bank_account": Spec("account", [
        r"no(?:mor)?\.?\s*rek(?:ening)?\b\.?|\brekening\b|account\s*(?:no|number)|\ba/c\b|acc(?:ount)?\.?\s*no",
    ]),
}
PERIOD_LABEL = re.compile(r"periode|\bperiod\b|masa\s*(?:tagihan|layanan|pajak|sewa)?|billing\s*period", re.I)
_RANGE_DM = re.compile(rf"(?<!\d)(\d{{1,2}})\s*(?:-|–|s/?d|sampai|to)\s*(\d{{1,2}})\s+({P._MONTH_RE})\.?\s+(\d{{4}})",
                       re.I)


def finder(kind: str) -> Callable[[str], list[P.Match]]:
    return {"date": P.find_dates, "money": P.find_money, "npwp": P.find_npwp, "account": P.find_account,
            "docno": P.find_doc_numbers, "text": _find_text, "currency": _find_currency}[kind]


def _find_text(text: str) -> list[P.Match]:
    t = text.strip(" :.-#\t")
    if sum(c.isalpha() for c in t) < 3:
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
        found: list[Field] = []
        for b in doc.blocks:
            if skip and b.id in skip:
                continue
            m = rx.search(P._cut(b.text))
            if not m or (spec.exclude and re.search(spec.exclude, b.text, re.I)):
                continue
            f = _value_near(doc, b, m.end(), spec.kind)
            if f:
                found.append(f)
        if found:
            return found[-1] if spec.pick == "last" else found[0]
    return None


def _value_near(doc: Document, b: Block, label_end: int, kind: str) -> Field | None:
    v = _value_in(_remainder(b.text, label_end), kind)
    if v:
        return Field(v.value, b.conf * C_SAME_BLOCK, b.source, "label", [b.id])
    for o in doc.right_of(b)[:2]:
        v = _value_in(o.text, kind)
        if v:
            return Field(v.value, min(b.conf, o.conf) * C_RIGHT, o.source, "label", [b.id, o.id])
    if kind != "text":
        for o in doc.below(b)[:1]:
            v = _value_in(o.text, kind)
            if v and not re.search(r"[a-z]{4,}\s*:", o.text, re.I):  # baris bawah adalah label lain
                return Field(v.value, min(b.conf, o.conf) * C_BELOW, o.source, "label", [b.id, o.id])
    return None


def _vendor_npwp(doc: Document, hint: str | None) -> Field | None:
    cands = []
    for b in doc.blocks:
        for m in P.find_npwp(b.text):
            near_buyer = BUYER.search(b.text) or any(BUYER.search(o.text) for o in doc.blocks
                                                     if o.id != b.id and b.same_row(o) and o.bbox[2] <= b.bbox[0])
            cands.append((m.value, b, bool(near_buyer)))
    if hint:
        for v, b, _ in cands:
            if v == hint:
                return Field(v, b.conf * 0.97, b.source, "hint", [b.id])
    for v, b, buyer in sorted(cands, key=lambda c: (c[1].page, c[1].bbox[1])):
        if not buyer:
            labeled = re.search(r"npwp", b.text, re.I)
            return Field(v, b.conf * (0.8 if labeled else 0.6), b.source, "label" if labeled else "guess", [b.id])
    return None


def _vendor_name(doc: Document) -> Field | None:
    f = _by_label(doc, Spec("text", [r"nama\s*(?:penjual|pkp|vendor|supplier|perusahaan)|\bvendor\b|\bsupplier\b|"
                                     r"\bpenjual\b|\bdari\b|\bfrom\b"], exclude=r"npwp|alamat|address"))
    if f:
        return f
    first = [b for b in doc.blocks if b.page == 0 and b.bbox[1] < 0.3]
    buyer_ids = set()
    for b in first:
        if BUYER.search(b.text):
            buyer_ids.add(b.id)
            buyer_ids.update(o.id for o in doc.below(b, 2.5) + doc.right_of(b))
    pool = [b for b in sorted(first, key=lambda x: x.bbox[1]) if b.id not in buyer_ids
            and not DOC_WORDS.match(b.text.strip(" :.")) and sum(c.isalpha() for c in b.text) >= 4
            and not re.search(r"npwp|alamat|jl\.|jalan|telp|phone|email|@|www\.", b.text, re.I)]
    for b in pool:
        if ENTITY.search(b.text):
            return Field(b.text.strip(" :")[:200], b.conf * 0.6, b.source, "guess", [b.id])
    if pool:
        return Field(pool[0].text.strip(" :")[:200], pool[0].conf * 0.4, pool[0].source, "guess", [pool[0].id])
    return None


def _currency(doc: Document) -> Field | None:
    for b in doc.blocks:
        ms = _find_currency(b.text)
        if ms:
            explicit = ms[0].raw.upper() in CURRENCIES
            return Field(ms[0].value, b.conf * (0.9 if explicit else 0.85), b.source, "label", [b.id])
    return None


def _period(doc: Document) -> tuple[Field | None, Field | None]:
    for b in doc.blocks:
        m = PERIOD_LABEL.search(P._cut(b.text))
        if not m:
            continue
        segs = [(b, _remainder(b.text, m.end()))] + [(o, o.text) for o in doc.right_of(b)[:2]]
        for src, text in segs:
            conf = min(b.conf, src.conf) * C_SAME_BLOCK
            r = _RANGE_DM.search(text)
            if r:
                mo, y = P.MONTHS[r[3].lower()], int(r[4])
                s, e = P._mk_date(y, mo, int(r[1])), P._mk_date(y, mo, int(r[2]))
                if s and e:
                    return (Field(s, conf, src.source, "label", [b.id]), Field(e, conf, src.source, "label", [b.id]))
            ds = P.find_dates(text)
            if len(ds) >= 2:
                return (Field(ds[0].value, conf, src.source, "label", [b.id]),
                        Field(ds[1].value, conf, src.source, "label", [b.id]))
            my = P.find_month_year(text)
            if my:
                return (Field(my[0], conf * 0.9, src.source, "label", [b.id]),
                        Field(my[1], conf * 0.9, src.source, "label", [b.id]))
    return None, None


# ------------------------------------------------------------------------------------------------ tabel item
HEAD_DESC = re.compile(r"deskripsi|description|keterangan|uraian|nama\s*barang|item|produk|product|jasa|layanan",
                       re.I)
HEAD_AMT = re.compile(r"jumlah|amount|total|subtotal|nilai|harga", re.I)
STOP = re.compile(r"^(?:sub\s*-?\s*total|total|grand|dpp|diskon|discount|ppn|vat|pph|jumlah\s*(?:tagihan|total)|"
                  r"terbilang|catatan|note|potongan|uang\s*muka)", re.I)
_QTY = re.compile(r"^(\d{1,6}(?:[.,]\d{1,3})?)(?:\s*[a-zA-Z]{1,6}\.?)?$")


def _num_cell(t: str) -> str | None:
    t = t.strip().removeprefix("Rp").removeprefix("IDR").strip(" .:")
    if not t or any(c.isalpha() for c in t.replace("Rp", "")) and not _QTY.match(t):
        return None
    ms = P.find_money(t)
    if ms and ms[0].start == 0 and ms[0].end >= len(t.rstrip(",-")) - 1:
        return ms[0].value  # type: ignore[return-value]
    q = _QTY.match(t)
    return P.money_from_raw(q[1]) if q else None


def _split_row(row: list[Block]) -> tuple[list[str], list[str]]:
    """Sel teks & sel angka (urut kiri → kanan). Baris OCR yang menyatu dipecah per token dari kanan."""
    cells = [b.text for b in row]
    if len(cells) == 1:
        toks = cells[0].split()
        nums: list[str] = []
        while toks and len(nums) < 4:
            v = _num_cell(toks[-1].lstrip("Rp"))
            if v is None:
                break
            nums.insert(0, v)
            toks.pop()
        return [" ".join(toks)], nums
    text, nums = [], []
    for c in cells:
        v = _num_cell(c)
        if v is None:
            if nums:  # teks di kanan angka (satuan, catatan): abaikan
                continue
            text.append(c)
        else:
            nums.append(v)
    return text, nums


def _lines(doc: Document) -> Field | None:
    rows = doc.rows()
    start = None
    for i, row in enumerate(rows):
        t = " ".join(b.text for b in row)
        if HEAD_DESC.search(t) and HEAD_AMT.search(t) and len(t) < 200:
            start = i + 1
            break
    if start is None:
        return None
    items: list[dict[str, Any]] = []
    confs: list[float] = []
    for row in rows[start:start + 200]:
        first = row[0].text.strip()
        if any(STOP.match(b.text.strip()) for b in row) or STOP.match(first):
            break
        text, nums = _split_row(row)
        desc = re.sub(r"^\d{1,3}[.)]\s+", "", " ".join(text)).strip()
        if not nums:
            if items and desc:  # deskripsi berlanjut ke baris berikutnya
                items[-1]["description"] = (items[-1]["description"] + " " + desc)[:300]
            continue
        item: dict[str, Any] = {"description": desc[:300] or None, "amount": nums[-1]}
        if len(nums) >= 3:
            item["quantity"], item["unit_price"] = nums[-3], nums[-2]
        elif len(nums) == 2:
            item["quantity"] = nums[0]
        conf = min(b.conf for b in row) * 0.8
        q, up, amt = (P.to_decimal(item.get(k)) for k in ("quantity", "unit_price", "amount"))
        if q is not None and up is not None and amt is not None:
            conf = min(b.conf for b in row) * (0.92 if abs(q * up - amt) <= max(Decimal(1), amt * Decimal("0.005"))
                                               else 0.5)
        items.append({k: item.get(k) for k in ("description", "quantity", "unit_price", "amount")})
        confs.append(conf)
    if not items:
        return None
    return Field(items, sum(confs) / len(confs), doc.blocks[0].source if doc.blocks else "ocr", "table")


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
            if v:
                return Field(v.value, o.conf * C_TEMPLATE_POS, o.source, "template", [o.id])
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


# ------------------------------------------------------------------------------------------------ validasi silang
def _approx(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= max(Decimal(2), abs(b) * Decimal("0.001"))


def cross_check(fields: dict[str, Field]) -> list[dict[str, Any]]:
    d = {k: P.to_decimal(fields[k].value) for k in MONEY_FIELDS if k in fields}
    issues: list[dict[str, Any]] = []

    def issue(code: str, flds: list[str], msg: str, ok: bool) -> None:
        for f in flds:
            if f in fields:
                fields[f].confidence = min(0.99, fields[f].confidence + 0.05) if ok else fields[f].confidence * 0.6
        if not ok:
            issues.append({"code": code, "fields": flds, "message": msg})

    sub, disc, dpp, vat, wht, tot = (d.get(k) for k in MONEY_FIELDS)
    if sub is not None and dpp is not None:
        issue("DPP_MISMATCH", ["dpp"], "subtotal - diskon ≠ DPP", _approx(sub - (disc or 0), dpp)
              or _approx((sub - (disc or 0)) * 11 / 12, dpp))
    if dpp is not None and vat is not None:
        rates = (Decimal("0.11"), Decimal("0.12"), Decimal("0.11") * 12 / 11, Decimal("0.10"))
        issue("VAT_MISMATCH", ["vat_amount"], "PPN bukan 10/11/12% dari DPP",
              any(abs(dpp * r - vat) <= max(Decimal(2), vat * Decimal("0.01")) for r in rates)
              or _approx(dpp * 11 / 12 * Decimal("0.12"), vat))
    if tot is not None:
        base = dpp if dpp is not None else (sub - (disc or 0) if sub is not None else None)
        if base is not None:
            options = [base + (vat or 0), base + (vat or 0) - (wht or 0)]
            if sub is not None:
                options += [sub - (disc or 0) + (vat or 0), sub - (disc or 0) + (vat or 0) - (wht or 0)]
            issue("TOTAL_MISMATCH", ["total"], "total ≠ DPP + PPN (- PPh)", any(_approx(o, tot) for o in options))
    if "lines" in fields and sub is not None:
        amts = [P.to_decimal(x.get("amount")) for x in fields["lines"].value]
        if all(a is not None for a in amts):
            issue("LINES_MISMATCH", ["lines"], "jumlah item ≠ subtotal", _approx(sum(amts), sub))  # type: ignore[arg-type]
    if "invoice_date" in fields and "due_date" in fields and fields["due_date"].value < fields["invoice_date"].value:
        issue("DUE_BEFORE_INVOICE", ["due_date"], "jatuh tempo sebelum tanggal invoice", False)
    if "period_start" in fields and "period_end" in fields \
            and fields["period_end"].value < fields["period_start"].value:
        issue("PERIOD_INVERTED", ["period_end"], "akhir periode sebelum awal periode", False)
    return issues


# ------------------------------------------------------------------------------------------------ plugin
class InvoiceExtractor(DocumentExtractor):
    document_type = "invoice"
    version = EXTRACTOR_VERSION

    def detect(self, doc: Document) -> float:
        t = doc.text.lower()
        score = 0.0
        for w, s in (("invoice", 0.4), ("faktur", 0.4), ("tagihan", 0.3), ("total", 0.2), ("npwp", 0.1),
                     ("ppn", 0.1), ("jatuh tempo", 0.1), ("due date", 0.1)):
            if w in t:
                score += s
        return min(1.0, score)

    def extract(self, doc: Document, template: dict[str, Any] | None, hint: str | None) -> dict[str, Field]:
        fields: dict[str, Field] = {}
        used: set[int] = set()
        for name in ("due_date", "invoice_number", "invoice_date", "subtotal", "discount", "dpp", "vat_amount",
                     "wht_amount", "total", "bank_account"):
            f = _by_label(doc, SPECS[name], skip=used if name == "invoice_date" else None)
            if f:
                fields[name] = f
                if name == "due_date":
                    used.update(f.block_ids)
        for name, f in (("vendor_npwp", _vendor_npwp(doc, hint)), ("vendor_name", _vendor_name(doc)),
                        ("currency", _currency(doc)), ("lines", _lines(doc))):
            if f:
                fields[name] = f
        ps, pe = _period(doc)
        if ps and pe:
            fields["period_start"], fields["period_end"] = ps, pe
        if fields.get("invoice_date") and fields.get("due_date") \
                and fields["invoice_date"].value == fields["due_date"].value \
                and set(fields["invoice_date"].block_ids) & set(fields["due_date"].block_ids):
            del fields["invoice_date"]  # label "Tanggal" umum ikut menangkap baris jatuh tempo
        if template:
            for name, rule in (template.get("rules") or {}).items():
                if name not in FIELD_KINDS:
                    continue
                f = apply_rule(doc, rule)
                if f:
                    fields[name] = f
        return fields

    def validate(self, fields: dict[str, Field]) -> list[dict[str, Any]]:
        return cross_check(fields)
