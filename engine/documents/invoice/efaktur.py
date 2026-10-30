"""Layer 2: QR e-Faktur (FR-OCR-004). MATI secara bawaan (izin TBD-15 belum dikonfirmasi).

PQE_INVOICE_EFAKTUR_QR:
  off    : QR tidak dibaca.
  decode : QR dibaca lokal; NPWP penjual & nomor faktur diambil dari URL QR. Tanpa koneksi keluar.
  lookup : decode + panggil URL validasi DJP (hanya host di PQE_INVOICE_EFAKTUR_HOSTS, HTTPS, tanpa redirect,
           timeout 10 dtk, respons ≤ 256 KB, XML via defusedxml) untuk NPWP/nama penjual, DPP, PPN, tanggal faktur.

ASUMSI (belum diverifikasi ke DJP): URL QR berbentuk
  http(s)://svc.efaktur.pajak.go.id/validasi/faktur/<npwp penjual 15>/<nomor faktur 13>/<kode>
dan respons berupa XML `resValidateFakturPm` (e-Faktur 3.x) dengan elemen npwpPenjual, namaPenjual, tanggalFaktur
(dd/MM/yyyy), jumlahDpp, jumlahPpn, nomorFaktur, statusFaktur. Format Coretax (2025) bisa berbeda.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from engine.documents import parsing as P
from engine.documents.plugins import Field
from engine.settings import get_settings

log = logging.getLogger(__name__)
MAX_RESPONSE = 256 * 1024
_PATH = re.compile(r"^/validasi/faktur/(\d{15,16})/(\d{13,17})/[A-Za-z0-9]{1,200}/?$")
QR_CONF = 0.99


def mode() -> str:
    m = get_settings().invoice_efaktur_qr.strip().lower()
    return m if m in ("off", "decode", "lookup") else "off"


def allowed_hosts() -> set[str]:
    return {h.strip().lower() for h in get_settings().invoice_efaktur_hosts.split(",") if h.strip()}


def parse_qr_url(text: str) -> dict[str, str] | None:
    """URL QR e-Faktur yang valid & host-nya diizinkan → {"url", "npwp", "nomor_faktur"}. Selain itu None."""
    try:
        u = urlsplit(text.strip()[:2000])
        port = u.port  # port tidak valid ("99999") → ValueError
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not u.hostname or u.hostname.lower() not in allowed_hosts():
        return None
    if port not in (None, 80, 443) or u.username or u.password or u.query or u.fragment:
        return None
    m = _PATH.match(u.path)
    if not m:
        return None
    url = urlunsplit(("https", u.hostname.lower(), u.path, "", ""))  # selalu HTTPS
    return {"url": url, "npwp": m[1], "nomor_faktur": m[2]}


def _xml_fields(body: bytes) -> dict[str, str]:
    from defusedxml import ElementTree  # menolak entity/DTD (XXE, billion laughs)

    root = ElementTree.fromstring(body, forbid_dtd=True)
    out = {}
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("npwpPenjual", "namaPenjual", "tanggalFaktur", "jumlahDpp", "jumlahPpn", "nomorFaktur",
                   "statusFaktur", "statusApproval") and el.text:
            out[tag] = el.text.strip()[:300]
    return out


def lookup(url: str, client: httpx.Client | None = None) -> dict[str, str]:
    own = client is None
    client = client or httpx.Client(timeout=10, follow_redirects=False)
    try:
        with client.stream("GET", url, headers={"Accept": "application/xml"}) as r:
            if r.status_code != 200:
                raise ValueError(f"HTTP {r.status_code}")
            buf = b""
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > MAX_RESPONSE:
                    raise ValueError("respons terlalu besar")
        return _xml_fields(buf)
    finally:
        if own:
            client.close()


def extract(qr_texts: list[str], client: httpx.Client | None = None) -> tuple[dict[str, Field], dict[str, Any]]:
    """Field dari QR e-Faktur sesuai mode. Return (fields, info untuk hasil job)."""
    m = mode()
    if m == "off":
        return {}, {}
    for text in qr_texts:
        q = parse_qr_url(text)
        if not q:
            continue
        fields = {"vendor_npwp": Field(q["npwp"], QR_CONF, "efaktur_qr", "qr")}
        info: dict[str, Any] = {"mode": m, "nomor_faktur": q["nomor_faktur"]}
        if m == "lookup":
            try:
                x = lookup(q["url"], client)
            except Exception as e:  # DJP tidak bisa dihubungi: tetap pakai hasil decode + OCR
                log.warning("validasi e-Faktur gagal: %s", type(e).__name__)
                info["lookup_error"] = type(e).__name__
                return fields, info
            info["status"] = x.get("statusFaktur")
            for src, dst, norm in (("npwpPenjual", "vendor_npwp", P.normalize_npwp),
                                   ("tanggalFaktur", "invoice_date", P.normalize_date),
                                   ("jumlahDpp", "dpp", P.normalize_money), ("jumlahPpn", "vat_amount",
                                                                            P.normalize_money)):
                v = norm(x.get(src)) if x.get(src) else None
                if v:
                    fields[dst] = Field(v, QR_CONF, "efaktur_qr", "qr")
            if x.get("namaPenjual"):
                fields["vendor_name"] = Field(x["namaPenjual"], QR_CONF, "efaktur_qr", "qr")
        return fields, info
    return {}, {"mode": m, "qr_found": bool(qr_texts)}


def merge(fields: dict, qr_fields: dict) -> list[dict[str, Any]]:
    """Gabungkan field QR e-Faktur dengan hasil baca dokumen. Isi QR dikendalikan pembuat dokumen (bisa QR faktur
    lain), jadi QR tidak menimpa nilai tercetak yang berbeda: nilai sama → confidence naik; field kosong → diisi dari
    QR (0,9); berbeda → nilai tercetak dipertahankan, confidence turun, dilaporkan EFAKTUR_MISMATCH."""
    issues = []
    for name, q in qr_fields.items():
        cur = fields.get(name)
        if cur is None:
            q.confidence = 0.9
            fields[name] = q
        elif P.norm_text(str(cur.value)) == P.norm_text(str(q.value)):
            cur.confidence = max(cur.confidence, q.confidence)
            cur.method, cur.source = "qr", q.source
        else:
            cur.confidence *= 0.6
            issues.append({"code": "EFAKTUR_MISMATCH", "fields": [name],
                           "message": f"nilai di QR e-Faktur ({q.value}) berbeda dengan yang tercetak"})
    return issues
