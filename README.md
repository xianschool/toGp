# toGp

toGp is a local macOS utility that turns printed guitar tablature into editable Guitar Pro 5 `.gp5` files.

```text
tab PDF / image -> local tab OMR -> GP5 -> alphaTab preview -> .gp5
```

The app does not upload scores, require an account, send telemetry, or contact an application server. All processing happens on the user's Mac.

## Status

The first milestone targets macOS Apple Silicon. The current workflow supports PDF, PNG, JPEG and TIFF input, tablature transcription, alphaTab score preview, and `.gp5` export.

Multiple images can be selected in one operation. toGp sorts their file names naturally (`page2` before `page10`) and combines them into one tablature track. A PDF with embedded text and vector lines is parsed directly; scanned pages use an offline OCR model.

Tablature recognition works best with clear printed scores. Handwritten notation is not supported, and OCR results should be reviewed.

## macOS distribution

Public builds require a `Developer ID Application` certificate and Apple notarization. Store notarization credentials in the macOS Keychain once:

```bash
xcrun notarytool store-credentials "togp-notary" \
  --apple-id "your-apple-id@example.com" \
  --team-id "YOUR_TEAM_ID"
```

The command prompts for an app-specific password; do not put that password in the repository or in build scripts.

Then run the normal packaging command:

```bash
npm run desktop:build
```

The script signs the app and DMG, submits the DMG to Apple, attaches the notarization ticket, and verifies it with Gatekeeper. For a local signing-only build, use:

```bash
SKIP_NOTARIZATION=1 npm run desktop:build
```

## Development

Requirements:

- macOS 13 or newer on Apple Silicon
- Xcode Command Line Tools
- Node.js 22 or newer
- uv
- Rust stable

Install dependencies and prepare the bundled tablature converter:

```bash
npm install
npm run prepare:tab-converter
```

Run the desktop app:

```bash
npm run desktop:dev
```

Build the Apple Silicon application and DMG:

```bash
npm run desktop:build
```

Build outputs are written under `src-tauri/target/aarch64-apple-darwin/release/bundle/`.

The local build is ad-hoc signed unless `APPLE_SIGNING_IDENTITY` is provided. Sharing a build outside your own Mac should use an Apple Developer ID certificate and notarization; otherwise macOS will show an unidentified-developer warning.

## Tablature converter component

`npm run prepare:tab-converter` prepares a pinned build of the MIT-licensed PDF-to-GP5 converter, an Apple Silicon Python runtime, and its offline OCR dependencies. The generated runtime is a build resource and is intentionally ignored by Git.

## Privacy and local security

- The selected file is copied into a private temporary directory.
- The tablature converter runs as a separate local process without an HTTP server.
- The temporary recognition directory is deleted after success or failure.
- The application has no HTTP API and does not collect telemetry.
- GP5 parsing and preview happen inside the Tauri WebView.

## Licensing

toGp is licensed under AGPL-3.0-only. The bundled PDF-to-GP5 converter is MIT-licensed. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

When publishing a binary release, publish the matching source repository and release tag alongside the DMG.

Guitar Pro is a trademark of its respective owner. toGp is not affiliated with or endorsed by Guitar Pro or Arobas Music.
