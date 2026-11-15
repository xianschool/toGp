# Security policy

## Supported versions

Security fixes are provided for the latest `0.1.x` development line.

## Reporting a vulnerability

Do not open a public issue for a vulnerability involving arbitrary file access, command execution, temporary-file leakage, packaging, signing, or exposed credentials.

Use GitHub's private vulnerability reporting or security advisory feature for this repository. If private reporting is unavailable, contact the maintainer through the GitHub profile linked on the repository page.

Do not attach private files, copyrighted scores, API tokens, notarization credentials, or signing identities to a report. A minimal reproduction using a synthetic PDF or a textual description is preferred.

## Security model

- Scores are processed locally.
- The desktop app does not expose an HTTP API.
- The tablature converter runs in a temporary working directory.
- Archive extraction in preparation scripts validates paths before writing files.
- Build resources, temporary files, credentials, and local regression scores must remain ignored by Git.
