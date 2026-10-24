"""Registry analyzer. Menambah check baru = buat class analyzer lalu daftarkan di REGISTRY."""

from engine.analyzers.base import AnalysisContext, CheckResult, DuplicateMatch, Finding
from engine.analyzers.duplicate import DuplicateAnalyzer
from engine.analyzers.exif import ExifAnalyzer
from engine.analyzers.exposure import ExposureAnalyzer
from engine.analyzers.ocr import OcrAnalyzer
from engine.analyzers.resolution import ResolutionAnalyzer
from engine.analyzers.sharpness import SharpnessAnalyzer

REGISTRY = {
    a.name: a
    for a in (
        SharpnessAnalyzer(),
        ResolutionAnalyzer(),
        ExposureAnalyzer(),
        ExifAnalyzer(),
        DuplicateAnalyzer(),
        OcrAnalyzer(),
    )
}

__all__ = ["REGISTRY", "AnalysisContext", "CheckResult", "DuplicateMatch", "Finding"]
