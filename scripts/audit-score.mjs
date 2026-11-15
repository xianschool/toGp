import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const resourceRoot = path.join(root, "src-tauri/resources/tab-converter");
const python = path.join(resourceRoot, "python/bin/python3.12");
const sitePackages = path.join(resourceRoot, "python/lib/python3.12/site-packages");
const runner = path.join(resourceRoot, "runner.py");


export function evaluateExpectations(report, expectations) {
  const checks = [];
  const add = (name, expected, actual, passed) => {
    checks.push({ name, expected, actual, passed });
  };

  const values = {
    title: report.title,
    artist: report.artist,
    sourceProfile: report.sourceProfile,
    adapter: report.sourceAdapter?.id,
    renderType: report.sourceAdapter?.renderType,
    sourceFamily: report.sourceAdapter?.sourceFamily,
    measures: report.measures,
    notes: report.notes,
    pages: report.tabScore?.totals?.pages,
    systems: report.tabScore?.totals?.systems,
    rhythmFromNotation: report.tracks?.[0]?.rhythm_from_notation,
    rhythmEstimated: report.tracks?.[0]?.rhythm_estimated,
    validationStatus: report.tabScore?.validation?.status,
  };

  for (const [key, expected] of Object.entries(expectations ?? {})) {
    if (key === "sha256") {
      continue;
    }
    if (key.startsWith("min")) {
      const field = key.slice(3, 4).toLowerCase() + key.slice(4);
      const actual = values[field];
      add(key, `>= ${expected}`, actual, Number(actual) >= Number(expected));
      continue;
    }
    if (key.startsWith("max")) {
      const field = key.slice(3, 4).toLowerCase() + key.slice(4);
      const actual = values[field];
      add(key, `<= ${expected}`, actual, Number(actual) <= Number(expected));
      continue;
    }
    const actual = values[key];
    add(key, expected, actual, Object.is(actual, expected));
  }
  return checks;
}


function sha256(filePath) {
  const hash = createHash("sha256");
  hash.update(readFileSync(filePath));
  return hash.digest("hex");
}


function findRunner() {
  if (process.env.TOGP_RUNNER) {
    return path.resolve(process.env.TOGP_RUNNER);
  }
  return runner;
}


function convert(inputPath) {
  if (!existsSync(python) || !existsSync(sitePackages)) {
    throw new Error("Tab converter runtime is missing. Run: npm run prepare:tab-converter");
  }
  const selectedRunner = findRunner();
  if (!existsSync(selectedRunner)) {
    throw new Error(`Tab converter runner is missing: ${selectedRunner}`);
  }

  const workDir = mkdtempSync(path.join(tmpdir(), "togp-audit-"));
  const jobPath = path.join(workDir, "job.json");
  const outputPath = path.join(workDir, "output.gp5");
  try {
    writeFileSync(jobPath, JSON.stringify({ inputs: [path.resolve(inputPath)] }));
    const stdout = execFileSync(
      python,
      [selectedRunner, jobPath, outputPath],
      {
        cwd: workDir,
        encoding: "utf8",
        env: {
          ...process.env,
          HOME: workDir,
          TMPDIR: workDir,
          PATH: "/usr/bin:/bin:/usr/sbin:/sbin",
          LANG: "C.UTF-8",
          LC_ALL: "C.UTF-8",
          PYTHONPATH: sitePackages,
          PYTHONDONTWRITEBYTECODE: "1",
          PYTHONNOUSERSITE: "1",
          ORT_DISABLE_TELEMETRY: "1",
        },
        maxBuffer: 32 * 1024 * 1024,
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    const reply = JSON.parse(stdout);
    if (!reply.ok) {
      throw new Error(reply.error || "Tab conversion failed");
    }
    return {
      report: reply.report,
      gp5Bytes: statSync(outputPath).size,
    };
  } finally {
    rmSync(workDir, { recursive: true, force: true });
  }
}


function parseArguments(argv) {
  let expectationPath = "";
  let pretty = false;
  let inputPath = "";
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--pretty") {
      pretty = true;
      continue;
    }
    if (argument === "--expect") {
      expectationPath = argv[index + 1] || "";
      if (!expectationPath) {
        throw new Error("--expect requires a JSON file");
      }
      index += 1;
      continue;
    }
    if (argument.startsWith("--")) {
      throw new Error(`Unknown option: ${argument}`);
    }
    if (inputPath) {
      throw new Error(`Unexpected argument: ${argument}`);
    }
    inputPath = argument;
  }
  if (!inputPath) {
    throw new Error("Usage: npm run audit:score -- score.pdf [--expect expectations.json] [--pretty]");
  }
  return { inputPath, expectationPath, pretty };
}


function printDirectoryHint() {
  const examples = path.join(root, "docs/source-regression-examples");
  if (!existsSync(examples)) {
    return;
  }
  const names = readdirSync(examples).filter((name) => name.endsWith(".json"));
  if (names.length) {
    console.error(`Example expectations: ${names.join(", ")}`);
  }
}


function main() {
  let options;
  try {
    options = parseArguments(process.argv.slice(2));
  } catch (error) {
    console.error(error.message);
    printDirectoryHint();
    process.exit(2);
  }
  const expectations = options.expectationPath
    ? JSON.parse(readFileSync(path.resolve(options.expectationPath), "utf8"))
    : {};
  const actualSha256 = sha256(options.inputPath);
  if (expectations.sha256 && expectations.sha256 !== actualSha256) {
    throw new Error(`SHA-256 mismatch: expected ${expectations.sha256}, got ${actualSha256}`);
  }
  const result = convert(options.inputPath);
  const checks = evaluateExpectations(result.report, expectations);
  const passed = checks.every((check) => check.passed);
  const payload = {
    ok: passed,
    input: path.resolve(options.inputPath),
    sha256: actualSha256,
    gp5Bytes: result.gp5Bytes,
    source: result.report.sourceDiagnosis,
    adapter: result.report.sourceAdapter,
    totals: result.report.tabScore?.totals,
    rhythm: {
      fromNotation: result.report.tracks?.[0]?.rhythm_from_notation,
      estimated: result.report.tracks?.[0]?.rhythm_estimated,
      estimatedBars: result.report.tracks?.[0]?.rhythm_estimated_bars,
    },
    validation: result.report.tabScore?.validation,
    warnings: result.report.warnings,
    checks,
  };
  console.log(JSON.stringify(payload, null, options.pretty ? 2 : 0));
  if (!passed) {
    process.exitCode = 1;
  }
}


if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  main();
}
