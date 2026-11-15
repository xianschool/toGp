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
from collections import Counter
from pathlib import Path

from score_source_model import aggregate_source_diagnosis, inspect_score_source

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
ACTIVE_TAB_PROFILE = "generic"
TAB_SCORE_SCHEMA_VERSION = 1
TAB_ADAPTERS = {
    "generic": {
        "displayName": "通用六线谱适配",
        "description": "使用通用矢量/图像回退规则。",
    },
    "monochrome-watermark-tab": {
        "displayName": "黑白水印六线谱",
        "description": "补充浅色系统弦线，并修正水印打印版的三小节系统。",
    },
    "red-chord-tab": {
        "displayName": "红色和弦数字谱",
        "description": "按红色和弦标签和和弦图定位，并将六线谱 X 转换为当前和弦品位。",
    },
}
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
        lines = [line for line in lines if line.x1 - line.x0 > 0.08 * page_width]
        if ACTIVE_TAB_PROFILE != "monochrome-watermark-tab":
            return lines

        runs = []
        for line in lines:
            if (
                runs
                and line.y - runs[-1][-1].y <= 15
                and abs(line.x0 - runs[-1][-1].x0) < 5
                and abs(line.x1 - runs[-1][-1].x1) < 5
            ):
                runs[-1].append(line)
            else:
                runs.append([line])
        dominant = Counter(
            len(run) for run in runs if 4 <= len(run) <= 8
        ).most_common(1)
        target_count = dominant[0][0] if dominant and dominant[0][0] >= 5 else 6
        completed = []
        for run in runs:
            # MuseScore pages draw the tab across the page. Short horizontal runs are
            # title rules or text strokes, not tab staves.
            if run[-1].x1 - run[0].x0 < 0.55 * page_width:
                continue
            missing = target_count - len(run)
            if len(run) < 2:
                completed.extend(run)
                continue
            spacing = sorted(
                run[index + 1].y - run[index].y for index in range(len(run) - 1)
            )[len(run) // 2]
            group = list(run)
            if missing >= 2:
                group.append(
                    engraved._StaffLine(
                        run[0].y - spacing,
                        run[0].x0,
                        run[0].x1,
                    )
                )
            if missing >= 1:
                group.append(
                    engraved._StaffLine(
                        run[-1].y + spacing,
                        run[-1].x0,
                        run[-1].x1,
                    )
                )
            group.sort(key=lambda line: line.y)
            # Uneven JPEG/watermark antialiasing can leave the detected staff lines a
            # fraction of a pixel off. Normalize them so the upstream grouping accepts all six.
            step = (group[-1].y - group[0].y) / (len(group) - 1)
            x0 = min(line.x0 for line in group)
            x1 = max(line.x1 for line in group)
            completed.extend(
                engraved._StaffLine(group[0].y + index * step, x0, x1)
                for index in range(len(group))
            )
        return sorted(completed, key=lambda line: line.y)

    original_find_staves = raster._find_staves

    def find_staves(gray, cv2, np):
        saved_dark = raster._DARK
        raster._DARK = 220
        try:
            dark, groups, spacing = original_find_staves(gray, cv2, np)
            if ACTIVE_TAB_PROFILE != "monochrome-watermark-tab":
                return dark, groups, spacing
            # A gray watermark can hide one complete staff group at the normal threshold.
            # Add only non-overlapping full-width groups found with the looser threshold.
            raster._DARK = 230
            _, faint_groups, _ = original_find_staves(gray, cv2, np)
        finally:
            raster._DARK = saved_dark
        if not groups or not faint_groups:
            return dark, groups, spacing
        minimum_width = 0.55 * gray.shape[1]
        existing = [(group[0][0], group[-1][0]) for group in groups]
        for group in faint_groups:
            width = group[0][4] - group[0][3]
            top, bottom = group[0][0], group[-1][0]
            if width < minimum_width or any(
                top <= end and bottom >= start for start, end in existing
            ):
                continue
            groups.append(group)
            existing.append((top, bottom))
        groups.sort(key=lambda group: group[0][0])
        spacing = float(
            np.median(
                [np.median(np.diff([line[0] for line in group])) for group in groups]
            )
        )
        return dark, groups, spacing

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
            if ACTIVE_TAB_PROFILE == "monochrome-watermark-tab":
                for system in systems:
                    if system.string_count != 6 or len(system.bars) - 1 == 3:
                        continue
                    # This MuseScore print layout uses three bars per system. Missing or
                    # doubled bar paths must not shift every following bar number.
                    start = system.bars[0] if system.bars else system.start_x
                    end = system.bars[-1] if system.bars else system.end_x
                    system.bars = [
                        start + (end - start) * index / 3 for index in range(4)
                    ]
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
    raster._find_staves = find_staves
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


def extract_image_metadata(path: Path) -> dict:
    from PIL import Image
    import numpy as np
    import pdf_to_gp5.extract.raster_tab as raster

    with Image.open(path) as opened:
        pixels = np.asarray(opened.convert("RGB"))
    boxes, _ = raster._detector()(pixels)
    if boxes is None:
        return {}
    title_candidates = []
    for box in boxes:
        x0 = float(np.min(box[:, 0]))
        x1 = float(np.max(box[:, 0]))
        y0 = float(np.min(box[:, 1]))
        y1 = float(np.max(box[:, 1]))
        if y1 > pixels.shape[0] * 0.22:
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
        return {}
    title = max(title_candidates, key=lambda item: item["height"])

    def compact(text: str) -> str:
        return unicodedata.normalize("NFKC", text).strip()

    def credit(text: str) -> str:
        return re.sub(
            r"^(?:music\s*by|编曲|编配|制谱|原唱|演唱|artist)\s*[:：]?\s*",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()

    credited_artist = next(
        (
            credit(item["text"])
            for item in sorted(title_candidates, key=lambda item: (item["y0"], item["x0"]))
            if re.search(r"原唱|演唱|artist", item["text"], re.IGNORECASE)
        ),
        "",
    )
    arranger = next(
        (
            credit(item["text"])
            for item in sorted(title_candidates, key=lambda item: (item["y0"], item["x0"]))
            if re.search(r"music\s*by|编曲|编配|制谱", item["text"], re.IGNORECASE)
        ),
        "",
    )
    artist_candidates = [
        item
        for item in title_candidates
        if title["y1"] <= item["y0"] <= title["y1"] + 2 * title["height"]
        and item["height"] < title["height"]
        and abs((item["x0"] + item["x1"]) / 2 - pixels.shape[1] / 2)
        < pixels.shape[1] * 0.25
        and not re.search(r"教程|教学|曲谱|第\s*[一二三四五六七八九十\d]+\s*期", item["text"])
    ]
    subtitle = min(
        artist_candidates,
        key=lambda item: abs(item["y0"] - title["y1"]),
        default={"text": ""},
    )["text"]
    all_text = "\n".join(compact(item["text"]) for item in title_candidates)
    tempo_match = re.search(r"(?:[JjLl|]\s*)?[=:]\s*(\d{2,3})\b", all_text)
    capo_match = re.search(r"capo\s*(?:fret)?\s*([0-9]{1,2})", all_text, re.IGNORECASE)
    return {
        "title": compact(title["text"]),
        "artist": compact(credited_artist),
        "subtitle": compact(subtitle),
        "arranger": compact(arranger),
        "tempo": int(tempo_match.group(1)) if tempo_match else None,
        "capo": int(capo_match.group(1)) if capo_match else 0,
        "tuning": "standard" if re.search(r"standard\s+tuning", all_text, re.IGNORECASE) else "",
    }


def detect_image_profile(path: Path) -> str:
    result = inspect_score_source(path, include_digest=False)
    return str(result.get("visualProfile") or "generic")


def diagnose_source_inputs(inputs: list[Path]) -> dict:
    results = []
    for path in inputs:
        try:
            results.append(inspect_score_source(path, include_digest=False))
        except Exception as error:
            results.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "error": str(error),
                }
            )
    return aggregate_source_diagnosis(results)


