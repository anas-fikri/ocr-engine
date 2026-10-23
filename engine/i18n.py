"""Terjemahan pesan alasan (ID/EN). Kode alasan stabil, pesan bisa berubah."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources

import yaml

SUPPORTED = ("id", "en")


@lru_cache
def _catalog(lang: str) -> dict:
    text = resources.files("engine").joinpath(f"locales/{lang}.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text)


def pick_lang(accept_language: str | None, default: str = "id") -> str:
    """Pilih bahasa dari header Accept-Language (contoh: 'en-US,en;q=0.9,id;q=0.8')."""
    if accept_language:
        candidates = []
        for part in accept_language.split(","):
            bits = part.strip().split(";q=")
            code = bits[0].strip().lower()[:2]
            try:
                q = float(bits[1]) if len(bits) > 1 else 1.0
            except ValueError:
                q = 0.0
            candidates.append((q, code))
        for _, code in sorted(candidates, reverse=True):
            if code in SUPPORTED:
                return code
    return default if default in SUPPORTED else "id"


def reason_text(code: str, lang: str) -> dict[str, str]:
    entry = _catalog(lang)["reasons"].get(code)
    if entry is None:
        return {"message": code, "suggestion": ""}
    return {"message": entry["message"], "suggestion": entry["suggestion"]}


def verdict_text(verdict: str, lang: str) -> str:
    return _catalog(lang)["verdict"].get(verdict, verdict)


def retake_text(lang: str) -> str:
    return _catalog(lang)["retake"]


def all_reason_codes() -> list[str]:
    return sorted(_catalog("id")["reasons"])


def localize_reasons(reasons: list[dict], lang: str) -> list[dict]:
    return [{**r, **reason_text(r["code"], lang)} for r in reasons]
