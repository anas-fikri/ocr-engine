"""Photo Quality Engine: deteksi kualitas foto, kompresi dinamis, dan validasi keaslian."""

__version__ = "0.1.0"

import os as _os

# Kode build (commit git) dari image Docker: scripts/deploy.sh mengisinya otomatis. "dev" = dijalankan dari source.
__build__ = _os.environ.get("PQE_BUILD") or "dev"
