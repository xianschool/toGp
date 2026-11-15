#!/usr/bin/env python3
from __future__ import annotations

import hashlib
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
    return {
        "family": name,
        "confidence": round(confidence, 3),
        "evidence": evidence,
    }


def pdf_render_type(
    text_pages: int,
    image_pages: int,
    lines: int,
    rectangles: int,
    curves: int,
) -> str:
    has_vectors = bool(lines or rectangles or curves)
    if text_pages and has_vectors and not image_pages:
        return "vector-text"
    if text_pages and image_pages:
        return "mixed-vector-raster"
    if not text_pages and has_vectors:
        return "vector-outline"
    if image_pages:
        return "raster-pdf"
    return "unknown"


def pdf_source_candidates(
    render_type: str,
    metadata: dict,
    font_names: str,
) -> list[dict]:
    producer = " ".join(
        str(metadata.get(key, ""))
        for key in ("Producer", "Creator", "Title")
    ).lower()
    candidates = []
    for terms, family, confidence in (
        (("musescore",), "musescore", 0.98),
        (("dorico", "steinberg"), "dorico", 0.98),
        (("sibelius", "avid"), "sibelius", 0.98),
        (("finale", "makemusic"), "finale", 0.98),
        (("lilypond",), "lilypond", 0.98),
        (("guitar pro", "arobas"), "guitar-pro", 0.98),
    ):
        matched = [term for term in terms if term in producer]
        if matched:
            candidates.append(
                candidate(
                    family,
                    confidence,
                    [f"metadata contains {matched[0]}"],
                )
            )

    for terms, family, confidence in (
        (("leland", "edwin"), "musescore", 0.72),
        (("opus", "inkpen"), "sibelius", 0.72),
        (("maestro", "engraver"), "finale", 0.72),
        (("emmentaler",), "lilypond", 0.82),
        (("gpbravura",), "guitar-pro", 0.95),
    ):
        matched = [term for term in terms if term in font_names]
        if matched:
            candidates.append(
                candidate(family, confidence, [f"embedded font {matched[0]}"])
            )

    if any(name in font_names for name in ("bravura", "petaluma", "gonville")):
        candidates.append(
            candidate(
                "smufl-font-user",
                0.45,
                ["embedded SMuFL-family music font"],
            )
        )

    if not candidates and render_type in {"vector-text", "vector-outline"}:
        candidates.append(
            candidate(
                "generic-vector-score",
                0.3,
                ["semantic text/vector structure without a known software signature"],
            )
        )
    combined = {}
    for item in candidates:
        current = combined.get(item["family"])
        if current is None:
            combined[item["family"]] = item
            continue
        current["confidence"] = max(current["confidence"], item["confidence"])
        current["evidence"] = list(
            dict.fromkeys(current["evidence"] + item["evidence"])
        )
    return sorted(
        combined.values(),
        key=lambda item: item["confidence"],
        reverse=True,
    )


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

    render_type = pdf_render_type(
        text_pages,
        image_pages,
        line_count,
        rect_count,
        curve_count,
    )
    candidates = pdf_source_candidates(
        render_type,
        metadata,
        " ".join(fonts).lower(),
    )
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


def image_signals(pixels) -> dict:
    red, green, blue = (
        pixels[:, :, 0].astype("float32"),
        pixels[:, :, 1].astype("float32"),
        pixels[:, :, 2].astype("float32"),
    )
    luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    red_mask = (red > 70) & (red > green * 1.1) & (red > blue * 1.1) & (green < 235)
    return {
        "darkRatio": round(float((luminance < 80).mean()), 5),
        "grayRatio": round(
            float(((luminance >= 80) & (luminance < 235)).mean()),
            5,
        ),
        "backgroundRatio": round(float((luminance >= 235).mean()), 5),
        "redRatio": round(float(red_mask.mean()), 5),
    }


