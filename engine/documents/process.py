"""Proses satu job dokumen: baca dokumen (sandbox) → plugin extractor → template → pengayaan plugin → validasi."""

from __future__ import annotations

import json
import logging
import time
from functools import lru_cache
from typing import Any

from sqlalchemy import select

from engine import __version__, metrics
from engine.db import session_scope
from engine.documents import queue, sandbox, store
from engine.documents.document import Document
from engine.documents.models import DocumentJob, DocumentTemplate
from engine.documents.plugins import get_extractor
from engine.documents.reader import Options
from engine.settings import get_settings

log = logging.getLogger(__name__)
MAX_RESULT_BYTES = 900_000  # client umumnya membatasi respons 1 MB
MAX_TEXT_CHARS = 200_000
MAX_BLOCKS = 4000


@lru_cache
def engine_versions() -> dict[str, str]:
    """Versi komponen (dipakai /healthz dan hasil job), dihitung di proses induk tanpa meng-import tesserocr: import
    pertama tesserocr dari thread non-main gagal (cysignals) dan akan mengubah jalur OCR foto. Versi library Tesseract
    yang persis ikut di hasil job (dilaporkan proses anak); di sini versi paket tesserocr yang membawanya."""
    from importlib.metadata import PackageNotFoundError, version

    out = {"pqe": __version__}
    for dist in ("tesserocr", "pypdfium2"):
        try:
            out[dist] = version(dist)
        except PackageNotFoundError:
            pass
    try:
        import pypdfium2

        out["pdfium"] = str(pypdfium2.version.PDFIUM_INFO).split()[0]
    except Exception:
        pass
    return out


def find_template(tenant: str, document_type: str, issuers: list[str]) -> dict[str, Any] | None:
    if not issuers:
        return None
    with session_scope() as s:
        rows = s.scalars(select(DocumentTemplate).where(
            DocumentTemplate.tenant_code == tenant, DocumentTemplate.document_type == document_type,
            DocumentTemplate.issuer_id.in_(issuers))).all()
        by = {r.issuer_id: r for r in rows}
        for n in issuers:  # urutan: hint dulu, lalu penerbit hasil ekstraksi
            if n in by and by[n].rules:
                return {"issuer_id": n, "version": by[n].version, "rules": dict(by[n].rules)}
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
    # template dicari dari hint, lalu penerbit yang ditentukan plugin dari hasil aturan umum (invoice: NPWP penjual,
    # bukan sembarang NPWP: NPWP pembeli ada di semua invoice masuk dan akan memilih template vendor yang salah)
    fields = ex.extract(doc, None, hint)
    issuers = [n for n in (hint, ex.issuer_of(fields)) if n]
    template = find_template(tenant, document_type, list(dict.fromkeys(issuers)))
    if template:
        fields = ex.extract(doc, template, hint)
    extra_issues, extra, extra_layers = ex.enrich(doc, fields)
    issues = ex.validate(fields) + extra_issues
    score = ex.detect(doc)
    if score < 0.2:
        issues.append({"code": "NOT_LIKELY_DOCUMENT_TYPE", "fields": [],
                       "message": f"dokumen tidak tampak seperti {document_type}"})
    sources = sorted({p.get("source", "ocr") for p in doc.pages}, key=lambda x: x != "pdf_text")
    layer = "ocr" if "ocr" in sources else "pdf_text"
    layers = list(sources)
    layers += extra_layers
    if any(f.method in ("template", "position") for f in fields.values()):
        layers.append("template")
    result = {
        "document_type": document_type,
        "layer": layer,
        "layers": layers,
        "pages": len(doc.pages),
        "text": doc.text,
        "blocks": [b.to_dict() for b in doc.blocks],
        "fields": {k: v.to_dict() for k, v in fields.items()},
        "template": {"issuer_id": template["issuer_id"], "version": template["version"]} if template else None,
        "validation": issues,
        "document_score": round(score, 2),
        "engine": {**engine_versions(), **doc.versions, f"extractor.{document_type}": ex.version},
    }
    for k, v in extra.items():  # key tambahan milik plugin (mis. "efaktur"); tidak boleh menimpa key core
        result.setdefault(k, v)
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
    ex = get_extractor(dtype)
    try:
        data = store.get(jid)
        if data is None:
            raise sandbox.SandboxError("FILE_EXPIRED", "file inbox sudah dihapus (melewati retensi)")
        opt = Options(media_type=media, max_pages=st.docs_max_pages, render_dpi=st.docs_render_dpi,
                      max_image_pixels=st.docs_max_image_pixels,
                      ocr_page_timeout_sec=st.docs_ocr_page_timeout_sec,
                      read_qr=bool(ex and ex.wants_qr()))
        raw = sandbox.run(data, opt, memory_mb=st.docs_sandbox_memory_mb,
                          timeout_sec=st.docs_sandbox_timeout_sec)
        result, layer = build_result(Document.from_read(raw), dtype, hint, tenant)
    except sandbox.SandboxError as e:
        code, msg = e.code, str(e)
    except Exception as e:  # bug / DB: dicoba ulang beberapa kali
        log.exception("job dokumen %s gagal", jid)
        code, msg = "PROCESSING_ERROR", f"{type(e).__name__}: {e}"
    dur = int((time.monotonic() - t0) * 1000)
    with session_scope() as s:
        job = s.get(DocumentJob, jid)
        if result is not None:
            queue.succeed(job, result, layer, dur)
            status = "succeeded"
        else:
            status = "failed" if queue.fail(job, code or "PROCESSING_ERROR", msg or "") else "retry"
    metrics.DOCS_SECONDS.observe(dur / 1000)
    metrics.DOCS_JOBS.labels(tenant=tenant, type=dtype, status=status, layer=layer or "-").inc()
    log.info("job dokumen %s %s (%s, %d ms)", jid, status, layer or code, dur)
    return True
