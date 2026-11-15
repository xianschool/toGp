#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from pathlib import Path


RUNNER_PATH = Path(__file__).with_name("tab-converter-runner.py")
SPEC = importlib.util.spec_from_file_location("tab_converter_runner", RUNNER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Unable to load tab converter runner")
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


@dataclass(frozen=True)
class RhythmMark:
    x: float
    units: int | None
    tuplet: bool = False
    is_rest: bool = False
    vibrato: bool = False


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

    def test_red_chord_pages_select_chord_adapter(self) -> None:
        adapter = RUNNER.select_tab_adapter(
            {
                "renderType": "digital-raster-tab",
                "sourceCandidates": [
                    {
                        "family": "digital-tab-screenshot",
                        "confidence": 0.75,
                    }
                ],
                "features": {
                    "imageFiles": 1,
                    "pdfFiles": 0,
                    "redChordImages": 1,
                },
                "files": [
                    {
                        "kind": "image",
                        "renderType": "digital-raster-tab",
                        "visualProfile": "red-chord-tab",
                    }
                ],
            }
        )

        self.assertEqual(adapter["id"], "red-chord-tab")
        self.assertEqual(adapter["sourceFamily"], "digital-tab-screenshot")


    def test_pdf_text_runs_separate_track_label_from_title(self) -> None:
        rows = RUNNER.pdf_text_runs(
            [
                {"x0": 40, "x1": 80, "top": 42, "size": 14, "text": "Guitar"},
                {
                    "x0": 146,
                    "x1": 449,
                    "top": 42,
                    "size": 22,
                    "text": "Boundless Oceans, Vast Skies",
                },
            ]
        )
        artist_rows = RUNNER.pdf_text_runs(
            [{"x0": 274, "x1": 321, "top": 70, "size": 14, "text": "Beyond"}]
        )

        self.assertEqual([row["text"] for row in rows], [
            "Guitar",
            "Boundless Oceans, Vast Skies",
        ])
        self.assertEqual(rows[1]["size"], 22)
        self.assertEqual(artist_rows[0]["text"], "Beyond")

    def test_musescore_vector_pdf_selects_musescore_adapter(self) -> None:
        adapter = RUNNER.select_tab_adapter(
            {
                "renderType": "vector-text",
                "sourceCandidates": [
                    {"family": "musescore", "confidence": 0.98}
                ],
                "features": {"imageFiles": 0, "pdfFiles": 1},
                "files": [
                    {
                        "kind": "pdf",
                        "renderType": "vector-text",
                    }
                ],
            }
        )

        self.assertEqual(adapter["id"], "musescore-vector-tab")
        self.assertEqual(adapter["sourceFamily"], "musescore")


    def test_guitar_pro_filter_drops_chord_grids_and_keeps_full_width_rests(self) -> None:
        systems = [
            SimpleNamespace(events=[], start_x=138.4, end_x=454.0),
            SimpleNamespace(events=[], start_x=28.3, end_x=566.9),
            SimpleNamespace(events=[object()], start_x=187.1, end_x=405.2),
        ]

        filtered = RUNNER.filter_adapter_systems(
            systems,
            595.0,
            "guitar-pro-vector-tab",
        )

        self.assertEqual(filtered, systems[1:])

    def test_gpbravura_vector_pdf_selects_guitar_pro_adapter(self) -> None:
        adapter = RUNNER.select_tab_adapter(
            {
                "renderType": "vector-text",
                "sourceCandidates": [
                    {"family": "guitar-pro", "confidence": 0.95}
                ],
                "features": {"imageFiles": 0, "pdfFiles": 1},
                "files": [
                    {
                        "kind": "pdf",
                        "renderType": "vector-text",
                    }
                ],
            }
        )

        self.assertEqual(adapter["id"], "guitar-pro-vector-tab")
        self.assertEqual(adapter["sourceFamily"], "guitar-pro")


    def test_guitar_pro_rhythm_is_aligned_to_tab_columns(self) -> None:
        notation = SimpleNamespace(
            string_count=5,
            start_x=28.3,
            end_x=566.9,
            rhythm=[
                RhythmMark(x=81.3, units=4),
                RhythmMark(x=89.9, units=2),
                RhythmMark(x=300.0, units=1),
            ],
        )
        tab = SimpleNamespace(
            string_count=6,
            start_x=28.3,
            end_x=566.9,
            char_width=3.8,
            rhythm=[],
            events=[
                SimpleNamespace(x=65.2),
                SimpleNamespace(x=78.8),
                SimpleNamespace(x=92.4),
            ],
        )

        RUNNER.transfer_guitar_pro_rhythm([notation, tab])

        self.assertEqual([mark.x for mark in tab.rhythm], [78.8, 92.4, 300.0])
        self.assertEqual([mark.units for mark in tab.rhythm], [4, 2, 1])


    def test_guitar_pro_trailing_margin_is_not_a_measure(self) -> None:
        system = SimpleNamespace(
            string_count=6,
            char_width=3.8,
            bars=[28.3, 455.0, 545.7, 549.9],
            events=[SimpleNamespace(x=521.8)],
        )

        RUNNER.trim_guitar_pro_margins([system])

        self.assertEqual(system.bars, [28.3, 455.0, 545.7])


if __name__ == "__main__":
    unittest.main()
