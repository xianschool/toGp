#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

SCHEMA_VERSION = 1


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_font(value: str) -> str:
    return value.split("+")[-1]


def candidate(name: str, confidence: float, evidence: list[str]) -> dict:
    return {"family": name, "confidence": round(confidence, 3), "evidence": evidence}


def inspect_pdf(path: Path) -> dict:
    import pdfplumber

    fonts = Counter()
    page_sizes = Counter()
    char_count = 0
    line_count = 0
    rect_count = 0
    curve_count = 0
    image_count = 0
    text_pages = 0
    image_pages = 0
    pages = 0
    metadata = {}

    with pdfplumber.open(path) as document:
        pages = len(document.pages)
        metadata = {
            key: value
            for key, value in (document.metadata or {}).items()
            if key in {"Title", "Author", "Creator", "Producer", "CreationDate"}
            and isinstance(value, (str, int, float))
        }
        for page in document.pages:
            page_sizes[(round(page.width, 2), round(page.height, 2))] += 1
            page_chars = [char for char in page.chars if char.get("text", "").strip()]
            char_count += len(page_chars)
            line_count += len(page.lines)
            rect_count += len(page.rects)
            curve_count += len(page.curves)
            image_count += len(page.images)
            if page_chars:
                text_pages += 1
            if page.images:
                image_pages += 1
            for char in page_chars:
                font = normalized_font(str(char.get("fontname", "")))
                if font:
                    fonts[font] += 1

    if text_pages and (line_count or rect_count or curve_count) and not image_pages:
        render_type = "vector-text"
    elif text_pages and image_pages:
        render_type = "mixed-vector-raster"
    elif not text_pages and (line_count or rect_count or curve_count):
        render_type = "vector-outline"
    elif image_pages:
        render_type = "raster-pdf"
    else:
        render_type = "unknown"

    producer = " ".join(
        str(metadata.get(key, ""))
        for key in ("Producer", "Creator", "Title")
    ).lower()
    font_names = " ".join(fonts).lower()
    candidates = []

    for terms, family, confidence in [
        (("musescore",), "musescore", 0.98),
        (("dorico", "steinberg"), "dorico", 0.98),
        (("sibelius", "avid"), "sibelius", 0.98),
        (("finale", "makemusic"), "finale", 0.98),
        (("lilypond",), "lilypond", 0.98),
        (("guitar pro", "arobas"), "guitar-pro", 0.98),
    ]:
        matched = [term for term in terms if term in producer]
        if matched:
            candidates.append(candidate(family, confidence, [f"metadata contains {matched[0]}"]))

    for terms, family, confidence in [
        (("leland", "edwin"), "musescore", 0.72),
        (("opus", "inkpen"), "sibelius", 0.72),
        (("maestro", "engraver"), "finale", 0.72),
        (("emmentaler",), "lilypond", 0.82),
    ]:
        matched = [term for term in terms if term in font_names]
        if matched:
            candidates.append(candidate(family, confidence, [f"embedded font {matched[0]}"]))

    if any(name in font_names for name in ("bravura", "petaluma", "gonville")):
        candidates.append(
            candidate("smufl-font-user", 0.45, ["embedded SMuFL-family music font"])
        )

    if not candidates and render_type in {"vector-text", "vector-outline"}:
        candidates.append(
            candidate(
                "generic-vector-score",
                0.3,
                ["semantic text/vector structure without a known software signature"],
            )
        )

    candidates.sort(key=lambda item: item["confidence"], reverse=True)
    return {
        "kind": "pdf",
        "renderType": render_type,
        "pages": pages,
        "metadata": metadata,
        "fonts": [
            {"name": name, "characters": count}
            for name, count in fonts.most_common(30)
        ],
        "pageSizes": [
            {"width": size[0], "height": size[1], "count": count}
            for size, count in page_sizes.most_common(10)
        ],
        "objects": {
            "characters": char_count,
            "textPages": text_pages,
            "imagePages": image_pages,
            "images": image_count,
            "lines": line_count,
            "rectangles": rect_count,
            "curves": curve_count,
        },
        "sourceCandidates": candidates,
    }


def inspect_image(path: Path) -> dict:
    from PIL import Image
    import numpy as np

    with Image.open(path) as opened:
        width, height = opened.size
        mode = opened.mode
        dpi = opened.info.get("dpi")
        thumbnail = opened.convert("RGB")
        thumbnail.thumbnail((1200, 1200), Image.Resampling.BILINEAR)
        pixels = np.asarray(thumbnail)

    red, green, blue = (
        pixels[:, :, 0].astype(np.float32),
        pixels[:, :, 1].astype(np.float32),
        pixels[:, :, 2].astype(np.float32),
    )
    luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    red_ratio = float(
        ((red > 70) & (red > green * 1.1) & (red > blue * 1.1) & (green < 235)).mean()
    )
    dark_ratio = float((luminance < 80).mean())
    gray_ratio = float(((luminance >= 80) & (luminance < 235)).mean())
    background_ratio = float((luminance >= 235).mean())

    if red_ratio > 0.001 and background_ratio > 0.75:
        render_type = "digital-raster-tab"
        candidates = [
            candidate(
                "digital-tab-screenshot",
                0.75,
                ["bright uniform background", "red chord labels"],
            )
        ]
    elif background_ratio > 0.65 and gray_ratio > 0.05:
        render_type = "digital-raster"
        candidates = [
            candidate("generic-raster-score", 0.4, ["digital rendering characteristics"])
        ]
    else:
        render_type = "scanned-print"
        candidates = [
            candidate("scanned-publication", 0.4, ["non-uniform or dark background"])
        ]

    return {
        "kind": "image",
        "renderType": render_type,
        "width": width,
        "height": height,
        "mode": mode,
        "dpi": list(dpi) if isinstance(dpi, tuple) else dpi,
        "signals": {
            "darkRatio": round(dark_ratio, 5),
            "grayRatio": round(gray_ratio, 5),
            "backgroundRatio": round(background_ratio, 5),
            "redRatio": round(red_ratio, 5),
        },
        "sourceCandidates": candidates,
    }


def inspect(path: Path) -> dict:
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "path": str(path),
        "name": path.name,
        "extension": path.suffix.lower(),
        "size": path.stat().st_size,
        "sha256": file_digest(path),
    }
    if path.suffix.lower() == ".pdf":
        result.update(inspect_pdf(path))
    else:
        result.update(inspect_image(path))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect score files and report render/source fingerprints."
    )
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    results = []
    for path in args.paths:
        try:
            results.append(inspect(path))
        except Exception as error:
            results.append(
                {
                    "schemaVersion": SCHEMA_VERSION,
                    "path": str(path),
                    "name": path.name,
                    "error": str(error),
                }
            )
    print(
        json.dumps(
            results,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
        )
    )
    return 1 if any("error" in result for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
