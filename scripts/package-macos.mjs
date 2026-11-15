import { execFileSync } from "node:child_process";
import { cp, mkdir, rm, symlink } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const target = "aarch64-apple-darwin";
const releaseDir = path.join(
  root,
  "src-tauri/target",
  target,
  "release",
);
const appPath = path.join(releaseDir, "bundle/macos/ToGpx.app");
const dmgPath = path.join(releaseDir, "bundle/dmg/ToGpx_0.1.0_aarch64.dmg");
const stageDir = path.join(releaseDir, "dmg-stage");
const identity = process.env.APPLE_SIGNING_IDENTITY || "-";

execFileSync(
  "npx",
  ["tauri", "build", "--target", target, "--bundles", "app"],
  { cwd: root, stdio: "inherit" },
);

execFileSync("xattr", ["-cr", appPath], { stdio: "inherit" });
execFileSync(
  "codesign",
  ["--force", "--deep", "--sign", identity, appPath],
  { stdio: "inherit" },
);
execFileSync(
  "codesign",
  ["--verify", "--deep", "--strict", appPath],
  { stdio: "inherit" },
);

await rm(stageDir, { recursive: true, force: true });
await mkdir(stageDir, { recursive: true });
await cp(appPath, path.join(stageDir, "ToGpx.app"), {
  recursive: true,
  preserveTimestamps: true,
});
await symlink("/Applications", path.join(stageDir, "Applications"));
await rm(dmgPath, { force: true });

execFileSync(
  "hdiutil",
  [
    "create",
    "-volname",
    "ToGpx",
    "-srcfolder",
    stageDir,
    "-ov",
    "-format",
    "UDZO",
    dmgPath,
  ],
  { stdio: "inherit" },
);

await rm(stageDir, { recursive: true, force: true });
console.log(`Signed and packaged ${dmgPath}`);
