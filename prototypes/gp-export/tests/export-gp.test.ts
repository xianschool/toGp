import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import * as alphaTab from "@coderline/alphatab";
import { strFromU8, unzipSync } from "fflate";
import { exportGp, loadAlphaTexScore } from "../src/export-gp.js";

function scoreShape(score: alphaTab.model.Score) {
  return {
    title: score.title,
    artist: score.artist,
    tracks: score.tracks.map((track) => ({
      name: track.name,
      tuning: track.staves[0]?.tuning ?? [],
      bars: track.staves[0]?.bars.map((bar) => ({
        timeSignature: [bar.masterBar.timeSignatureNumerator, bar.masterBar.timeSignatureDenominator],
        notes: bar.voices.flatMap((voice) =>
          voice.beats.flatMap((beat) =>
            beat.notes.map((note) => ({
              pitch: note.realValue,
              string: note.string,
              fret: note.fret,
              duration: beat.duration,
            })),
          ),
        ),
      })),
    })),
  };
}

test("exports a readable Guitar Pro file without changing the input score", async () => {
  const fixture = await readFile(new URL("../fixtures/tab-example.atex", import.meta.url), "utf8");
  const source = loadAlphaTexScore(fixture);
  const beforeExport = scoreShape(source);

  const exported = exportGp(source);
  assert.equal(exported.extension, ".gp");

  const zip = unzipSync(exported.bytes);
  assert.equal(strFromU8(zip.VERSION!), "7.0");

  const gpif = strFromU8(zip["Content/score.gpif"]!);
  assert.match(gpif, /<GPVersion>8\.1\.3<\/GPVersion>/);
  assert.match(gpif, /<EncodingDescription>GP8<\/EncodingDescription>/);

  const imported = alphaTab.importer.ScoreLoader.loadScoreFromBytes(
    exported.bytes,
    new alphaTab.Settings(),
  );
  assert.deepEqual(scoreShape(source), beforeExport);
  assert.deepEqual(scoreShape(imported), beforeExport);
});
