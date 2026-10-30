"""Proses satu job invoice: baca dokumen (sandbox) → plugin extractor → template → e-Faktur → validasi → hasil."""

from __future__ import annotations

import json
import logging
import time
from functools import lru_cache
from typing import Any

from sqlalchemy import select

from engine import __version__, metrics
from engine.db import session_scope
from engine.invoice import efaktur, queue, sandbox, store
from engine.invoice import parsing as P
from engine.invoice.document import Document
from engine.invoice.models import InvoiceJob, InvoiceTemplate
from engine.invoice.plugins import get_extractor
from engine.invoice.reader import Options
from engine.settings import get_settings

log = logging.getLogger(__name__)
MAX_RESULT_BYTES = 900_000  # klien ICTNotes membatasi respons 1 MB
MAX_TEXT_CHARS = 200_000
MAX_BLOCKS = 4000


@lru_cache
def engine_versions() -> dict[str, str]:
    """Versi komponen OCR (dipakai /healthz dan hasil job). Dihitung di proses induk tanpa membaca dokumen."""
    from engine.invoice.reader import versions

    return {"pqe": __version__, **versions()}


def find_template(tenant: str, document_type: str, npwps: list[str]) -> dict[str, Any] | None:
    if not npwps:
        return None
    with session_scope() as s:
        rows = s.scalars(select(InvoiceTemplate).where(
            InvoiceTemplate.tenant_code == tenant, InvoiceTemplate.document_type == document_type,
            InvoiceTemplate.issuer_npwp.in_(npwps))).all()
        by = {r.issuer_npwp: r for r in rows}
        for n in npwps:  # urutan: hint dulu, lalu NPWP di dokumen dari atas
            if n in by and by[n].rules:
                return {"issuer_npwp": n, "version": by[n].version, "rules": dict(by[n].rules)}
    return None


def cap_result(result: dict[str, Any]) -> dict[str, Any]:
    """Potong text & blocks agar JSON hasil ≤ MAX_RESULT_BYTES (field tidak pernah dipotong)."""
    text, blocks = result["text"], result["blocks"]
    truncated = len(text) > MAX_TEXT_CHARS or len(blocks) > MAX_BLOCKS
    text, blocks = text[:MAX_TEXT_CHARS], blocks[:MAX_BLOCKS]
    while True:
        result.update(text=text, blocks=blocks, truncated=truncated)
        size = len(json.dumps(result, ensure_ascii=False).encode())
        if size <= MAX_RESULT_BYTES or (not text and not blocks):
            return result
        truncated = True
        text, blocks = text[: len(text) // 2], blocks[: len(blocks) // 2]


def build_result(doc: Document, document_type: str, hint: str | None, tenant: str) -> tuple[dict[str, Any], str]:
    ex = get_extractor(document_type)
    if ex is None:
        raise sandbox.SandboxError("UNSUPPORTED_DOCUMENT_TYPE", f"document_type {document_type} tidak terdaftar")
    npwps = ([hint] if hint else []) + [m.value for b in doc.blocks for m in P.find_npwp(b.text)]  # type: ignore[misc]
    template = find_template(tenant, document_type, list(dict.fromkeys(npwps)))
    fields = ex.extract(doc, template, hint)
    qr_fields, qr_info = efaktur.extract(doc.qr)
    fields.update(qr_fields)
    issues = ex.validate(fields)
    score = ex.detect(doc)
    if score < 0.2:
        issues.append({"code": "NOT_LIKELY_DOCUMENT_TYPE", "fields": [],
                       "message": f"dokumen tidak tampak seperti {document_type}"})
    sources = sorted({p.get("source", "ocr") for p in doc.pages}, key=lambda x: x != "pdf_text")
    layer = "ocr" if "ocr" in sources else "pdf_text"
    layers = list(sources)
    if qr_fields:
        layers.append("efaktur_qr")
    if any(f.method == "template" for f in fields.values()):
        layers.append("template")
    result = {
        "document_type": document_type,
        "layer": layer,
        "layers": layers,
        "pages": len(doc.pages),
        "text": doc.text,
        "blocks": [b.to_dict() for b in doc.blocks],
        "fields": {k: v.to_dict() for k, v in fields.items()},
        "template": {"issuer_npwp": template["issuer_npwp"], "version": template["version"]} if template else None,
        "validation": issues,
        "document_score": round(score, 2),
        "engine": {**engine_versions(), **doc.versions, f"extractor.{document_type}": ex.version},
    }
    if qr_info:
        result["efaktur"] = qr_info
    return cap_result(result), layer


def run_one() -> bool:
    """Ambil dan proses satu job. Return False jika antrean kosong."""
    st = get_settings()
    with session_scope() as s:
        job = queue.claim(s)
        if job is None:
            return False
        jid, tenant, dtype, hint, media = job.id, job.tenant_code, job.document_type, job.template_hint, job.media_type
    t0 = time.monotonic()
    code = msg = None
    result: dict[str, Any] | None = None
    layer = ""
    try:
        data = store.get(jid)
        if data is None:
            raise sandbox.SandboxError("FILE_EXPIRED", "file inbox sudah dihapus (melewati retensi)")
        opt = Options(media_type=media, max_pages=st.invoice_max_pages, render_dpi=st.invoice_render_dpi,
                      max_image_pixels=st.invoice_max_image_pixels,
                      ocr_page_timeout_sec=st.invoice_ocr_page_timeout_sec, read_qr=efaktur.mode() != "off")
        raw = sandbox.run(data, opt, memory_mb=st.invoice_sandbox_memory_mb,
                          timeout_sec=st.invoice_sandbox_timeout_sec)
        result, layer = build_result(Document.from_read(raw), dtype, hint, tenant)
    except sandbox.SandboxError as e:
        code, msg = e.code, str(e)
    except Exception as e:  # bug / DB: dicoba ulang beberapa kali
        log.exception("job invoice %s gagal", jid)
        code, msg = "PROCESSING_ERROR", f"{type(e).__name__}: {e}"
    dur = int((time.monotonic() - t0) * 1000)
    with session_scope() as s:
        job = s.get(InvoiceJob, jid)
        if result is not None:
            queue.succeed(job, result, layer, dur)
            status = "succeeded"
        else:
            status = "failed" if queue.fail(job, code or "PROCESSING_ERROR", msg or "") else "retry"
    metrics.INVOICE_SECONDS.observe(dur / 1000)
    metrics.INVOICE_JOBS.labels(tenant=tenant, status=status, layer=layer or "-").inc()
    log.info("job invoice %s %s (%s, %d ms)", jid, status, layer or code, dur)
    return True