def image_classification(signals: dict) -> dict:
    red_ratio = signals["redRatio"]
    dark_ratio = signals["darkRatio"]
    gray_ratio = signals["grayRatio"]
    background_ratio = signals["backgroundRatio"]
    if red_ratio > 0.001 and background_ratio > 0.75:
        return {
            "renderType": "digital-raster-tab",
            "visualProfile": "red-chord-tab",
            "sourceCandidates": [
                candidate(
                    "digital-tab-screenshot",
                    0.75,
                    ["bright uniform background", "red chord labels"],
                )
            ],
        }
    if (
        red_ratio < 0.001
        and dark_ratio < 0.04
        and gray_ratio > 0.06
        and background_ratio > 0.75
    ):
        return {
            "renderType": "digital-raster",
            "visualProfile": "monochrome-watermark-tab",
            "sourceCandidates": [
                candidate(
                    "generic-raster-score",
                    0.4,
                    ["digital rendering characteristics", "faint monochrome staff ink"],
                )
            ],
        }
    if background_ratio > 0.65 and gray_ratio > 0.05:
        return {
            "renderType": "digital-raster",
            "visualProfile": "generic",
            "sourceCandidates": [
                candidate(
                    "generic-raster-score",
                    0.4,
                    ["digital rendering characteristics"],
                )
            ],
        }
    return {
        "renderType": "scanned-print",
        "visualProfile": "scanned-print",
        "sourceCandidates": [
            candidate(
                "scanned-publication",
                0.4,
                ["non-uniform or dark background"],
            )
        ],
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

    signals = image_signals(pixels)
    classification = image_classification(signals)
    return {
        "kind": "image",
        "width": width,
        "height": height,
        "mode": mode,
        "dpi": list(dpi) if isinstance(dpi, tuple) else dpi,
        "signals": signals,
        **classification,
    }


def inspect_score_source(path: Path, include_digest: bool = True) -> dict:
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "path": str(path),
        "name": path.name,
        "extension": path.suffix.lower(),
        "size": path.stat().st_size,
    }
    if include_digest:
        result["sha256"] = file_digest(path)
    if path.suffix.lower() == ".pdf":
        result.update(inspect_pdf(path))
    else:
        result.update(inspect_image(path))
    return result


def aggregate_source_diagnosis(results: list[dict]) -> dict:
    valid = [result for result in results if "error" not in result]
    render_types = sorted({result.get("renderType", "unknown") for result in valid})
    combined_candidates = {}
    for result in valid:
        for item in result.get("sourceCandidates", []):
            family = item["family"]
            current = combined_candidates.get(family)
            if current is None or item["confidence"] > current["confidence"]:
                combined_candidates[family] = {
                    "family": family,
                    "confidence": item["confidence"],
                    "evidence": list(dict.fromkeys(item.get("evidence", []))),
                }
            else:
                current["evidence"] = list(
                    dict.fromkeys(current["evidence"] + item.get("evidence", []))
                )
    candidates = sorted(
        combined_candidates.values(),
        key=lambda item: item["confidence"],
        reverse=True,
    )
    fonts = sorted(
        {
            font["name"]
            for result in valid
            for font in result.get("fonts", [])
            if font.get("name")
        }
    )
    image_results = [result for result in valid if result.get("kind") == "image"]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "renderTypes": render_types,
        "renderType": (
            render_types[0]
            if len(render_types) == 1
            else ("mixed" if render_types else "unknown")
        ),
        "sourceCandidates": candidates,
        "features": {
            "files": len(valid),
            "imageFiles": len(image_results),
            "pdfFiles": len(valid) - len(image_results),
            "embeddedFonts": fonts,
            "redChordImages": sum(
                result.get("visualProfile") == "red-chord-tab"
                for result in image_results
            ),
            "monochromeImages": sum(
                result.get("visualProfile") == "monochrome-watermark-tab"
                for result in image_results
            ),
        },
        "files": [
            {
                "name": result.get("name"),
                "kind": result.get("kind"),
                "renderType": result.get("renderType"),
                "visualProfile": result.get("visualProfile"),
                "sourceCandidates": result.get("sourceCandidates", []),
                "error": result.get("error"),
            }
            for result in results
        ],
    }
