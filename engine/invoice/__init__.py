"""Modul OCR dokumen invoice vendor (ADR 0008, docs/invoice-ocr-design.md).

Terpisah dari pemeriksaan foto POD: router (`api.py`), tabel (`models.py`), antrean (`queue.py`), dan thread worker
(`worker.py`) sendiri. Dokumen dibaca di proses anak dengan batas sumber daya (`sandbox.py`).
"""

EXTRACTOR_VERSION = "1"
