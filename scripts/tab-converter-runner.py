#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
import traceback
from pathlib import Path

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.write("\n")


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

    i18n.use("en")
    job = json.loads(job_path.read_text(encoding="utf-8"))
    inputs = [Path(value) for value in job["inputs"]]
    images = [path for path in inputs if path.suffix.lower() in IMAGE_EXTENSIONS]

    documents: list[bytes] = []
    filenames: list[str] = []
    if images:
        combined_pdf = output_path.with_name("images.pdf")
        combine_images(images, combined_pdf)
        documents.append(combined_pdf.read_bytes())
        filenames.append(images[0].stem)

    for source in inputs:
        if source.suffix.lower() == ".pdf":
            documents.append(source.read_bytes())
            filenames.append(source.stem)

    if not documents:
        raise ConversionError("没有可识别的 PDF 或图片")

    options = ConversionOptions(
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
