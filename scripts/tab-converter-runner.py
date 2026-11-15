#!/usr/bin/env python3
from __future__ import annotations

import json
import io
import itertools
import math
import re
import sys
import traceback
import unicodedata
from pathlib import Path

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.write("\n")


def extract_pdf_metadata(data: bytes) -> tuple[str, str]:
    import pdfplumber

    try:
        with pdfplumber.open(io.BytesIO(data)) as document:
            page = document.pages[0]
            rows = []
            chars = sorted(page.chars, key=lambda char: (char["top"], char["x0"]))
            for key, group in itertools.groupby(chars, key=lambda char: round(char["top"] / 5) * 5):
                items = list(group)
                text = "".join(item["text"] for item in items).strip()
                if not text:
                    continue
                rows.append(
                    {
                        "top": float(key),
                        "x0": float(min(item["x0"] for item in items)),
                        "size": float(max(item["size"] for item in items)),
                        "text": unicodedata.normalize("NFKC", text),
                    }
                )
            if not rows:
                return "", ""
            title_row = max(
                (row for row in rows if row["top"] < page.height * 0.2),
                key=lambda row: row["size"],
                default=None,
            )
            if title_row is None:
                return "", ""
            title = title_row["text"]
            artist_rows = [
                row
                for row in rows
                if title_row["top"] < row["top"] <= title_row["top"] + 3 * title_row["size"]
                and row["x0"] < page.width * 0.25
                and 2 <= len(row["text"]) <= 30
                and not row["text"].startswith("=")
                and row["size"] < title_row["size"]
            ]
            artist = max(artist_rows, key=lambda row: row["size"], default={"text": ""})["text"]
            return title, artist
    except Exception:
        return "", ""


def patch_tab_reader() -> None:
    import pdf_to_gp5.converter as converter
    import pdf_to_gp5.extract.engraved_tab as engraved
    from pdf_to_gp5.extract.pdf_reader import Segment, group_lines

    if getattr(engraved, "_togp_patched", False):
        return

    prefer_rectangular_bar_lines = False
    original_vertical = Segment.is_vertical.fget

    def staff_lines(segments, page_width):
        horizontals = sorted(
            (
                segment
                for segment in segments
                if segment.is_horizontal and (segment.x1 - segment.x0) > 2
            ),
            key=lambda segment: (round((segment.top + segment.bottom) / 2, 1), segment.x0),
        )
        lines = []
        for segment in horizontals:
            y = (segment.top + segment.bottom) / 2
            if (
                lines
                and abs(lines[-1].y - y) <= 0.5
                and segment.x0 <= lines[-1].x1 + 0.1 * page_width
            ):
                lines[-1].x0 = min(lines[-1].x0, segment.x0)
                lines[-1].x1 = max(lines[-1].x1, segment.x1)
            else:
                lines.append(engraved._StaffLine(y, segment.x0, segment.x1))
        return [line for line in lines if line.x1 - line.x0 > 0.08 * page_width]

    def is_vertical(segment):
        width = segment.x1 - segment.x0
        height = segment.bottom - segment.top
        if prefer_rectangular_bar_lines:
            return 1 <= width <= 8 and height > 3 * (width + 0.1)
        return original_vertical(segment)

    original_section_labels = engraved.section_labels

    def section_labels(page, top, x0, x1, spacing):
        labels = list(original_section_labels(page, top, x0, x1, spacing))
        known_sections = re.compile(
            r"(Intro|Verse\s*\d*|Chorus|Inter|Bridge|Outro)",
            re.IGNORECASE,
        )
        for line in group_lines(page.chars):
            if not (top - 16 * spacing <= line.yc <= top - 0.3 * spacing):
                continue
            if not (x0 - spacing <= line.chars[0].x0 <= x1):
                continue
            raw = "".join(char.text for char in line.chars).strip()
            font = line.chars[0].font
            if not raw or not font.startswith("PingFang"):
                continue
            match = known_sections.search(raw)
            if match:
                text = match.group(1)
                labels.append((line.chars[0].x0, text))
        return labels

    original_extract = converter.extract_engraved_systems
    original_shared_bars = engraved.shared_bars

    def extract_engraved_systems(page):
        nonlocal prefer_rectangular_bar_lines
        rectangular = sum(
            1
            for segment in page.segments
            if 1 <= segment.x1 - segment.x0 <= 8
            and segment.bottom - segment.top > 100
        )
        previous = prefer_rectangular_bar_lines
        prefer_rectangular_bar_lines = rectangular >= 4
        try:
            systems = original_extract(page)
            ignored_lyrics = {
                "A",
                "B",
                "C",
                "D",
                "E",
                "F",
                "G",
                "H",
                "I",
                "Intro",
                "Verse",
                "Verse2",
                "Chorus",
                "Inter",
                "Bridge",
                "Outro",
                "H",
                "P",
                "sl.",
                "PH",
            }
            for system in systems:
                system.lyrics = [
                    lyric for lyric in system.lyrics if lyric[1] not in ignored_lyrics
                ]
            return systems
        finally:
            prefer_rectangular_bar_lines = previous

    engraved._staff_lines = staff_lines
    engraved.section_labels = section_labels
    engraved.shared_bars = lambda groups, tolerance: original_shared_bars(groups, tolerance * 2.5)
    Segment.is_vertical = property(is_vertical)
    converter.extract_engraved_systems = extract_engraved_systems
    engraved._togp_patched = True