def select_tab_adapter(source_diagnosis: dict) -> dict:
    files = source_diagnosis.get("files", [])
    images = [
        item
        for item in files
        if item.get("kind") == "image" and not item.get("error")
    ]
    features = source_diagnosis.get("features", {})
    has_pdfs = bool(features.get("pdfFiles", 0)) or any(
        item.get("kind") == "pdf" for item in files
    )
    all_inputs_are_images = features.get("imageFiles", len(images)) == len(files)
    selected = "generic"
    reason = "当前输入没有匹配到专用版面适配器，使用通用回退。"
    raster_images = all(
        str(item.get("renderType") or "").startswith("digital-raster")
        for item in images
    )
    if (
        images
        and all_inputs_are_images
        and not has_pdfs
        and raster_images
        and features.get("redChordImages", 0) > 0
    ):
        selected = "red-chord-tab"
        reason = "检测到数字谱中的红色和弦标签，启用和弦图与 X 占位符映射。"
    elif (
        images
        and all_inputs_are_images
        and not has_pdfs
        and all(
            item.get("visualProfile") == "monochrome-watermark-tab"
            for item in images
        )
    ):
        selected = "monochrome-watermark-tab"
        reason = "所有页面均具有浅色单色谱线和数字渲染背景，启用黑白水印适配。"
    candidates = source_diagnosis.get("sourceCandidates", [])
    top_candidate = candidates[0] if candidates else None
    return {
        "id": selected,
        "displayName": TAB_ADAPTERS[selected]["displayName"],
        "description": TAB_ADAPTERS[selected]["description"],
        "reason": reason,
        "sourceFamily": top_candidate.get("family") if top_candidate else None,
        "sourceConfidence": top_candidate.get("confidence") if top_candidate else None,
        "renderType": source_diagnosis.get("renderType"),
    }

