"""Representasi dokumen hasil baca (halaman + blok teks ber-koordinat) dan helper tata letak."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Block:
    id: int
    page: int
    text: str
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1 relatif halaman (0-1), (0,0) kiri atas
    conf: float  # 0-1
    source: str  # pdf_text | ocr

    @property
    def cx(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2

    @property
    def cy(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2

    @property
    def height(self) -> float:
        return max(1e-4, self.bbox[3] - self.bbox[1])

    def same_row(self, other: Block) -> bool:
        return self.page == other.page and (self.bbox[1] <= other.cy <= self.bbox[3]
                                            or other.bbox[1] <= self.cy <= other.bbox[3])

    def to_dict(self) -> dict[str, Any]:
        return {"page": self.page + 1, "text": self.text, "bbox": [round(v, 4) for v in self.bbox],
                "confidence": round(self.conf, 3), "source": self.source}


@dataclass
class Document:
    pages: list[dict[str, Any]]
    blocks: list[Block] = field(default_factory=list)
    qr: list[str] = field(default_factory=list)
    versions: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_read(cls, raw: dict[str, Any]) -> Document:
        blocks: list[Block] = []
        for p in raw.get("pages", []):
            for ln in sorted(p.get("lines", []), key=lambda x: (round(x["bbox"][1], 3), x["bbox"][0])):
                text = " ".join(str(ln.get("text", "")).split())
                if text:
                    blocks.append(Block(len(blocks), int(p["index"]), text, tuple(ln["bbox"]),  # type: ignore[arg-type]
                                        float(ln.get("conf", 0.0)), p.get("source", "ocr")))
        return cls(pages=raw.get("pages", []), blocks=blocks, qr=list(raw.get("qr", [])),
                   versions=dict(raw.get("versions", {})))

    @classmethod
    def from_blocks(cls, blocks: list[dict[str, Any]]) -> Document:
        """Bangun ulang dari `blocks` hasil job yang tersimpan (dipakai saat belajar template)."""
        out = [Block(i, int(b["page"]) - 1, b["text"], tuple(b["bbox"]), float(b.get("confidence", 0.0)),  # type: ignore[arg-type]
                     b.get("source", "ocr")) for i, b in enumerate(blocks)]
        return cls(pages=[], blocks=out)

    @property
    def sources(self) -> set[str]:
        return {p.get("source", "ocr") for p in self.pages} or {b.source for b in self.blocks}

    @property
    def text(self) -> str:
        """Teks penuh urut baca: per halaman, per baris (blok di baris yang sama digabung dengan spasi)."""
        out, last_page = [], None
        for row in self.rows():
            if last_page is not None and row[0].page != last_page:
                out.append("\f")
            last_page = row[0].page
            out.append("  ".join(b.text for b in row))
        return "\n".join(out)

    def rows(self) -> list[list[Block]]:
        """Blok dikelompokkan per baris visual (urut atas → bawah, kiri → kanan)."""
        rows: list[list[Block]] = []
        for b in sorted(self.blocks, key=lambda x: (x.page, x.cy)):
            if rows and rows[-1][0].page == b.page and any(b.same_row(o) for o in rows[-1]):
                rows[-1].append(b)
            else:
                rows.append([b])
        for r in rows:
            r.sort(key=lambda x: x.bbox[0])
        return rows

    def right_of(self, b: Block) -> list[Block]:
        return sorted((o for o in self.blocks if o.id != b.id and b.same_row(o) and o.bbox[0] >= b.bbox[2] - 0.01),
                      key=lambda o: o.bbox[0])

    def below(self, b: Block, max_lines: float = 2.5) -> list[Block]:
        out = []
        for o in self.blocks:
            if o.page != b.page or o.id == b.id or o.bbox[1] < b.bbox[3] - b.height * 0.3:
                continue
            if o.bbox[1] - b.bbox[3] > max_lines * b.height:
                continue
            if o.bbox[2] < b.bbox[0] or o.bbox[0] > b.bbox[2] + 0.15:  # harus sejajar kolom label
                continue
            out.append(o)
        return sorted(out, key=lambda o: (o.bbox[1], abs(o.bbox[0] - b.bbox[0])))
