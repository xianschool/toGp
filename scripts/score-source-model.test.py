#!/usr/bin/env python3
from __future__ import annotations

import unittest

import score_source_model as model


class ScoreSourceModelTest(unittest.TestCase):
    def test_pdf_render_types_distinguish_text_vectors_and_images(self) -> None:
        self.assertEqual(model.pdf_render_type(2, 0, 10, 2, 4), "vector-text")
        self.assertEqual(model.pdf_render_type(0, 0, 10, 0, 0), "vector-outline")
        self.assertEqual(model.pdf_render_type(2, 1, 10, 0, 0), "mixed-vector-raster")
        self.assertEqual(model.pdf_render_type(0, 2, 0, 0, 0), "raster-pdf")

    def test_metadata_candidate_outranks_generic_font_evidence(self) -> None:
        candidates = model.pdf_source_candidates(
            "vector-text",
            {"Producer": "MuseScore Studio 4.5"},
            "edwin leland",
        )

        self.assertEqual(candidates[0]["family"], "musescore")
        self.assertEqual(candidates[0]["confidence"], 0.98)
        self.assertEqual(len(candidates), 1)
        self.assertTrue(any("metadata" in item for item in candidates[0]["evidence"]))
        self.assertTrue(any("font" in item for item in candidates[0]["evidence"]))

    def test_red_chord_image_selects_digital_tab_profile(self) -> None:
        result = model.image_classification(
            {
                "redRatio": 0.004,
                "darkRatio": 0.02,
                "grayRatio": 0.1,
                "backgroundRatio": 0.88,
            }
        )

        self.assertEqual(result["renderType"], "digital-raster-tab")
        self.assertEqual(result["visualProfile"], "red-chord-tab")
        self.assertEqual(result["sourceCandidates"][0]["family"], "digital-tab-screenshot")

    def test_faint_monochrome_page_selects_watermark_profile(self) -> None:
        result = model.image_classification(
            {
                "redRatio": 0,
                "darkRatio": 0.022,
                "grayRatio": 0.097,
                "backgroundRatio": 0.88,
            }
        )

        self.assertEqual(result["visualProfile"], "monochrome-watermark-tab")

    def test_dark_uneven_page_falls_back_to_scanned_profile(self) -> None:
        result = model.image_classification(
            {
                "redRatio": 0,
                "darkRatio": 0.2,
                "grayRatio": 0.5,
                "backgroundRatio": 0.3,
            }
        )

        self.assertEqual(result["renderType"], "scanned-print")
        self.assertEqual(result["visualProfile"], "scanned-print")

    def test_aggregate_merges_candidates_and_visual_features(self) -> None:
        diagnosis = model.aggregate_source_diagnosis(
            [
                {
                    "name": "page1.png",
                    "kind": "image",
                    "renderType": "digital-raster",
                    "visualProfile": "monochrome-watermark-tab",
                    "sourceCandidates": [
                        model.candidate("generic-raster-score", 0.4, ["digital"])
                    ],
                },
                {
                    "name": "page2.png",
                    "kind": "image",
                    "renderType": "digital-raster",
                    "visualProfile": "monochrome-watermark-tab",
                    "sourceCandidates": [
                        model.candidate("musescore", 0.72, ["font"])
                    ],
                },
            ]
        )

        self.assertEqual(diagnosis["renderType"], "digital-raster")
        self.assertEqual(diagnosis["sourceCandidates"][0]["family"], "musescore")
        self.assertEqual(diagnosis["features"]["imageFiles"], 2)
        self.assertEqual(diagnosis["features"]["monochromeImages"], 2)


if __name__ == "__main__":
    unittest.main()
