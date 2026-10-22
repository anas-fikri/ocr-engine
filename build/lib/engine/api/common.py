"""Helper bersama untuk router API."""

from __future__ import annotations

from fastapi import HTTPException, Request

from engine.i18n import pick_lang
from engine.settings import get_settings


def api_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message})


def request_lang(request: Request, lang: str | None = None) -> str:
    if lang in ("id", "en"):
        return lang
    return pick_lang(request.headers.get("accept-language"), get_settings().default_lang)
