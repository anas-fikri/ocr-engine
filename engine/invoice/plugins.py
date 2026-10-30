"""Plugin jenis dokumen (FR-OCR-012). Setiap jenis dokumen = satu kelas extractor:

    class MyExtractor(DocumentExtractor):
        document_type = "receipt"
        version = "1"
        def detect(self, doc) -> float: ...                 # 0-1: seberapa yakin dokumen ini jenisnya
        def extract(self, doc, template, hint) -> dict: ... # {field: Field}
        def validate(self, fields) -> list[dict]: ...       # [{"code", "fields", "message"}]

Daftarkan lewat entry point Python `pqe.document_extractors` (paket terpisah) atau env
`PQE_DOCUMENT_PLUGINS=paket.modul:Kelas`. Bawaan: `invoice` (engine/invoice/extract.py).
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.metadata import entry_points
from typing import Any

from engine.invoice.document import Document

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


class DocumentExtractor:
    document_type = ""
    version = "0"

    def detect(self, doc: Document) -> float:  # pragma: no cover - antarmuka
        raise NotImplementedError

    def extract(self, doc: Document, template: dict[str, Any] | None, hint: str | None) -> dict[str, Field]:
        raise NotImplementedError  # pragma: no cover

    def validate(self, fields: dict[str, Field]) -> list[dict[str, Any]]:
        return []


def _load(spec: str) -> DocumentExtractor:
    mod, _, cls = spec.partition(":")
    obj = getattr(importlib.import_module(mod.strip()), cls.strip())
    inst = obj() if isinstance(obj, type) else obj
    if not isinstance(inst, DocumentExtractor) or not inst.document_type:
        raise TypeError(f"{spec} bukan DocumentExtractor dengan document_type")
    return inst


@lru_cache
def registry() -> dict[str, DocumentExtractor]:
    from engine.invoice.extract import InvoiceExtractor
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
