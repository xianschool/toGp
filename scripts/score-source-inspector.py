#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from score_source_model import SCHEMA_VERSION, inspect_score_source


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect score files and report render/source fingerprints."
    )
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    results = []
    for path in args.paths:
        try:
            results.append(inspect_score_source(path))
        except Exception as error:
            results.append(
                {
                    "schemaVersion": SCHEMA_VERSION,
                    "path": str(path),
                    "name": path.name,
                    "error": str(error),
                }
            )
    print(
        json.dumps(
            results,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
        )
    )
    return 1 if any("error" in result for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
