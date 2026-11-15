# Third-party notices

toGp itself is licensed under the MIT License. The following components remain under their own licenses.

## PDF-to-GP5

- Project: https://github.com/F100Pilot/PDF-to-GP5
- Pinned source: `ace2e894ec1abaa1a73398f9b3e711293045384a`
- License: MIT
- Use: tablature extraction, rhythm reconstruction, and Guitar Pro 5 serialization

The build process downloads this pinned source and preserves its LICENSE and THIRD_PARTY_NOTICES files under `src-tauri/resources/tab-converter/licenses/`.

## PyGuitarPro 0.9.3

- Project: https://github.com/Perlence/PyGuitarPro
- License: LGPL-3.0-only
- Use: reading and writing Guitar Pro files

PyGuitarPro is installed as replaceable Python source inside the bundled runtime. Its license file is included in its `.dist-info` directory. The MIT license for toGp does not relicense PyGuitarPro or remove the rights granted by LGPL-3.0.

## Python and OCR runtime

The macOS package includes a standalone Python runtime and pinned versions of `pdfplumber`, `pdfminer.six`, `pypdfium2`, `RapidOCR`, `ONNX Runtime`, OpenCV, NumPy, Pillow, Shapely, and their transitive dependencies. Their license files are installed alongside the Python packages.

## Tauri and Rust dependencies

Tauri and the Rust application dependencies are distributed under their respective permissive licenses. Cargo metadata and upstream license files remain available from their package sources.