def enum_value(value):
    return getattr(value, "value", value)


def enum_name(value) -> str:
    name = getattr(value, "name", None)
    if name:
        return str(name)
    return str(value).rsplit(".", 1)[-1]


def normalized_time_signature(signature) -> str:
    numerator = int(getattr(signature, "numerator", 0) or 0)
    denominator = int(enum_value(getattr(signature, "denominator", 0)) or 0)
    if numerator <= 0 or denominator <= 0:
        return ""
    return f"{numerator}/{denominator}"


def build_layout_systems(report: dict, captured_pages: dict) -> tuple[list[dict], list[dict]]:
    tracks = report.get("tracks", [])
    all_systems = []
    track_systems = []
    for track in tracks:
        systems = []
        page_offsets = Counter()
        for system_index, detail in enumerate(track.get("systems_detail", []), start=1):
            page = int(detail.get("page") or 1)
            system = {
                "index": system_index,
                "page": page,
                "source": str(detail.get("source") or "unknown"),
                "strings": int(track.get("strings") or 0),
                "measures": detail.get("measures"),
                "notes": int(detail.get("notes") or 0),
            }
            captured = captured_pages.get(page, {}).get("systems", [])
            captured_index = page_offsets[page]
            page_offsets[page] += 1
            if captured_index < len(captured):
                geometry = captured[captured_index]
                system["top"] = round(float(geometry.get("top", 0)), 2)
                system["bars"] = [
                    round(float(value), 2) for value in geometry.get("bars", [])
                ]
                system["layoutNotes"] = int(geometry.get("events") or 0)
            systems.append(system)
            all_systems.append(system)
        track_systems.append(systems)
    return all_systems, track_systems


def note_techniques(note) -> list[str]:
    effect = getattr(note, "effect", None)
    if effect is None:
        return []
    techniques = []
    for attribute, label in (
        ("hammer", "hammer-on"),
        ("palmMute", "palm-mute"),
        ("letRing", "let-ring"),
        ("staccato", "staccato"),
        ("vibrato", "vibrato"),
        ("ghostNote", "ghost-note"),
        ("accentuatedNote", "accent"),
        ("heavyAccentuatedNote", "heavy-accent"),
    ):
        if getattr(effect, attribute, False):
            techniques.append(label)
    if getattr(effect, "bend", None) is not None:
        techniques.append("bend")
    if getattr(effect, "harmonic", None) is not None:
        techniques.append("harmonic")
    if getattr(effect, "slides", None):
        techniques.append("slide")
    if getattr(effect, "trill", None) is not None:
        techniques.append("trill")
    if getattr(effect, "tremoloPicking", None) is not None:
        techniques.append("tremolo-picking")
    return techniques


def build_score_measure(measure, number: int) -> dict:
    events = []
    beat_count = 0
    for voice_index, voice in enumerate(getattr(measure, "voices", [])):
        for beat in getattr(voice, "beats", []):
            beat_count += 1
            start = int(getattr(beat, "startInMeasure", 0) or 0)
            duration = int(getattr(getattr(beat, "duration", None), "time", 0) or 0)
            for note in getattr(beat, "notes", []):
                events.append(
                    {
                        "voice": voice_index + 1,
                        "string": int(getattr(note, "string", 0) or 0),
                        "fret": int(getattr(note, "value", 0) or 0),
                        "start": start,
                        "duration": duration,
                        "type": enum_name(getattr(note, "type", "normal")),
                        "velocity": int(getattr(note, "velocity", 0) or 0),
                        "techniques": note_techniques(note),
                    }
                )
    events.sort(key=lambda event: (event["start"], event["voice"], event["string"]))
    header = getattr(measure, "header", None)
    signature = getattr(header, "timeSignature", None)
    return {
        "number": number,
        "start": int(getattr(measure, "start", 0) or 0),
        "duration": max(
            int(getattr(measure, "end", 0) or 0) - int(getattr(measure, "start", 0) or 0),
            0,
        ),
        "timeSignature": normalized_time_signature(signature),
        "beats": beat_count,
        "eventCount": len(events),
        "events": events,
    }


