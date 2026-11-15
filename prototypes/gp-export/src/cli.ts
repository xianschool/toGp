import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { exportGp, loadAlphaTexScore } from "./export-gp.js";

const [inputPath, outputPath] = process.argv.slice(2);

if (!inputPath || !outputPath) {
  console.error("Usage: npm run export -- <input.atex> <output.gp>");
  process.exitCode = 2;
} else {
  const source = await readFile(inputPath, "utf8");
  const score = loadAlphaTexScore(source);
  const result = exportGp(score);

  await mkdir(path.dirname(path.resolve(outputPath)), { recursive: true });
  await writeFile(outputPath, result.bytes);
  console.log(`Wrote ${result.bytes.length} bytes to ${outputPath}`);
}
