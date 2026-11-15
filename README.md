# toGp

toGp is a local macOS utility that turns printed guitar scores into editable Guitar Pro 7/8 `.gp` files.

```text
PDF / image -> local Audiveris OMR -> MusicXML -> alphaTab -> .gp
```

The app does not upload scores, require an account, send telemetry, or contact an application server. All processing happens on the user's Mac.

## Status

The first milestone targets macOS Apple Silicon. The current workflow supports PDF, PNG, JPEG and TIFF input, Audiveris transcription, alphaTab score preview, and `.gp` export.

Multiple files can be selected in one operation. toGp sorts their file names naturally (`page2` before `page10`) and uses an Audiveris playlist to combine them into one score. File names that contain scan or page numbers therefore produce the most predictable results.

Audiveris works best with clear printed scores. Handwritten music, strongly distorted photos, and complex layouts may still need correction in the Audiveris editor.

## Development

Requirements:

- macOS 13 or newer on Apple Silicon
- Xcode Command Line Tools
- Node.js 22 or newer
- Rust stable

Install dependencies and prepare the official Audiveris application:

```bash
npm install
npm run prepare:audiveris
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

## Audiveris component

`npm run prepare:audiveris` downloads the official Audiveris 5.11.0 Apple Silicon disk image, verifies SHA-256, extracts the unmodified application, materializes the Java runtime links required by Tauri, and re-signs the nested application ad hoc.

The prepared Audiveris application is a build resource and is intentionally ignored by Git.

## Privacy and local security

- The selected file is copied into a private temporary directory.
- Audiveris runs as a separate local process with fixed arguments.
- The temporary recognition directory is deleted after success or failure.
- The application has no HTTP API and does not collect telemetry.
- MusicXML parsing and `.gp` generation happen inside the Tauri WebView.

## Licensing

toGp is licensed under AGPL-3.0-only because it bundles Audiveris. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

When publishing a binary release, publish the matching source repository and release tag alongside the DMG. The source for the exact upstream Audiveris version remains available from `https://github.com/Audiveris/audiveris/tree/5.11.0`.

Guitar Pro is a trademark of its respective owner. toGp is not affiliated with or endorsed by Guitar Pro or Arobas Music.
