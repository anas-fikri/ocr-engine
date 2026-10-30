"""Plugin jenis dokumen (FR-OCR-012). Core modul dokumen (API, antrean, sandbox, template, retensi) generik; semua
pengetahuan tentang satu jenis dokumen ada di plugin-nya. Setiap jenis dokumen = satu kelas extractor:

    class MyExtractor(DocumentExtractor):
        document_type = "receipt"
        version = "1"
        field_kinds = {"total": "money", "receipt_date": "date"}  # field yang bisa dipelajari template
        def detect(self, doc) -> float: ...                 # 0-1: seberapa yakin dokumen ini jenisnya
        def extract(self, doc, template, hint) -> dict: ... # {field: Field}
        def validate(self, fields) -> list[dict]: ...       # [{"code", "fields", "message"}]

Opsional: identitas penerbit untuk template (`normalize_issuer`, `issuer_of`, `issuer_check`) dan pengayaan setelah
ekstraksi (`wants_qr`, `enrich`). Jenis nilai yang tersedia: lihat engine/documents/rules.py (`finder`); template
diterapkan plugin dengan `rules.apply_rule`.

Daftarkan lewat entry point Python `pqe.document_extractors` (paket terpisah) atau env
`PQE_DOCUMENT_PLUGINS=paket.modul:Kelas`. Bawaan: `invoice` (engine/documents/invoice/).
"""

from __future__ import annotations

import importlib
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.metadata import entry_points
from typing import Any

from engine.documents.document import Document

log = logging.getLogger(__name__)


@dataclass
class Field:
    value: Any
    confidence: float
    source: str  # pdf_text | ocr | template | efaktur_qr
    method: str = "label"  # label | template | position | guess | qr | table
    block_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "confidence": round(max(0.0, min(1.0, self.confidence)), 3),
                "source": self.source, "method": self.method}


_ISSUER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$")


class DocumentExtractor:
    document_type = ""
    version = "0"
    field_kinds: dict[str, str] = {}  # field yang bisa dipelajari template → jenis nilai
    extra_fields: tuple[str, ...] = ()  # field lain yang dikenal tetapi tidak dipelajari (mis. tabel)

    @property
    def fields(self) -> tuple[str, ...]:
        return (*self.field_kinds, *self.extra_fields)

    def detect(self, doc: Document) -> float:  # pragma: no cover - antarmuka
        raise NotImplementedError

    def extract(self, doc: Document, template: dict[str, Any] | None, hint: str | None) -> dict[str, Field]:
        raise NotImplementedError  # pragma: no cover

    def validate(self, fields: dict[str, Field]) -> list[dict[str, Any]]:
        return []

    # ---- penerbit (template per penerbit dokumen)
    def normalize_issuer(self, value: Any) -> str | None:
        """ID penerbit kanonik (bawaan: 1-64 karakter alfanumerik . _ -). Invoice: NPWP digit saja."""
        v = str(value or "").strip()
        return v if _ISSUER.match(v) else None

    def issuer_of(self, fields: dict[str, Field]) -> str | None:
        """ID penerbit dari hasil ekstraksi (memilih template saat klien tidak mengirim template_hint)."""
        return None

    def issuer_check(self, doc: Document, issuer: str, hint: str | None) -> str | None:
        """Boleh belajar template untuk penerbit ini dari dokumen ini? None = boleh, selain itu kode error.
        Bawaan: ID penerbit tercetak di dokumen, atau sama dengan template_hint job."""
        from engine.documents import parsing as P

        key = P.norm_text(issuer)
        if issuer == hint or (key and any(key in P.norm_text(b.text) for b in doc.blocks)):
            return None
        return "ISSUER_MISMATCH"

    def status(self) -> dict[str, Any]:
        """Info tambahan plugin untuk /healthz (mis. mode QR e-Faktur)."""
        return {}

    # ---- pengayaan opsional (mis. QR e-Faktur pada invoice)
    def wants_qr(self) -> bool:
        """True = proses anak juga membaca QR di halaman pertama (doc.qr)."""
        return False

    def enrich(self, doc: Document, fields: dict[str, Field]) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
        """Setelah extract: (issues tambahan, key tambahan di hasil job, layer tambahan)."""
        return [], {}, []


def _load(spec: str) -> DocumentExtractor:
    mod, _, cls = spec.partition(":")
    obj = getattr(importlib.import_module(mod.strip()), cls.strip())
    inst = obj() if isinstance(obj, type) else obj
    if not isinstance(inst, DocumentExtractor) or not inst.document_type:
        raise TypeError(f"{spec} bukan DocumentExtractor dengan document_type")
    return inst


@lru_cache
def registry() -> dict[str, DocumentExtractor]:
    from engine.documents.invoice.extract import InvoiceExtractor
    from engine.settings import get_settings

    reg: dict[str, DocumentExtractor] = {"invoice": InvoiceExtractor()}
    specs = [f"{ep.module}:{ep.attr}" for ep in entry_points(group="pqe.document_extractors")]
    specs += [s for s in get_settings().document_plugins.split(",") if s.strip()]
    for spec in specs:
        try:
            inst = _load(spec)
            reg[inst.document_type] = inst
            log.info("plugin dokumen dimuat: %s (%s)", inst.document_type, spec)
        except Exception:
            log.exception("plugin dokumen %s gagal dimuat", spec)
    return reg


def get_extractor(document_type: str) -> DocumentExtractor | None:
    return registry().get(document_type)
