import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import {
  chmod,
  copyFile,
  cp,
  mkdir,
  mkdtemp,
  readdir,
  readFile,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const VERSION = "5.11.0";
const PREPARED_REVISION = `${VERSION}-r2`;
const FILE_NAME = `Audiveris-${VERSION}-macosx-arm64.dmg`;
const EXPECTED_SHA256 =
  "17491af8b6d40153b031dd1f0815e37213f0999ef23f10586af46706e59b2eb6";
const DOWNLOAD_URL =
  "https://api.github.com/repos/Audiveris/audiveris/releases/assets/473797297";
const TESSDATA_COMMIT = "ced78752cc61322fb554c280d13360b35b8684e4";
const TESSDATA_FILES = new Map([
  ["eng.traineddata", "daa0c97d651c19fba3b25e81317cd697e9908c8208090c94c3905381c23fc047"],
  ["chi_sim.traineddata", "fc05d89ab31d8b4e226910f16a8bcbf78e43bae3e2580bb5feefd052efdab363"],
]);

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const cacheDir = path.join(root, ".cache/audiveris");
const imagePath = path.join(cacheDir, FILE_NAME);
const destination = path.join(root, "src-tauri/resources/audiveris");
const destinationApp = path.join(destination, "Audiveris.app");
const archivePath = path.join(root, "src-tauri/resources/audiveris-runtime.zip");
const legalDestination = path.join(root, "src-tauri/resources/legal");
const tessdataDestination = path.join(root, "src-tauri/resources/tessdata");
const markerPath = path.join(destination, ".prepared-version");

if (process.platform !== "darwin" || process.arch !== "arm64") {
  throw new Error("This release only prepares the macOS Apple Silicon Audiveris component.");
}

await mkdir(cacheDir, { recursive: true });
await mkdir(destination, { recursive: true });
await mkdir(legalDestination, { recursive: true });
await mkdir(tessdataDestination, { recursive: true });
await copyFile(path.join(root, "LICENSE"), path.join(legalDestination, "LICENSE"));
await copyFile(
  path.join(root, "THIRD_PARTY_NOTICES.md"),
  path.join(legalDestination, "THIRD_PARTY_NOTICES.md"),
);
for (const [fileName, expectedHash] of TESSDATA_FILES) {
  const cachedPath = path.join(cacheDir, fileName);
  const targetPath = path.join(tessdataDestination, fileName);
  if (!(await hasHash(cachedPath, expectedHash))) {
    await download(
      `https://raw.githubusercontent.com/tesseract-ocr/tessdata/${TESSDATA_COMMIT}/${fileName}`,
      cachedPath,
    );
  }
  if (!(await hasHash(cachedPath, expectedHash))) {
    throw new Error(`Tesseract language checksum mismatch: ${fileName}`);
  }
  await copyFile(cachedPath, targetPath);
}

if (
  (await isDirectory(destinationApp)) &&
  (await readFile(markerPath, "utf8").catch(() => "")) === `${PREPARED_REVISION}\n` &&
  (await isFile(archivePath))
) {
  console.log(`Audiveris ${VERSION} is already prepared.`);
  process.exit(0);
}

if (!(await hasExpectedHash(imagePath))) {
  console.log(`Downloading ${FILE_NAME}...`);
  await download(DOWNLOAD_URL, imagePath, "application/octet-stream");
}

if (!(await hasExpectedHash(imagePath))) {
  throw new Error("Audiveris download checksum mismatch.");
}

const mountPoint = await mkdtemp(path.join(tmpdir(), "togpx-audiveris-"));
let mounted = false;

try {
  execFileSync(
    "hdiutil",
    [
      "attach",
      imagePath,
      "-nobrowse",
      "-readonly",
      "-noverify",
      "-noautoopen",
      "-mountpoint",
      mountPoint,
    ],
    { stdio: ["pipe", "ignore", "inherit"], input: "Y\n" },
  );
  mounted = true;

  const appName = (await readdir(mountPoint)).find((name) => name.endsWith(".app"));
  if (!appName) {
    throw new Error("The Audiveris disk image does not contain an application bundle.");
  }

  await rm(destinationApp, { recursive: true, force: true });
  await cp(path.join(mountPoint, appName), destinationApp, {
    recursive: true,
    preserveTimestamps: true,
    dereference: true,
  });

  await copyFile(path.join(root, "LICENSE"), path.join(destination, "AUDIVERIS_LICENSE.txt"));
  await writeFile(
    path.join(destination, "AUDIVERIS_SOURCE.txt"),
    `Audiveris ${VERSION}\nhttps://github.com/Audiveris/audiveris/tree/${VERSION}\n`,
  );
  await makeTreeReadable(destinationApp);

  execFileSync(
    "codesign",
    [
      "--force",
      "--deep",
      "--sign",
      "-",
      "--preserve-metadata=identifier,entitlements",
      destinationApp,
    ],
    { stdio: "inherit" },
  );
  execFileSync("codesign", ["--verify", "--deep", "--strict", destinationApp], {
    stdio: "inherit",
  });
  await rm(archivePath, { force: true });
  execFileSync(
    "ditto",
    ["-c", "-k", "--sequesterRsrc", "--keepParent", destinationApp, archivePath],
    { stdio: "inherit" },
  );
  await writeFile(markerPath, `${PREPARED_REVISION}\n`);

  console.log(`Prepared Audiveris ${VERSION} archive at ${archivePath}`);
} finally {
  if (mounted) {
    execFileSync("hdiutil", ["detach", mountPoint, "-quiet"], { stdio: "ignore" });
  }
  await rm(mountPoint, { recursive: true, force: true });
}

async function hasExpectedHash(filePath) {
  return hasHash(filePath, EXPECTED_SHA256);
}

async function hasHash(filePath, expectedHash) {
  if (!(await isFile(filePath))) {
    return false;
  }
  const contents = await readFile(filePath);
  return createHash("sha256").update(contents).digest("hex") === expectedHash;
}

async function download(url, destinationPath, accept = "*/*") {
  execFileSync(
    "curl",
    [
      "-fL",
      "--retry",
      "3",
      "--connect-timeout",
      "30",
      "--header",
      `Accept: ${accept}`,
      "--header",
      "User-Agent: ToGpx-build",
      "--output",
      destinationPath,
      url,
    ],
    { stdio: "inherit" },
  );
}

async function isFile(filePath) {
  try {
    return (await stat(filePath)).isFile();
  } catch {
    return false;
  }
}

async function isDirectory(filePath) {
  try {
    return (await stat(filePath)).isDirectory();
  } catch {
    return false;
  }
}

async function makeTreeReadable(directory) {
  await chmod(directory, 0o755);
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const fullPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      await makeTreeReadable(fullPath);
    } else if (entry.isFile()) {
      const current = await stat(fullPath);
      await chmod(fullPath, current.mode & 0o111 ? 0o755 : 0o644);
    }
  }
}
