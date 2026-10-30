"""Photo Quality Engine: deteksi kualitas foto, kompresi dinamis, validasi keaslian, dan OCR dokumen invoice."""

__version__ = "0.5.0"

import os as _os

# Kode build (commit git) dari image Docker: scripts/deploy.sh mengisinya otomatis. "dev" = dijalankan dari source.
__build__ = _os.environ.get("PQE_BUILD") or "dev"
