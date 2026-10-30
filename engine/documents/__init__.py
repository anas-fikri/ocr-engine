"""Modul OCR dokumen generik untuk semua aplikasi client (ADR 0008, ADR 0009, docs/document-ocr-design.md).

Jenis dokumen = plugin (`plugins.py`); plugin pertama: invoice vendor Indonesia (`invoice/`).

Terpisah dari pemeriksaan foto POD: router (`api.py`), tabel (`models.py`), antrean (`queue.py`), dan thread worker
(`worker.py`) sendiri. Dokumen dibaca di proses anak dengan batas sumber daya (`sandbox.py`).
"""

EXTRACTOR_VERSION = "1"