def validate_tab_score(tab_score: dict) -> dict:
    checks = []

    def add_check(identifier: str, status: str, message: str) -> None:
        checks.append({"id": identifier, "status": status, "message": message})

    tracks = tab_score.get("tracks", [])
    if not tracks:
        add_check("tracks-present", "error", "没有任何六线谱轨道。")
    else:
        add_check("tracks-present", "pass", f"共识别出 {len(tracks)} 条轨道。")

    track_measure_counts = set()
    for track_index, track in enumerate(tracks, start=1):
        label = track.get("name") or f"轨道 {track_index}"
        measures = len(track.get("measures", []))
        track_measure_counts.add(measures)
        report_measures = track.get("reportedMeasures")
        if isinstance(report_measures, int) and report_measures != measures:
            add_check(
                "track-measure-count",
                "warning",
                f"{label} 的识别报告为 {report_measures} 小节，标准化结果包含 {measures} 小节。",
            )

        systems = track.get("systems", [])
        measured_systems = [
            system
            for system in systems
            if isinstance(system.get("measures"), int)
        ]
        if measured_systems and sum(system["measures"] for system in measured_systems) != measures:
            add_check(
                "system-measure-coverage",
                "warning",
                f"轨道“{label}”的谱表系统合计 {sum(system['measures'] for system in measured_systems)} 小节，"
                f"与轨道总小节数 {measures} 不一致。",
            )
        elif measured_systems:
            add_check(
                "system-measure-coverage",
                "pass",
                f"轨道“{label}”的 {len(systems)} 个谱表系统完整覆盖 {measures} 小节。",
            )

        string_counts = {
            system["strings"]
            for system in systems
            if isinstance(system.get("strings"), int) and system["strings"] > 0
        }
        expected_strings = int(track.get("strings") or 0)
        if string_counts and (len(string_counts) > 1 or expected_strings not in string_counts):
            add_check(
                "system-string-count",
                "warning",
                f"{label} 的主谱表为 {expected_strings} 弦，但各系统检测到弦数 {sorted(string_counts)}。",
            )

        numbers = [measure.get("number") for measure in track.get("measures", [])]
        if numbers != list(range(1, measures + 1)):
            add_check(
                "measure-numbering",
                "warning",
                f"{label} 的小节编号不连续。",
            )

        invalid_events = []
        for measure in track.get("measures", []):
            for event in measure.get("events", []):
                string = event.get("string")
                fret = event.get("fret")
                if (
                    not isinstance(string, int)
                    or not 1 <= string <= expected_strings
                    or not isinstance(fret, int)
                    or fret < 0
                ):
                    invalid_events.append(
                        {"bar": measure.get("number"), "string": string, "fret": fret}
                    )
        if invalid_events:
            first = invalid_events[0]
            add_check(
                "event-range",
                "warning",
                f"{label} 有 {len(invalid_events)} 个音符的弦号或品位超出范围"
                f"（首个：第 {first['bar']} 小节，弦 {first['string']}，品 {first['fret']}）。",
            )

    if len(track_measure_counts) > 1:
        add_check(
            "track-alignment",
            "warning",
            f"各轨道的小节数不一致：{sorted(track_measure_counts)}。",
        )

    status = (
        "review"
        if any(check["status"] in {"warning", "error"} for check in checks)
        else "ok"
    )
    return {
        "status": status,
        "checks": checks,
        "issues": [
            check for check in checks if check["status"] in {"warning", "error"}
        ],
    }


