# Third-party notices

toGp includes and uses the following third-party software.

## PDF-to-GP5

- Project: https://github.com/F100Pilot/PDF-to-GP5
- Pinned source: `ace2e894ec1abaa1a73398f9b3e711293045384a`
- License: MIT
- Use: tablature extraction, rhythm reconstruction, and Guitar Pro 5 serialization

## alphaTab 1.9.0

- Project: https://github.com/CoderLine/alphaTab
- License: Mozilla Public License 2.0
- Use: score rendering and Guitar Pro 5 `.gp5` import

## Bravura music font

- Copyright Steinberg Media Technologies
- License: SIL Open Font License 1.1
- License texts are generated under `public/font/` and included in the application bundle.

## SONiVOX soundfont

- License: Apache License 2.0
- License text is generated under `public/soundfont/` and included in the application bundle.

## Python and OCR runtime

The macOS package includes a standalone Python runtime and pinned versions of `pdfplumber`, `PyGuitarPro`, `RapidOCR`, `ONNX Runtime`, OpenCV, NumPy, Pillow, Shapely, and their transitive dependencies. Their license files are installed alongside the Python packages.

## Tauri and Rust dependencies

Tauri and the Rust application dependencies are distributed under their respective permissive licenses. Cargo metadata and upstream license files remain available from their package sources.
