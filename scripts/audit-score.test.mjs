import assert from "node:assert/strict";
import test from "node:test";

import { evaluateExpectations } from "./audit-score.mjs";


const report = {
  title: "Yesterday",
  sourceProfile: "guitar-pro-vector-tab",
  measures: 18,
  notes: 302,
  sourceAdapter: {
    id: "guitar-pro-vector-tab",
    sourceFamily: "guitar-pro",
  },
  tracks: [{ rhythm_from_notation: 16, rhythm_estimated: 1 }],
  tabScore: {
    totals: { pages: 2, systems: 4 },
    validation: { status: "ok" },
  },
};


test("checks exact, minimum and maximum expectations", () => {
  const checks = evaluateExpectations(report, {
    title: "Yesterday",
    adapter: "guitar-pro-vector-tab",
    measures: 18,
    minNotes: 300,
    maxRhythmEstimated: 1,
    validationStatus: "ok",
  });

  assert.equal(checks.every((check) => check.passed), true);
});


test("reports failed expectations", () => {
  const checks = evaluateExpectations(report, {
    adapter: "musescore-vector-tab",
    minNotes: 400,
  });

  assert.equal(checks.filter((check) => !check.passed).length, 2);
});
