#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


RUNNER_PATH = Path(__file__).with_name("tab-converter-runner.py")
SPEC = importlib.util.spec_from_file_location("tab_converter_runner", RUNNER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Unable to load tab converter runner")
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def score_with(measures: int = 2, systems: list[dict] | None = None) -> dict:
    return {
        "tracks": [
            {
                "name": "Guitar",
                "strings": 6,
                "reportedMeasures": measures,
                "systems": systems
                or [
                    {"strings": 6, "measures": 1},
                    {"strings": 6, "measures": 1},
                ],
                "measures": [
                    {
                        "number": number,
                        "events": [{"string": 1, "fret": number - 1}],
                    }
                    for number in range(1, measures + 1)
                ],
            }
        ]
    }


class TabScoreModelTest(unittest.TestCase):
    def test_layout_geometry_is_merged_with_system_details(self) -> None:
        report = {
            "tracks": [
                {
                    "strings": 6,
                    "systems_detail": [
                        {"page": 1, "source": "image", "notes": 4, "measures": 2},
                        {"page": 1, "source": "image", "notes": 3, "measures": 2},
                    ],
                }
            ]
        }
        captured = {
            1: {
                "width": 1000,
                "systems": [
                    {"top": 100.125, "bars": [0, 250, 500], "events": 4},
                    {"top": 400.875, "bars": [0, 250, 500], "events": 3},
                ],
            }
        }

        all_systems, track_systems = RUNNER.build_layout_systems(report, captured)

        self.assertEqual(len(all_systems), 2)
        self.assertEqual(len(track_systems[0]), 2)
        self.assertEqual(all_systems[0]["top"], 100.12)
        self.assertEqual(all_systems[1]["bars"], [0.0, 250.0, 500.0])
        self.assertEqual(all_systems[1]["layoutNotes"], 3)

    def test_consistent_score_passes_validation(self) -> None:
        validation = RUNNER.validate_tab_score(score_with())

        self.assertEqual(validation["status"], "ok")
        self.assertEqual(validation["issues"], [])

    def test_system_measure_mismatch_is_reported(self) -> None:
        validation = RUNNER.validate_tab_score(
            score_with(systems=[{"strings": 6, "measures": 1}])
        )

        issue_ids = {issue["id"] for issue in validation["issues"]}
        self.assertEqual(validation["status"], "review")
        self.assertIn("system-measure-coverage", issue_ids)

    def test_out_of_range_event_and_track_alignment_are_reported(self) -> None:
        tab_score = score_with()
        tab_score["tracks"][0]["measures"][1]["events"][0]["string"] = 7
        tab_score["tracks"].append(
            {
                "name": "Bass",
                "strings": 4,
                "reportedMeasures": 1,
                "systems": [],
                "measures": [{"number": 1, "events": []}],
            }
        )

        validation = RUNNER.validate_tab_score(tab_score)
        issue_ids = {issue["id"] for issue in validation["issues"]}

        self.assertIn("event-range", issue_ids)
        self.assertIn("track-alignment", issue_ids)

    def test_report_aliases_are_added_without_overwriting_values(self) -> None:
        report = {
            "source_profile": "generic",
            "time_signature": "4/4",
            "timeSignature": "3/4",
        }

        RUNNER.normalize_report_aliases(report)

        self.assertEqual(report["sourceProfile"], "generic")
        self.assertEqual(report["timeSignature"], "3/4")

    def test_monochrome_pages_select_watermark_adapter(self) -> None:
        adapter = RUNNER.select_tab_adapter(
            {
                "renderType": "digital-raster",
                "sourceCandidates": [
                    {
                        "family": "generic-raster-score",
                        "confidence": 0.4,
                    }
                ],
                "files": [
                    {
                        "kind": "image",
                        "visualProfile": "monochrome-watermark-tab",
                    }
                ],
            }
        )

        self.assertEqual(adapter["id"], "monochrome-watermark-tab")
        self.assertEqual(adapter["displayName"], "黑白水印六线谱")

    def test_red_chord_pages_use_generic_fallback(self) -> None:
        adapter = RUNNER.select_tab_adapter(
            {
                "renderType": "digital-raster-tab",
                "sourceCandidates": [
                    {
                        "family": "digital-tab-screenshot",
                        "confidence": 0.75,
                    }
                ],
                "files": [
                    {
                        "kind": "image",
                        "visualProfile": "red-chord-tab",
                    }
                ],
            }
        )

        self.assertEqual(adapter["id"], "generic")
        self.assertEqual(adapter["sourceFamily"], "digital-tab-screenshot")


if __name__ == "__main__":
    unittest.main()