def build_tab_score(
    gp5: bytes,
    report: dict,
    captured_pages: dict,
    inputs: list[Path],
    source_profile: str,
) -> dict:
    import guitarpro as gp

    song = gp.parse(io.BytesIO(gp5))
    all_systems, track_systems = build_layout_systems(report, captured_pages)
    reported_tracks = report.get("tracks", [])
    tracks = []
    for track_index, track in enumerate(song.tracks):
        reported = reported_tracks[track_index] if track_index < len(reported_tracks) else {}
        measures = [
            build_score_measure(measure, number)
            for number, measure in enumerate(track.measures, start=1)
        ]
        tracks.append(
            {
                "index": track_index + 1,
                "name": str(getattr(track, "name", "") or reported.get("name") or ""),
                "kind": "tab" if track_index < len(reported_tracks) else "generated",
                "strings": len(getattr(track, "strings", [])),
                "tuning": [
                    {
                        "string": int(getattr(string, "number", 0) or 0),
                        "midi": int(getattr(string, "value", 0) or 0),
                    }
                    for string in getattr(track, "strings", [])
                ],
                "reportedMeasures": reported.get("measures"),
                "reportedNotes": reported.get("notes"),
                "systems": track_systems[track_index]
                if track_index < len(track_systems)
                else [],
                "measures": measures,
            }
        )

    page_numbers = sorted(
        {system["page"] for system in all_systems}
        | {int(page) for page in captured_pages}
    )
    pages = []
    for page_number in page_numbers:
        captured = captured_pages.get(page_number, {})
        pages.append(
            {
                "number": page_number,
                "width": captured.get("width"),
                "systems": [
                    system for system in all_systems if system["page"] == page_number
                ],
            }
        )

    input_extensions = {path.suffix.lower() for path in inputs}
    source_kinds = {
        source
        for track in reported_tracks
        for source in track.get("sources", [])
        if isinstance(source, str)
    }
    if input_extensions <= IMAGE_EXTENSIONS:
        render_type = "digital-raster-tab"
    elif source_kinds == {"image"}:
        render_type = "raster-pdf"
    elif "image" in source_kinds:
        render_type = "mixed-vector-raster"
    else:
        render_type = "vector-text"

    tab_score = {
        "schemaVersion": TAB_SCORE_SCHEMA_VERSION,
        "source": {
            "profile": source_profile,
            "renderType": render_type,
            "inputKinds": sorted(extension.lstrip(".") for extension in input_extensions),
            "layouts": sorted(source_kinds),
            "files": [path.name for path in inputs],
            "sourceCandidates": report.get("source_diagnosis", {}).get(
                "sourceCandidates", []
            ),
            "adapter": report.get("source_adapter"),
        },
        "pages": pages,
        "systems": all_systems,
        "tracks": tracks,
        "texts": {
            "title": report.get("title", ""),
            "artist": report.get("artist", ""),
            "sections": report.get("sections", []),
            "timedLyrics": report.get("timed_lyrics", []),
        },
        "chords": report.get("chords", []),
        "chordAnalysis": report.get("chord_analysis", {}),
        "totals": {
            "pages": len(page_numbers),
            "systems": len(all_systems),
            "measures": max((len(track["measures"]) for track in tracks), default=0),
            "events": sum(
                measure["eventCount"]
                for track in tracks
                for measure in track["measures"]
            ),
        },
    }
    tab_score["validation"] = validate_tab_score(tab_score)
    return tab_score


def normalize_report_aliases(report: dict) -> None:
    for snake_case, camel_case in (
        ("source_profile", "sourceProfile"),
        ("time_signature", "timeSignature"),
        ("time_signature_changes", "timeSignatureChanges"),
        ("tempo_changes", "tempoChanges"),
        ("repeats_expanded", "repeatsExpanded"),
        ("timed_lyrics", "timedLyrics"),
        ("source_diagnosis", "sourceDiagnosis"),
        ("source_adapter", "sourceAdapter"),
        ("chord_analysis", "chordAnalysis"),
    ):
        if snake_case in report and camel_case not in report:
            report[camel_case] = report[snake_case]


