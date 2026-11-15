import { execFileSync } from "node:child_process";
import {
  copyFile,
  cp,
  mkdir,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const COMMIT = "ace2e894ec1abaa1a73398f9b3e711293045384a";
const PYTHON_VERSION = "3.12.14";
const PREPARED_REVISION = `pdf-to-gp5-${COMMIT.slice(0, 8)}-r1`;
const DEPENDENCIES = [
  "pdfplumber==0.11.10",
  "PyGuitarPro==0.9.3",
  "onnxruntime==1.30.0",
  "opencv-python==5.0.0.93",
  "numpy==2.4.6",
  "pyclipper==1.4.0",
  "Shapely==2.1.2",
  "PyYAML==6.0.3",
  "Pillow==13.0.0",
  "six==1.17.0",
  "tqdm==4.70.1",
  "rapidocr-onnxruntime==1.4.4",
];
const SOURCE_FILES = [
  "__init__.py",
  "converter.py",
  "gp5_writer.py",
  "i18n.py",
  "model.py",
  "preview.py",
  "repeats.py",
  "rhythm.py",
  "tunings.py",
  "extract/__init__.py",
  "extract/annotations.py",
  "extract/ascii_tab.py",
  "extract/bar_signs.py",
  "extract/common.py",
  "extract/engraved_tab.py",
  "extract/metadata.py",
  "extract/pdf_reader.py",
  "extract/raster_tab.py",
  "extract/rhythm_marks.py",
];

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const resourceRoot = path.join(root, "src-tauri/resources/tab-converter");
const legalDestination = path.join(root, "src-tauri/resources/legal");
const markerPath = path.join(resourceRoot, ".prepared-version");
const uv = process.env.UV_BIN || "uv";

await mkdir(legalDestination, { recursive: true });
await copyFile(path.join(root, "LICENSE"), path.join(legalDestination, "LICENSE"));
await copyFile(
  path.join(root, "THIRD_PARTY_NOTICES.md"),
  path.join(legalDestination, "THIRD_PARTY_NOTICES.md"),
);

if (
  (await readFile(markerPath, "utf8").catch(() => "")) === `${PREPARED_REVISION}\n` &&
  (await readFile(path.join(resourceRoot, "runner.py"), "utf8").catch(() => "")).length > 0
) {
  console.log(`Tab converter ${COMMIT.slice(0, 8)} is already prepared.`);
  process.exit(0);
}

execFileSync(uv, ["python", "install", PYTHON_VERSION], { stdio: "inherit" });
const managedPython = execFileSync(
  uv,
  ["python", "find", "--managed-python", PYTHON_VERSION],
  { encoding: "utf8" },
).trim();
const pythonRoot = path.dirname(path.dirname(managedPython));
const pythonVersion = PYTHON_VERSION.split(".").slice(0, 2).join(".");
const sitePackages = path.join(
  resourceRoot,
  "python",
  "lib",
  `python${pythonVersion}`,
  "site-packages",
);

await rm(resourceRoot, { recursive: true, force: true });
await mkdir(sitePackages, { recursive: true });
await mkdir(legalDestination, { recursive: true });
await cp(pythonRoot, path.join(resourceRoot, "python"), {
  recursive: true,
  preserveTimestamps: true,
});

execFileSync(
  uv,
  [
    "pip",
    "install",
    "--target",
    sitePackages,
    "--python",
    managedPython,
    ...DEPENDENCIES,
  ],
  { stdio: "inherit" },
);

const converterPackage = path.join(sitePackages, "pdf_to_gp5");
await Promise.all(
  SOURCE_FILES.map(async (relative) => {
    const destination = path.join(converterPackage, relative);
    await mkdir(path.dirname(destination), { recursive: true });
    const source = `https://raw.githubusercontent.com/F100Pilot/PDF-to-GP5/${COMMIT}/app/${relative}`;
    const response = await fetch(source, {
      headers: { "User-Agent": "toGp-build" },
    });
    if (!response.ok) {
      throw new Error(`Unable to download ${source}: ${response.status}`);
    }
    await writeFile(destination, new Uint8Array(await response.arrayBuffer()));
  }),
);

await copyFile(
  path.join(root, "scripts/tab-converter-runner.py"),
  path.join(resourceRoot, "runner.py"),
);
await mkdir(path.join(resourceRoot, "licenses"), { recursive: true });
for (const [name, url] of [
  ["LICENSE", `https://raw.githubusercontent.com/F100Pilot/PDF-to-GP5/${COMMIT}/LICENSE`],
  [
    "THIRD_PARTY_NOTICES.md",
    `https://raw.githubusercontent.com/F100Pilot/PDF-to-GP5/${COMMIT}/THIRD_PARTY_NOTICES.md`,
  ],
]) {
  const response = await fetch(url, { headers: { "User-Agent": "toGp-build" } });
  if (!response.ok) {
    throw new Error(`Unable to download ${url}: ${response.status}`);
  }
  await writeFile(
    path.join(resourceRoot, "licenses", `pdf-to-gp5-${name}`),
    new Uint8Array(await response.arrayBuffer()),
  );
}

const python = path.join(resourceRoot, "python/bin", `python${pythonVersion}`);
execFileSync(python, [path.join(resourceRoot, "runner.py"), "--check"], {
  env: {
    ...process.env,
    PYTHONPATH: sitePackages,
    PYTHONDONTWRITEBYTECODE: "1",
    PYTHONNOUSERSITE: "1",
    ORT_DISABLE_TELEMETRY: "1",
  },
  stdio: "inherit",
});

await writeFile(markerPath, `${PREPARED_REVISION}\n`);
console.log(`Prepared tab converter in ${resourceRoot}`);
