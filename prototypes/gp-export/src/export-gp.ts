import * as alphaTab from "@coderline/alphatab";

export interface ExportedGp {
  readonly bytes: Uint8Array;
  readonly extension: ".gp";
}

export function loadAlphaTexScore(source: string): alphaTab.model.Score {
  return alphaTab.importer.ScoreLoader.loadAlphaTex(source, new alphaTab.Settings());
}

export function exportGp(score: alphaTab.model.Score): ExportedGp {
  const bytes = new alphaTab.exporter.Gp7Exporter().export(
    score,
    new alphaTab.Settings(),
  );

  if (bytes.length === 0) {
    throw new Error("GP exporter returned an empty file");
  }

  return { bytes, extension: ".gp" };
}
