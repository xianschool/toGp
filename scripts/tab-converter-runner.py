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
from bisect import bisect_left, bisect_right
from pathlib import Path

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
CHORD_NAME = re.compile(
    r"^[A-G][#b]?(?:m|maj|min|sus|add|dim|aug|\u00b0)?\d*(?:[-+]\d+)?"
    r"(?:/[A-G][#b]?\d*(?:[-+]\d+)?)?$",
    re.IGNORECASE,
)
FALLBACK_CHORD_SHAPES = {
    "A": {1: 0, 2: 2, 3: 2, 4: 2, 5: 0},
    "Asus2": {1: 0, 2: 0, 3: 2, 4: 2, 5: 0},
    "Asus4": {1: 0, 2: 3, 3: 2, 4: 2, 5: 0},
    "B7": {1: 2, 2: 0, 3: 2, 4: 1, 5: 2},
    "Bm": {1: 2, 2: 3, 3: 4, 4: 4, 5: 2},
    "C": {1: 0, 2: 1, 3: 0, 4: 2, 5: 3},
    "D": {1: 2, 2: 3, 3: 2},
    "Em": {1: 0, 2: 0, 3: 0, 4: 2, 5: 2, 6: 0},
    "Em/D": {1: 0, 2: 0, 3: 0, 4: 0},
    "Em/D#": {1: 0, 2: 0, 3: 0, 4: 1},
    "F#7": {1: 2, 2: 2, 3: 3, 4: 2, 5: 4, 6: 2},
    "F#m7": {1: 2, 2: 2, 3: 2, 4: 2, 5: 4, 6: 2},
}


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
    import pdf_to_gp5.extract.raster_tab as raster
    from pdf_to_gp5.extract.pdf_reader import Segment, group_lines

    raster._DARK = 210
    if getattr(engraved, "_togp_patched", False):
        return engraved._togp_captured_pages

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
    captured_pages = {}

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
            staves = [
                staff
                for staff in engraved._staves(
                    engraved._staff_lines(page.segments, page.width)
                )
                if len(staff) == 6
            ]
            tab_systems = [
                (system, staff)
                for system, staff in zip(systems, staves, strict=False)
                if system.string_count == 6
            ]
            if tab_systems:
                captured_pages[page.number] = {
                    "width": page.width,
                    "systems": [
                        {
                            "bars": list(system.bars),
                            "top": staff[0].y,
                            "events": len(system.events),
                        }
                        for system, staff in tab_systems
                    ],
                }
            return systems
        finally:
            prefer_rectangular_bar_lines = previous

    engraved._staff_lines = staff_lines
    engraved.section_labels = section_labels
    engraved.shared_bars = lambda groups, tolerance: original_shared_bars(groups, tolerance * 2.5)
    Segment.is_vertical = property(is_vertical)
    converter.extract_engraved_systems = extract_engraved_systems
    engraved._togp_patched = True
    engraved._togp_captured_pages = captured_pages
    return captured_pages


def detect_chord_labels(path: Path) -> list[dict]:
    from PIL import Image
    import cv2
    import numpy as np
    import pdf_to_gp5.extract.raster_tab as raster

    pixels = np.asarray(Image.open(path).convert("RGB"))
    red, green, blue = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
    mask = (
        (red > 70)
        & (red > green * 1.1)
        & (red > blue * 1.1)
        & (green < 235)
    ).astype(np.uint8) * 255
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8), iterations=1)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    recognizer = raster._recognizer()
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        if not (8 <= width <= 110 and 8 <= height <= 35):
            continue
        crop = pixels[
            max(0, y - 3) : min(pixels.shape[0], y + height + 3),
            max(0, x - 3) : min(pixels.shape[1], x + width + 3),
        ]
        try:
            recognized, _ = recognizer(crop)
        except Exception:
            continue
        if not recognized:
            continue
        text = recognized[0][0].strip()
        confidence = float(recognized[0][1])
        compact = text.replace(" ", "")
        if confidence >= 0.5 and CHORD_NAME.fullmatch(compact):
            candidates.append(
                {
                    "x0": x,
                    "x1": x + width,
                    "y": y + height / 2,
                    "text": compact,
                }
            )

    rows = []
    for _, group in itertools.groupby(
        sorted(candidates, key=lambda item: (round(item["y"] / 8), item["x0"])),
        key=lambda item: round(item["y"] / 8),
    ):
        merged = []
        for item in sorted(group, key=lambda item: item["x0"]):
            if merged and item["x0"] - merged[-1]["x1"] <= 45:
                merged[-1]["text"] += f" {item['text']}"
                merged[-1]["x1"] = item["x1"]
            else:
                merged.append(dict(item))
        rows.extend(merged)
    ordered = sorted(rows, key=lambda item: (item["y"], item["x0"]))
    for item in ordered:
        fallback = FALLBACK_CHORD_SHAPES.get(item["text"].split(" ", 1)[0], {})
        detected = chord_shape_from_diagram(pixels, item)
        item["shape"] = {**fallback, **detected}
    return ordered


