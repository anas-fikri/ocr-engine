"""Plugin jenis dokumen `invoice` (invoice vendor Indonesia): label ID/EN, NPWP penjual vs pembeli, tabel item,
validasi silang DPP/PPN/PPh/total, dan QR e-Faktur (efaktur.py). Dibangun di atas mesin aturan generik
(engine/documents/rules.py); core modul dokumen tidak tahu apa pun tentang invoice.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from engine.documents import parsing as P
from engine.documents.document import Block, Document
from engine.documents.plugins import DocumentExtractor, Field
from engine.documents.rules import (
    C_SAME_BLOCK,
    CURRENCIES,
    DOC_WORDS,
    MAX_LABEL_HITS,
    Spec,
    _by_label,
    _find_currency,
    _remainder,
    apply_rule,
)

EXTRACTOR_VERSION = "1"

FIELD_KINDS = {
    "vendor_npwp": "npwp", "vendor_name": "text", "bank_account": "account", "invoice_number": "docno",
    "invoice_date": "date", "due_date": "date", "period_start": "date", "period_end": "date",
    "currency": "currency", "subtotal": "money", "discount": "money", "dpp": "money", "vat_amount": "money",
    "wht_amount": "money", "total": "money",
}
FIELDS = (*FIELD_KINDS, "lines")
MONEY_FIELDS = tuple(f for f, k in FIELD_KINDS.items() if k == "money")

BUYER = re.compile(r"pembeli|buyer|customer|pelanggan|kepada|bill\s*to|ship\s*to|lawan\s*transaksi|penerima|"
                   r"dibayar\s*oleh|sold\s*to", re.I)
ENTITY = re.compile(r"\b(?:PT|CV|UD|Tbk|PD|Koperasi|Yayasan|Firma|Ltd|Inc|Pte)\b\.?", re.I)

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
    ], exclude=r"jatuh|tempo|\bdue\b|periode|period|masa|cetak|\bpo\b|order|pesanan|kirim|delivery|pengiriman|"
               r"faktur\s*pajak|lahir|terima|received"),
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
        r"grand\s*total|total\s*(?:tagihan|bayar|pembayaran|invoice|amount|keseluruhan|akhir)|"
        r"jumlah\s*(?:tagihan|yang\s*harus\s*dibayar|total)",
        r"\btotal\b|amount\s*due|balance\s*due",
    ], exclude=r"sub\s*-?\s*total|total\s*(?:harga|dpp|ppn|pph|vat|tax|pajak|diskon|discount|qty|item|berat|paid)|"
               r"amount\s*paid|sudah\s*dibayar|uang\s*muka|down\s*payment", pick="max"),
    "bank_account": Spec("account", [  # nomor telepon di baris yang sama dipotong (_find_account)
        r"no(?:mor)?\.?\s*rek(?:ening)?\b\.?|\brekening\b|account\s*(?:no|number)|\ba/c\b|acc(?:ount)?\.?\s*no",
    ]),
}
PERIOD_LABEL = re.compile(r"periode|\bperiod\b|masa\s*(?:tagihan|layanan|pajak|sewa)?|billing\s*period", re.I)
_RANGE_DM = re.compile(rf"(?<!\d)(\d{{1,2}})\s*(?:-|–|s/?d|sampai|to)\s*(\d{{1,2}})\s+({P._MONTH_RE})\.?\s+(\d{{4}})",
                       re.I)



def buyer_zone(doc: Document) -> set[int]:
    """Blok milik identitas pembeli: blok berlabel pembeli/"Kepada" + blok di kanannya + beberapa baris di bawahnya
    (sampai ketemu nama badan usaha lain atau label penjual)."""
    ids: set[int] = set()
    for b in doc.blocks[:MAX_LABEL_HITS * 10]:
        if not BUYER.search(P._cut(b.text)):
            continue
        ids.add(b.id)
        ids.update(o.id for o in doc.right_of(b))
        seen_entity = bool(ENTITY.search(b.text))
        for o in doc.below(b, 5):
            if re.search(r"penjual|seller|vendor|supplier|dari\b|from\b", o.text, re.I):
                break
            if ENTITY.search(o.text):
                if seen_entity:
                    break
                seen_entity = True
            ids.add(o.id)
    return ids


def npwp_candidates(doc: Document) -> list[tuple[str, Block, bool, bool]]:
    """(npwp, blok, berlabel NPWP, zona pembeli). 16/15 digit polos tanpa label NPWP (nomor VA, NIK, rekening)
    bukan kandidat."""
    buyer = buyer_zone(doc)
    out = []
    for row in doc.rows():  # satu kali jalan per baris: label "NPWP" di kiri berlaku untuk blok di kanannya
        label_seen = False
        for b in row:
            has_label = bool(re.search(r"npwp", P._cut(b.text), re.I))
            labeled, label_seen = has_label or label_seen, label_seen or has_label
            for m in P.find_npwp(b.text):
                formatted = not m.raw.isdigit()
                if labeled or formatted:
                    out.append((m.value, b, labeled, b.id in buyer))
    return sorted(out, key=lambda c: (c[1].page, c[1].bbox[1]))


def _vendor_npwp(doc: Document, hint: str | None) -> Field | None:
    cands = npwp_candidates(doc)
    if hint:
        for v, b, _, _ in cands:
            if v == hint:
                return Field(v, b.conf * 0.97, b.source, "hint", [b.id])
    for v, b, labeled, buyer in cands:
        if not buyer:
            return Field(v, b.conf * (0.8 if labeled else 0.6), b.source, "label" if labeled else "guess", [b.id])
    return None


def _vendor_name(doc: Document) -> Field | None:
    # label harus di awal blok dan berbentuk label ("Penjual:", "From"), bukan kata di dalam nama ("PT Penjual Jaya")
    f = _by_label(doc, Spec("text", [r"^\s*(?:nama\s*(?:penjual|pkp|vendor|supplier|perusahaan)|vendor|supplier|"
                                     r"penjual|dari|from)\s*(?::|$)"], exclude=r"npwp|alamat|address"))
    if f:
        return f
    first = [b for b in doc.blocks if b.page == 0 and b.bbox[1] < 0.3]
    buyer_ids = buyer_zone(doc)
    pool = [b for b in sorted(first, key=lambda x: x.bbox[1]) if b.id not in buyer_ids
            and not DOC_WORDS.match(b.text.strip(" :.")) and sum(c.isalpha() for c in b.text) >= 4
            and not re.search(r"npwp|alamat|jl\.|jalan|telp|phone|email|@|www\.", b.text, re.I)]
    for b in pool:
        if ENTITY.search(b.text):
            return Field(_clean_name(b.text), b.conf * 0.6, b.source, "guess", [b.id])
    if pool:
        return Field(_clean_name(pool[0].text), pool[0].conf * 0.4, pool[0].source, "guess", [pool[0].id])
    return None


def _clean_name(text: str) -> str:
    """OCR sering menggabungkan nama vendor dengan judul di baris yang sama ("PT X  INVOICE"): judul dibuang."""
    words = text.strip(" :").split()
    while len(words) > 1 and DOC_WORDS.match(words[-1].strip(" :.|")):
        words.pop()
    while len(words) > 1 and DOC_WORDS.match(words[0].strip(" :.|")):
        words.pop(0)
    return " ".join(words)[:200]


def _currency(doc: Document) -> Field | None:
    for b in doc.blocks:
        ms = _find_currency(b.text)
        if ms:
            explicit = ms[0].raw.upper() in CURRENCIES
            return Field(ms[0].value, b.conf * (0.9 if explicit else 0.85), b.source, "label", [b.id])
    return None


def _period(doc: Document) -> tuple[Field | None, Field | None]:
    hits = [(b, m) for b in doc.blocks if (m := PERIOD_LABEL.search(P._cut(b.text)))][:MAX_LABEL_HITS]
    for b, m in hits:
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
    """Sel teks & sel angka (urut kiri → kanan). Angka di ujung kanan sel teks dipisah per token (OCR sering
    menggabungkan deskripsi + qty + harga dalam satu baris)."""
    text: list[str] = []
    nums: list[str] = []
    for b in row:
        v = _num_cell(b.text)
        if v is not None:
            nums.append(v)
            continue
        toks, tail = b.text.split(), []
        while toks and len(tail) < 4:
            t = _num_cell(toks[-1])
            if t is None:
                break
            tail.insert(0, t)
            toks.pop()
        if toks and not nums:  # teks di kanan angka (satuan, catatan): abaikan
            text.append(" ".join(toks))
        nums += tail
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
    """Invoice vendor Indonesia. Penerbit = NPWP penjual (15/16 digit)."""

    document_type = "invoice"
    version = EXTRACTOR_VERSION
    field_kinds = FIELD_KINDS
    extra_fields = ("lines",)

    def normalize_issuer(self, value: Any) -> str | None:
        return P.normalize_npwp(value)

    def issuer_of(self, fields: dict[str, Field]) -> str | None:
        f = fields.get("vendor_npwp")
        return str(f.value) if f else None

    def issuer_check(self, doc: Document, issuer: str, hint: str | None) -> str | None:
        """Template hanya untuk NPWP penjual yang tercetak (bukan NPWP pembeli, yang ada di semua invoice masuk).
        Dokumen tanpa NPWP penjual tercetak: boleh bila sama dengan template_hint job."""
        cands = npwp_candidates(doc)
        if issuer in {v for v, _, _, buyer in cands if not buyer}:
            return None
        if issuer in {v for v, _, _, buyer in cands if buyer}:
            return "ISSUER_IS_BUYER"
        if issuer == hint and not any(not buyer for *_, buyer in cands):
            return None
        return "ISSUER_MISMATCH"

    def status(self) -> dict[str, Any]:
        from engine.documents.invoice import efaktur

        return {"efaktur_qr": efaktur.mode()}

    def wants_qr(self) -> bool:
        from engine.documents.invoice import efaktur

        return efaktur.mode() != "off"

    def enrich(self, doc: Document, fields: dict[str, Field]) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
        from engine.documents.invoice import efaktur

        qr_fields, info = efaktur.extract(doc.qr)
        issues = efaktur.merge(fields, qr_fields)
        return issues, ({"efaktur": info} if info else {}), (["efaktur_qr"] if qr_fields else [])

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
                if f and (f.method == "template" or name not in fields):
                    fields[name] = f
        return fields

    def validate(self, fields: dict[str, Field]) -> list[dict[str, Any]]:
        return cross_check(fields)
