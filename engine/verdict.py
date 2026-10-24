"""Gabungkan hasil semua check menjadi verdict PASS / WARN / REJECT + alasan + aksi sesuai mode."""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.analyzers.base import CheckResult
from engine.tenant_config import Profile

PASS, WARN, REJECT = "PASS", "WARN", "REJECT"
_RANK = {PASS: 0, WARN: 1, REJECT: 2}
TEXT_FIRST_SOFTEN = {"sharpness", "resolution", "exposure"}  # diturunkan ke WARN jika teks terbaca


@dataclass
class Reason:
    code: str
    check: str
    severity: str  # reject | warn | info
    score: float | None = None
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"code": self.code, "check": self.check, "severity": self.severity,
                "score": self.score, "detail": self.detail}


@dataclass
class Verdict:
    verdict: str
    action: str  # log | notify | reject
    scores: dict[str, float]
    reasons: list[Reason]


def authenticity_score(results: list[CheckResult]) -> tuple[float | None, list]:
    """Skor kepercayaan 0-100 (100 = tidak ada indikasi). Risk = 100 - skor."""
    risks = [f for r in results for f in r.findings if f.severity == "risk"]
    relevant = any(r.check in ("exif", "duplicate") for r in results)
    if not relevant:
        return None, []
    return float(max(0, 100 - sum(f.points for f in risks))), risks


def decide(profile: Profile, results: list[CheckResult]) -> Verdict:
    scores: dict[str, float] = {}
    reasons: list[Reason] = []
    level = PASS

    def apply(check: str, score: float, code: str, detail: dict | None = None) -> None:
        nonlocal level
        t = profile.thresholds.get(check)
        if t is None:
            return
        if t.reject is not None and score < t.reject:
            reasons.append(Reason(code, check, "reject", score, detail or {}))
            level = REJECT
        elif t.warn is not None and score < t.warn:
            reasons.append(Reason(code, check, "warn", score, detail or {}))
            if _RANK[level] < _RANK[WARN]:
                level = WARN

    for r in results:
        if r.score is not None:
            scores[r.check] = r.score
            apply(r.check, r.score, r.issue_code or r.check.upper())
        for f in r.findings:
            if f.severity == "info":
                reasons.append(Reason(f.code, r.check, "info", None, f.detail))

    # Target utama: teks terbaca. Jika teks terbaca, kejelasan foto (target berikutnya: paket terlihat jelas)
    # cukup diberi tahu ke kurir (WARN), tidak menahan status pengiriman.
    t_ocr = profile.thresholds.get("ocr")
    if (profile.text_first and t_ocr is not None and scores.get("ocr") is not None
            and scores["ocr"] >= (t_ocr.warn if t_ocr.warn is not None else t_ocr.reject or 0)):
        for r in reasons:
            if r.severity == "reject" and r.check in TEXT_FIRST_SOFTEN:
                r.severity = "warn"
                r.detail = {**r.detail, "softened": "text_readable"}
        if level == REJECT and not any(r.severity == "reject" for r in reasons):
            level = WARN

    auth, risks = authenticity_score(results)
    if auth is not None:
        scores["authenticity"] = auth
        t = profile.thresholds.get("authenticity")
        if t and ((t.reject is not None and auth < t.reject) or (t.warn is not None and auth < t.warn)):
            # satu alasan per temuan risiko, supaya ops tahu persis apa yang mencurigakan
            for f in risks:
                apply("authenticity", auth, f.code, {**f.detail, "points": f.points})

    action = {"shadow": "log", "warn": "notify", "enforce": "reject" if level == REJECT else "notify"}[profile.mode]
    if level == PASS:
        action = "log"
    order = {"reject": 0, "warn": 1, "info": 2}
    reasons.sort(key=lambda x: order[x.severity])
    return Verdict(verdict=level, action=action, scores=scores, reasons=reasons)
