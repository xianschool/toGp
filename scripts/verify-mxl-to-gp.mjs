import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import * as alphaTab from "@coderline/alphatab";

const input = process.argv[2];
const output = process.argv[3];

if (!input || !output) {
  console.error("Usage: npm run verify:mxl -- <input.mxl> <output.gp>");
  process.exit(2);
}

const settings = new alphaTab.Settings();
const musicXml = readFileSync(input);
const score = alphaTab.importer.ScoreLoader.loadScoreFromBytes(musicXml, settings);
const gp = new alphaTab.exporter.Gp7Exporter().export(score, settings);
writeFileSync(output, gp);

const readback = alphaTab.importer.ScoreLoader.loadScoreFromBytes(gp, settings);
const summary = {
  input: path.basename(input),
  output: path.basename(output),
  bytes: gp.length,
  title: readback.title,
  tracks: readback.tracks.length,
  bars: readback.masterBars.length,
  notes: readback.tracks.reduce(
    (total, track) =>
      total +
      track.staves.reduce(
        (staffTotal, staff) =>
          staffTotal +
          staff.bars.reduce(
            (barTotal, bar) =>
              barTotal +
              bar.voices.reduce(
                (voiceTotal, voice) =>
                  voiceTotal +
                  voice.beats.reduce(
                    (beatTotal, beat) => beatTotal + beat.notes.length,
                    0,
                  ),
                0,
              ),
            0,
          ),
        0,
      ),
    0,
  ),
};

console.log(JSON.stringify(summary, null, 2));