def normalize_image(opened, width: int, height: int):
    from PIL import Image

    if width * height > 250_000_000:
        raise ValueError(f"图片尺寸过大：{width}×{height}")
    image = opened.convert("RGB")
    image.thumbnail((6000, 6000), Image.Resampling.LANCZOS)
    pixels = image.width * image.height
    if pixels > 39_000_000:
        scale = math.sqrt(39_000_000 / pixels)
        image = image.resize(
            (round(image.width * scale), round(image.height * scale)),
            Image.Resampling.LANCZOS,
        )
    return image


def combine_images(images: list[Path], destination: Path) -> None:
    from PIL import Image, ImageOps

    pages = []
    Image.MAX_IMAGE_PIXELS = 250_000_000
    try:
        for source in images:
            with Image.open(source) as opened:
                width, height = opened.size
                page = normalize_image(ImageOps.exif_transpose(opened), width, height)
                pages.append(page)
        pages[0].save(
            destination,
            "PDF",
            save_all=True,
            append_images=pages[1:],
            resolution=150.0,
            quality=95,
        )
    finally:
        for page in pages:
            page.close()


def run(job_path: Path, output_path: Path) -> None:
    from pdf_to_gp5 import i18n
    from pdf_to_gp5.converter import (
        ConversionError,
        ConversionOptions,
        TrackOptions,
        convert_many,
    )

    patch_tab_reader()
    i18n.use("en")
    job = json.loads(job_path.read_text(encoding="utf-8"))
    inputs = [Path(value) for value in job["inputs"]]
    images = [path for path in inputs if path.suffix.lower() in IMAGE_EXTENSIONS]
    pdfs = [path for path in inputs if path.suffix.lower() == ".pdf"]

    documents: list[bytes] = []
    filenames: list[str] = []
    pdf_documents: list[bytes] = []
    if images:
        combined_pdf = output_path.with_name("images.pdf")
        combine_images(images, combined_pdf)
        documents.append(combined_pdf.read_bytes())
        filenames.append(images[0].stem)

    for source in pdfs:
        data = source.read_bytes()
        documents.append(data)
        pdf_documents.append(data)
        filenames.append(source.stem)

    if not documents:
        raise ConversionError("没有可识别的 PDF 或图片")

    title = artist = ""
    if pdf_documents:
        title, artist = extract_pdf_metadata(pdf_documents[0])

    options = ConversionOptions(
        title=title,
        artist=artist,
        tracks=tuple(TrackOptions(filename=name) for name in filenames),
        max_pages=40,
        max_image_pages=30,
        max_image_pixels=40_000_000,
        max_events=50_000,
        max_measures=2_000,
    )
    result = convert_many(documents, options)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(result.gp5)
    emit({"ok": True, "report": result.report})


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "--check":
        import guitarpro  # noqa: F401
        import pdf_to_gp5  # noqa: F401
        import pdfplumber  # noqa: F401

        emit({"ok": True})
        return 0

    if len(sys.argv) != 3:
        emit({"ok": False, "error": "转换器参数无效"})
        return 2

    try:
        run(Path(sys.argv[1]), Path(sys.argv[2]))
        return 0
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        emit({"ok": False, "error": str(error)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
