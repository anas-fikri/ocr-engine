"""Pemetaan metadata foto (resi, kurir, jenis foto, ...) dari berbagai sumber sesuai config tenant.

Field yang dikirim eksplisit oleh pemanggil (body API / kolom DB dengan nama field yang sama)
selalu diutamakan. Jika tidak ada, aturan dicoba berurutan dan nilai pertama yang tidak kosong dipakai:

  meta:<nama>               metadata objek S3 (x-amz-meta-<nama>)
  field:<nama>              field dari request API / baris DB
  key:regex:<pola>          regex pada object key; pakai named group sesuai nama field
                            atau group pertama
  key:segment:<n>           potongan ke-n dari key (dipisah "/", mulai 0, boleh negatif)
  key:basename              nama file tanpa ekstensi
  default:<nilai>           nilai tetap

Jika tenant tidak mendefinisikan mapping untuk sebuah field, dipakai aturan bawaan:
  ["meta:<nama-pakai-strip>", "meta:<nama_pakai_underscore>"]
"""

from __future__ import annotations

import posixpath
import re
from functools import lru_cache
from typing import Any

from engine.tenant_config import MAPPABLE_FIELDS


@lru_cache(maxsize=512)
def _regex(pattern: str) -> re.Pattern:
    return re.compile(pattern)


def default_rules(field: str) -> list[str]:
    return [f"meta:{field.replace('_', '-')}", f"meta:{field}"]


def apply_rule(rule: str, field: str, key: str, s3_meta: dict, fields: dict) -> Any:
    kind, _, arg = rule.partition(":")
    if kind == "meta":
        return s3_meta.get(arg.lower())
    if kind == "field":
        return fields.get(arg)
    if kind == "default":
        return arg
    if kind == "key":
        sub, _, param = arg.partition(":")
        if sub == "regex":
            m = _regex(param).search(key)
            if not m:
                return None
            gd = m.groupdict()
            if field in gd:
                return gd[field]
            return m.group(1) if m.groups() else m.group(0)
        if sub == "segment":
            parts = key.split("/")
            try:
                return parts[int(param)]
            except (IndexError, ValueError):
                return None
        if sub == "basename":
            return posixpath.splitext(posixpath.basename(key))[0]
    raise ValueError(f"aturan mapping tidak dikenal: {rule!r}")


def map_metadata(
    rules: dict[str, list[str]],
    key: str = "",
    s3_meta: dict | None = None,
    fields: dict | None = None,
) -> dict[str, Any]:
    s3_meta = {k.lower(): v for k, v in (s3_meta or {}).items()}
    fields = {k: v for k, v in (fields or {}).items() if v not in (None, "")}
    out: dict[str, Any] = {}
    for field in MAPPABLE_FIELDS:
        if field in fields:
            out[field] = fields[field]
            continue
        for rule in rules.get(field) or default_rules(field):
            value = apply_rule(rule, field, key, s3_meta, fields)
            if value not in (None, ""):
                out[field] = str(value).strip() if not isinstance(value, (int, float)) else value
                break
    return out


def validate_rules(rules: dict[str, list[str]]) -> None:
    """Raise ValueError jika ada aturan yang salah tulis (dipakai saat menyimpan config)."""
    for field, items in rules.items():
        for rule in items:
            kind, _, arg = rule.partition(":")
            if kind not in ("meta", "field", "default", "key"):
                raise ValueError(f"{field}: aturan tidak dikenal {rule!r}")
            if kind == "key":
                sub, _, param = arg.partition(":")
                if sub == "regex":
                    try:
                        re.compile(param)
                    except re.error as e:
                        raise ValueError(f"{field}: regex tidak valid {param!r}: {e}") from e
                elif sub == "segment":
                    if not param.lstrip("-").isdigit():
                        raise ValueError(f"{field}: key:segment butuh angka, contoh key:segment:1")
                elif sub != "basename":
                    raise ValueError(f"{field}: aturan key tidak dikenal {rule!r}")