def add_chords_to_gp5(
    gp5: bytes,
    captured_pages: dict,
    images: list[Path],
    metadata: dict | None = None,
) -> tuple[bytes, list[dict], dict]:
    import guitarpro as gp
    from pdf_to_gp5.gp5_writer import sanitize_text

    analysis = {
        "pages": len(images),
        "labels": 0,
        "assignedLabels": 0,
        "unmatchedLabels": 0,
        "convertedNotes": 0,
    }
    song = gp.parse(io.BytesIO(gp5))
    if not song.tracks:
        return gp5, [], analysis
    track = song.tracks[0]
    metadata = metadata or {}
    song.title = sanitize_text(str(metadata.get("title") or song.title))
    song.artist = sanitize_text(str(metadata.get("artist") or song.artist))
    song.subtitle = sanitize_text(str(metadata.get("subtitle") or song.subtitle))
    song.music = sanitize_text(str(metadata.get("arranger") or song.music))
    if metadata.get("tempo"):
        song.tempo = int(metadata["tempo"])
    if metadata.get("capo") is not None:
        track.offset = max(0, min(24, int(metadata["capo"])))
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
        analysis["labels"] += len(labels)
        analysis["assignedLabels"] += sum(len(items) for items in assigned)
        analysis["unmatchedLabels"] += len(unmatched)

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
                                analysis["convertedNotes"] += 1
            measure_offset += max(0, len(bars) - 1)

    buffer = io.BytesIO()
    gp.write(song, buffer, version=(5, 1, 0))
    return buffer.getvalue(), markers, analysis


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
    global ACTIVE_TAB_PROFILE

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
    source_diagnosis = diagnose_source_inputs(inputs)
    source_adapter = select_tab_adapter(source_diagnosis)
    ACTIVE_TAB_PROFILE = source_adapter["id"]
    if ACTIVE_TAB_PROFILE == "monochrome-watermark-tab":
        import pdf_to_gp5.extract.raster_tab as raster

        raster._DARK = 220

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

    detected_metadata: dict = {}
    if images:
        detected_metadata = extract_image_metadata(images[0])
        title = detected_metadata.get("title", "")
        artist = detected_metadata.get("artist", "")
    elif pdf_documents:
        title, artist = extract_pdf_metadata(pdf_documents[0])
    else:
        title = artist = ""

    overrides = job.get("metadata", {})
    if not isinstance(overrides, dict):
        raise ConversionError("元数据格式无效")
    if overrides.get("title"):
        title = str(overrides["title"]).strip()
    if overrides.get("artist"):
        artist = str(overrides["artist"]).strip()
    if overrides.get("subtitle"):
        detected_metadata["subtitle"] = str(overrides["subtitle"]).strip()
    if overrides.get("arranger"):
        detected_metadata["arranger"] = str(overrides["arranger"]).strip()
    if overrides.get("tempo"):
        detected_metadata["tempo"] = max(20, min(400, int(overrides["tempo"])))
    if overrides.get("capo") is not None:
        detected_metadata["capo"] = max(0, min(24, int(overrides["capo"])))
    tempo = detected_metadata.get("tempo")

    options = ConversionOptions(
        title=title,
        artist=artist,
        tempo=tempo,
        tracks=tuple(TrackOptions(filename=name) for name in filenames),
        max_pages=40,
        max_image_pages=30,
        max_image_pixels=40_000_000,
        max_events=50_000,
        max_measures=2_000,
    )
    result = convert_many(documents, options)
    if images and not pdfs:
        gp5, chords, chord_analysis = add_chords_to_gp5(
            result.gp5,
            captured_pages,
            images,
            metadata=detected_metadata,
        )
        result.gp5 = gp5
        result.report["chords"] = chords
        result.report["chord_analysis"] = chord_analysis
        if chord_analysis["unmatchedLabels"]:
            result.report.setdefault("warnings", []).append(
                f"有 {chord_analysis['unmatchedLabels']} 个红色和弦标签无法关联到谱表，"
                "对应位置的 X 可能保留了原有含义。"
            )
        if ACTIVE_TAB_PROFILE == "red-chord-tab" and chord_analysis["labels"] == 0:
            result.report.setdefault("warnings", []).append(
                "检测到红色和弦谱样式，但没有定位到可用的和弦标签，请核对含 X 的音符。"
            )
    result.report["capo"] = detected_metadata.get("capo", 0)
    result.report["source_profile"] = ACTIVE_TAB_PROFILE
    result.report["source_diagnosis"] = source_diagnosis
    result.report["source_adapter"] = source_adapter
    try:
        result.report["tabScore"] = build_tab_score(
            result.gp5,
            result.report,
            captured_pages,
            inputs,
            ACTIVE_TAB_PROFILE,
        )
        validation = result.report["tabScore"]["validation"]
        result.report.setdefault("warnings", []).extend(
            issue["message"] for issue in validation["issues"]
        )
    except Exception as error:
        result.report["tabScore"] = {
            "schemaVersion": TAB_SCORE_SCHEMA_VERSION,
            "validation": {
                "status": "unavailable",
                "checks": [],
                "issues": [
                    {
                        "id": "tab-score-unavailable",
                        "status": "warning",
                        "message": f"标准化结构报告生成失败：{error}",
                    }
                ],
            },
        }
        result.report.setdefault("warnings", []).append(
            f"标准化结构报告生成失败，已保留转换结果：{error}"
        )
    normalize_report_aliases(result.report)
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
