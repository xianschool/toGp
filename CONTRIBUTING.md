# Contributing to toGp

Thanks for helping improve toGp. The project is a local macOS Apple Silicon utility, so changes should preserve its offline-first behavior and keep score data on the user's machine.

## Development setup

Requirements:

- macOS 13 or newer on Apple Silicon
- Xcode Command Line Tools
- Node.js 22 or newer
- Python 3.12 for model tests
- `uv` for preparing the bundled converter
- Rust stable

```bash
npm ci
npm run prepare:tab-converter
npm run desktop:dev
```

`npm run prepare:tab-converter` downloads a pinned MIT-licensed converter and an offline Python/OCR runtime. Generated resources under `src-tauri/resources/` and build outputs under `src-tauri/target/` are not tracked.

## Checks before opening a pull request

```bash
npm test
npm run build
cargo test --manifest-path src-tauri/Cargo.toml
```

If a change affects recognition, also run:

```bash
npm run audit:score -- /path/to/score.pdf --pretty
```

Do not commit original PDFs, screenshots, `.gp5` exports, or other score files unless you own the rights and have deliberately documented them as redistributable fixtures.

## Areas of work

- source diagnostics and layout adapters
- Guitar Pro and MuseScore PDF compatibility
- scanned-print preprocessing and OCR quality
- normalized TabScore validation
- macOS packaging, accessibility, and privacy

Keep changes focused. Add or update tests when changing parsing, validation, or export behavior. New source-specific rules should record evidence and include a reproducible way to verify them.

## Licensing

By contributing, you agree that your contribution can be distributed under the repository's AGPL-3.0-only license. Do not contribute code or assets under an incompatible license without updating `THIRD_PARTY_NOTICES.md`.
