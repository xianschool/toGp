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
const appPath = path.join(releaseDir, "bundle/macos/toGp.app");
const dmgPath = path.join(releaseDir, "bundle/dmg/toGp_0.1.0_aarch64.dmg");
const stageDir = path.join(releaseDir, "dmg-stage");
const notaryProfile = process.env.NOTARYTOOL_PROFILE || "togp-notary";
const identity = findDeveloperIdIdentity();
const shouldNotarize = process.env.SKIP_NOTARIZATION !== "1";

if (shouldNotarize) {
  execFileSync(
    "xcrun",
    ["notarytool", "history", "--keychain-profile", notaryProfile],
    { stdio: "inherit" },
  );
}

execFileSync(
  "npx",
  ["tauri", "build", "--target", target, "--bundles", "app"],
  {
    cwd: root,
    stdio: "inherit",
    env: { ...process.env, APPLE_SIGNING_IDENTITY: identity },
  },
);

execFileSync("xattr", ["-cr", appPath], { stdio: "inherit" });
execFileSync(
  "codesign",
  ["--force", "--options", "runtime", "--timestamp", "--sign", identity, appPath],
  { stdio: "inherit" },
);
execFileSync(
  "codesign",
  ["--verify", "--deep", "--strict", appPath],
  { stdio: "inherit" },
);

await rm(stageDir, { recursive: true, force: true });
await mkdir(stageDir, { recursive: true });
await cp(appPath, path.join(stageDir, "toGp.app"), {
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
    "toGp",
    "-srcfolder",
    stageDir,
    "-ov",
    "-format",
    "UDZO",
    dmgPath,
  ],
  { stdio: "inherit" },
);

execFileSync(
  "codesign",
  ["--force", "--timestamp", "--sign", identity, dmgPath],
  { stdio: "inherit" },
);

if (shouldNotarize) {
  const notaryOutput = execFileSync(
    "xcrun",
    [
      "notarytool",
      "submit",
      dmgPath,
      "--keychain-profile",
      notaryProfile,
      "--output-format",
      "json",
      "--wait",
    ],
    { encoding: "utf8" },
  );
  const result = JSON.parse(notaryOutput);
  if (result.status !== "Accepted") {
    const logPath = path.join(releaseDir, "notary-log.json");
    execFileSync(
      "xcrun",
      ["notarytool", "log", result.id, "--keychain-profile", notaryProfile, logPath],
      { stdio: "inherit" },
    );
    throw new Error(`Apple notarization failed (${result.status}). See ${logPath}`);
  }

  execFileSync("xcrun", ["stapler", "staple", dmgPath], { stdio: "inherit" });
  execFileSync("xcrun", ["stapler", "validate", dmgPath], { stdio: "inherit" });
  execFileSync(
    "spctl",
    [
      "--assess",
      "--type",
      "open",
      "--context",
      "context:primary-signature",
      "--verbose=4",
      dmgPath,
    ],
    { stdio: "inherit" },
  );
}

await rm(stageDir, { recursive: true, force: true });
console.log(`Signed${shouldNotarize ? ", notarized, and stapled" : ""} ${dmgPath}`);

function findDeveloperIdIdentity() {
  if (process.env.APPLE_SIGNING_IDENTITY) {
    return process.env.APPLE_SIGNING_IDENTITY;
  }

  const output = execFileSync("security", ["find-identity", "-v", "-p", "codesigning"], {
    encoding: "utf8",
  });
  const identities = [...output.matchAll(/"([^"]*Developer ID Application: [^"]+)"/g)].map(
    (match) => match[1],
  );
  const teamId = process.env.APPLE_TEAM_ID;
  const identity = identities.find(
    (candidate) => !teamId || candidate.endsWith(`(${teamId})`),
  );
  if (!identity) {
    throw new Error(
      "No Developer ID Application identity found. Install the certificate or set APPLE_SIGNING_IDENTITY.",
    );
  }
  return identity;
}
