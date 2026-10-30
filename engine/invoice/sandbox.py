"""Proses anak untuk membaca dokumen yang tidak dipercaya (PDF/gambar dari vendor).

Proses induk (worker) menjalankan `python -m engine.invoice.sandbox`, mengirim opsi (1 baris JSON) + isi file lewat
stdin, dan membaca hasil JSON dari stdout. Di proses anak:

- RLIMIT_AS (memori), RLIMIT_CPU, RLIMIT_FSIZE dipasang sebelum dokumen disentuh: PDF bom / gambar bom membuat proses
  anak mati, bukan worker;
- environment hanya berisi variabel yang perlu (tanpa DATABASE_URL, kredensial SILO, atau PQE_*);
- prioritas CPU diturunkan (nice) agar analisis sinkron foto kurir tetap didahulukan.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from dataclasses import asdict
from typing import Any

from engine.invoice.reader import Options

SAFE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TESSDATA_PREFIX", "PYTHONPATH", "OMP_THREAD_LIMIT")
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
NICE = 10


class SandboxError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def child_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in SAFE_ENV if k in os.environ}
    env.setdefault("OMP_THREAD_LIMIT", "1")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def run(data: bytes, opt: Options, *, memory_mb: int, timeout_sec: int) -> dict[str, Any]:
    """Jalankan pembacaan dokumen di proses anak. Raise SandboxError dengan kode error permanen."""
    header = json.dumps({"options": asdict(opt), "memory_mb": memory_mb, "cpu_sec": max(5, int(timeout_sec * 2 / 3))})
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "engine.invoice.sandbox"],
            input=header.encode() + b"\n" + data,
            capture_output=True,
            timeout=timeout_sec,
            env=child_env(),
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise SandboxError("PROCESSING_TIMEOUT", f"dokumen tidak selesai dibaca dalam {timeout_sec} detik") from e
    out = proc.stdout[:MAX_OUTPUT_BYTES]
    if proc.returncode != 0 or not out:
        if proc.returncode in (-signal.SIGXCPU, -signal.SIGKILL, -signal.SIGXFSZ, -signal.SIGSEGV, -signal.SIGABRT) \
                or b"MemoryError" in proc.stderr[-4000:] or b"std::bad_alloc" in proc.stderr[-4000:]:
            raise SandboxError("RESOURCE_LIMIT", "dokumen melewati batas memori/CPU saat dibaca")
        if out:
            try:
                res = json.loads(out)
                if not res.get("ok"):
                    raise SandboxError(res.get("code", "PROCESSING_ERROR"), res.get("message", ""))
            except ValueError:
                pass
        raise SandboxError("PROCESSING_ERROR", f"proses pembaca dokumen gagal (exit {proc.returncode})")
    try:
        res = json.loads(out)
    except ValueError as e:
        raise SandboxError("PROCESSING_ERROR", "keluaran pembaca dokumen tidak valid") from e
    if not res.get("ok"):
        raise SandboxError(res.get("code", "PROCESSING_ERROR"), res.get("message", ""))
    return res["document"]


def _limit(memory_mb: int, cpu_sec: int) -> None:
    import resource

    mem = memory_mb * 1024 * 1024
    for lim, val in ((resource.RLIMIT_AS, mem), (resource.RLIMIT_CPU, cpu_sec),
                     (resource.RLIMIT_FSIZE, 16 * 1024 * 1024), (resource.RLIMIT_CORE, 0)):
        try:
            soft, hard = resource.getrlimit(lim)
            if hard != resource.RLIM_INFINITY:
                val = min(val, hard)
            resource.setrlimit(lim, (val, hard))
        except (ValueError, OSError):  # pragma: no cover - platform tanpa dukungan (macOS RLIMIT_AS)
            pass
    try:
        os.nice(NICE)
    except OSError:  # pragma: no cover
        pass


def main() -> int:  # pragma: no cover - dijalankan di proses anak, diuji lewat run()
    raw = sys.stdin.buffer.read()
    nl = raw.find(b"\n")
    header = json.loads(raw[:nl])
    data = raw[nl + 1:]
    del raw
    _limit(int(header["memory_mb"]), int(header["cpu_sec"]))
    from engine.invoice.reader import ReadError, read_document

    try:
        doc = read_document(data, Options(**header["options"]))
        res: dict[str, Any] = {"ok": True, "document": doc}
    except ReadError as e:
        res = {"ok": False, "code": e.code, "message": str(e)}
    except MemoryError:
        res = {"ok": False, "code": "RESOURCE_LIMIT", "message": "dokumen melewati batas memori saat dibaca"}
    sys.stdout.write(json.dumps(res, ensure_ascii=False))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