def chord_shape_from_diagram(pixels, label: dict) -> dict[int, int]:
    import cv2
    import numpy as np

    center_x = (label["x0"] + label["x1"]) / 2
    x0 = max(0, round(center_x - 70))
    x1 = min(pixels.shape[1], round(center_x + 70))
    y0 = max(0, round(label["y"] + 5))
    y1 = min(pixels.shape[0], round(label["y"] + 105))
    crop = pixels[y0:y1, x0:x1]
    if crop.size == 0:
        return {}
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    dark = (gray < 90).astype(np.uint8) * 255
    connected = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(
        connected, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    boxes = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        if 30 <= width <= 90 and 30 <= height <= 90:
            boxes.append((width * height, x, y, width, height))
    if not boxes:
        return {}
    _, x, y, width, height = max(boxes)
    grid = (gray[y : y + height, x : x + width] < 100).astype(np.uint8)

    def grouped_lines(values):
        groups = []
        for value in values:
            if groups and value - groups[-1][-1] <= 2:
                groups[-1].append(value)
            else:
                groups.append([value])
        return [sum(group) / len(group) for group in groups]

    columns = grouped_lines(
        [
            index
            for index, count in enumerate(grid.sum(axis=0))
            if count > height * 0.55
        ]
    )
    rows = grouped_lines(
        [
            index
            for index, count in enumerate(grid.sum(axis=1))
            if count > width * 0.55
        ]
    )
    if len(columns) < 6 or len(rows) < 3:
        return {}

    dots = cv2.erode((gray[y : y + height, x : x + width] < 80).astype(np.uint8), np.ones((2, 2), np.uint8))
    count, _, stats, centers = cv2.connectedComponentsWithStats(dots, 8)
    shape = {}
    for component in range(1, count):
        component_x, component_y, component_width, component_height, area = stats[
            component
        ]
        if not (3 <= component_width <= 12 and 3 <= component_height <= 12):
            continue
        if area < 6:
            continue
        center_y = centers[component][1]
        fret = bisect_left(rows, center_y)
        if fret < 1:
            continue
        column = min(
            range(6),
            key=lambda index: abs(centers[component][0] - columns[index]),
        )
        string_number = 6 - column
        shape[string_number] = fret
    return shape


def extract_image_metadata(path: Path) -> tuple[str, str]:
    from PIL import Image
    import numpy as np
    import pdf_to_gp5.extract.raster_tab as raster

    pixels = np.asarray(Image.open(path).convert("RGB"))
    boxes, _ = raster._detector()(pixels)
    if boxes is None:
        return "", ""
    title_candidates = []
    for box in boxes:
        x0 = float(np.min(box[:, 0]))
        x1 = float(np.max(box[:, 0]))
        y0 = float(np.min(box[:, 1]))
        y1 = float(np.max(box[:, 1]))
        if y1 > pixels.shape[0] * 0.18:
            continue
        crop = pixels[
            max(0, round(y0) - 2) : min(pixels.shape[0], round(y1) + 2),
            max(0, round(x0) - 2) : min(pixels.shape[1], round(x1) + 2),
        ]
        try:
            recognized, _ = raster._recognizer()(crop)
        except Exception:
            continue
        if recognized:
            text = recognized[0][0].strip()
            confidence = float(recognized[0][1])
            if text and confidence >= 0.5:
                title_candidates.append(
                    {
                        "text": text,
                        "x0": x0,
                        "x1": x1,
                        "y0": y0,
                        "y1": y1,
                        "height": y1 - y0,
                    }
                )
    if not title_candidates:
        return "", ""
    title = max(title_candidates, key=lambda item: item["height"])
    artist_candidates = [
        item
        for item in title_candidates
        if title["y1"] <= item["y0"] <= title["y1"] + 2 * title["height"]
        and item["height"] < title["height"]
        and abs((item["x0"] + item["x1"]) / 2 - pixels.shape[1] / 2)
        < pixels.shape[1] * 0.25
    ]
    artist = min(
        artist_candidates,
        key=lambda item: abs(item["y0"] - title["y1"]),
        default={"text": ""},
    )["text"]
    return unicodedata.normalize("NFKC", title["text"]), unicodedata.normalize(
        "NFKC", artist
    )


def add_chords_to_gp5(
    gp5: bytes,
    captured_pages: dict,
    images: list[Path],
) -> tuple[bytes, list[dict]]:
    import guitarpro as gp

    song = gp.parse(io.BytesIO(gp5))
    if not song.tracks:
        return gp5, []
    track = song.tracks[0]
    chord_labels = {
        page_number: detect_chord_labels(image)
        for page_number, image in enumerate(images, start=1)
    }
    measure_offset = 0
    markers = []
    active_shape = {}

    for page_number in sorted(captured_pages):
        captured = captured_pages[page_number]
        systems = captured["systems"]
        image = images[page_number - 1]
        from PIL import Image

        with Image.open(image) as opened:
            image_width = opened.width
        scale = captured["width"] / image_width
        labels = chord_labels.get(page_number, [])
        assigned = [[] for _ in systems]
        unmatched = []
        for label in labels:
            y = label["y"] * scale
            candidates = [
                (
                    abs(system["top"] - y),
                    index,
                )
                for index, system in enumerate(systems)
                if system["top"] >= y - 5
            ]
            if candidates:
                assigned[min(candidates)[1]].append(label)
            else:
                unmatched.append(label)

        for index, system in enumerate(systems):
            bars = system["bars"]
            by_measure = {}
            for label in assigned[index]:
                x = label["x0"] * scale
                local_measure = min(
                    max(bisect_right(bars, x) - 1, 0),
                    max(0, len(bars) - 2),
                )
                by_measure.setdefault(local_measure, []).append(label)
            for local_measure in range(max(0, len(bars) - 1)):
                measure_index = measure_offset + local_measure
                if measure_index >= len(track.measures):
                    continue
                labels_here = by_measure.get(local_measure, [])
                if labels_here:
                    name = " / ".join(
                        dict.fromkeys(label["text"] for label in labels_here)
                    )[:22]
                    shape = {}
                    for label in labels_here:
                        shape.update(label.get("shape", {}))
                    active_shape = shape
                    beat = track.measures[measure_index].voices[0].beats[0]
                    beat.effect.chord = gp.Chord(
                        length=len(track.strings),
                        name=name,
                        firstFret=0,
                        add=False,
                        show=False,
                        strings=[-1] * len(track.strings),
                    )
                    markers.append(
                        {
                            "bar": measure_index + 1,
                            "name": name,
                            "shape": dict(sorted(shape.items())),
                        }
                    )
                if active_shape:
                    for voice in track.measures[measure_index].voices:
                        for chord_beat in voice.beats:
                            for note in chord_beat.notes:
                                if note.type != gp.NoteType.dead:
                                    continue
                                note.type = gp.NoteType.normal
                                note.value = active_shape.get(note.string, 0)
            measure_offset += max(0, len(bars) - 1)

    buffer = io.BytesIO()
    gp.write(song, buffer, version=(5, 1, 0))
    return buffer.getvalue(), markers


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

    captured_pages = patch_tab_reader()
    captured_pages.clear()
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
    if images:
        title, artist = extract_image_metadata(images[0])
    elif pdf_documents:
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
    if images and not pdfs:
        gp5, chords = add_chords_to_gp5(result.gp5, captured_pages, images)
        result.gp5 = gp5
        result.report["chords"] = chords
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
