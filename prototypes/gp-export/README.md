# GP export prototype

This prototype validates the first format gate for the ToGpx service:

```text
alphaTex/Score -> alphaTab Score -> .gp -> alphaTab readback
```

It uses an exact alphaTab version and does not expose file uploads, PDF parsing, OMR, or user-controlled paths.

## Run

```bash
npm install
npm run check
npm test
mkdir -p artifacts
npm run export -- fixtures/tab-example.atex artifacts/tab-example.gp
```

The generated file is intentionally ignored by Git. Open it in Guitar Pro 8 for the external compatibility check. Guitar Pro 7 compatibility remains a separate required test.

## Current compatibility contract

- Output extension: `.gp`
- Container version: `7.0`
- GPIF encoding metadata currently emitted by alphaTab: `8.1.3` / `GP8`
- No GPX output
- No promise of compatibility with Guitar Pro 6 or earlier
