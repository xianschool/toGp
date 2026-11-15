# Third-party notices

toGp includes and uses the following third-party software.

## Audiveris 5.11.0

- Project: https://github.com/Audiveris/audiveris
- Source for the bundled version: https://github.com/Audiveris/audiveris/tree/5.11.0
- License: GNU Affero General Public License v3.0
- Bundled artifact: official `Audiveris-5.11.0-macosx-arm64.dmg`
- SHA-256: `17491af8b6d40153b031dd1f0815e37213f0999ef23f10586af46706e59b2eb6`
- Changes: none to Audiveris source code. The app bundle is repackaged with filesystem links materialized and an ad-hoc signature.

The complete AGPL license is included in `LICENSE` and inside the app resources as `AUDIVERIS_LICENSE.txt`.

## alphaTab 1.9.0

- Project: https://github.com/CoderLine/alphaTab
- License: Mozilla Public License 2.0
- Use: score rendering, playback, MusicXML import and Guitar Pro 7/8 `.gp` export

## Bravura music font

- Copyright Steinberg Media Technologies
- License: SIL Open Font License 1.1
- License texts are generated under `public/font/` and included in the application bundle.

## SONiVOX soundfont

- License: Apache License 2.0
- License text is generated under `public/soundfont/` and included in the application bundle.

## Tesseract OCR language data

- Source: https://github.com/tesseract-ocr/tessdata
- Pinned commit: `ced78752cc61322fb554c280d13360b35b8684e4`
- Files: `eng.traineddata`, `chi_sim.traineddata`
- License: Apache License 2.0

## Tauri and Rust dependencies

Tauri and the Rust application dependencies are distributed under their respective permissive licenses. Cargo metadata and upstream license files remain available from their package sources.
