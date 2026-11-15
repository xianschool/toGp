import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const resourceRoot = path.join(root, "src-tauri/resources/tab-converter");
const python = path.join(resourceRoot, "python/bin/python3.12");
const sitePackages = path.join(resourceRoot, "python/lib/python3.12/site-packages");
const inspector = path.join(root, "scripts/score-source-inspector.py");

if (!existsSync(python) || !existsSync(sitePackages)) {
  console.error("Tab converter runtime is missing. Run: npm run prepare:tab-converter");
  process.exit(1);
}

execFileSync(
  python,
  [inspector, ...process.argv.slice(2)],
  {
    env: {
      ...process.env,
      PYTHONPATH: sitePackages,
      PYTHONDONTWRITEBYTECODE: "1",
      PYTHONNOUSERSITE: "1",
    },
    stdio: "inherit",
  },
);
