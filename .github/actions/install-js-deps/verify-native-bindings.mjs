// Guard against npm/cli#4828: `npm ci` intermittently exits 0 while omitting a
// platform-specific optional native binding that IS present in the lockfile, so
// the failure only surfaces later as "Cannot find native binding" at build
// time. This script reifies that silent gap into a caught condition: it reads
// the lockfile, determines every top-level optional native binding that SHOULD
// be installed on the current platform, checks each landed on disk, repairs the
// exact missing ones, and exits non-zero only if a binding is still absent
// after repair (a reproducible problem rather than the transient race).

import { execSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

const platform = process.platform; // e.g. "linux"
const arch = process.arch; // e.g. "x64"
// Standard GitHub Linux runners are glibc; musl bindings must be skipped so a
// glibc runner does not demand a musl artifact it will never use.
const GLIBC = "glibc";

let lock;
try {
  lock = JSON.parse(readFileSync("package-lock.json", "utf8"));
} catch (err) {
  console.error(
    `::error::Failed to read or parse package-lock.json: ${err.message}. ` +
      "Ensure npm ci ran in this working-directory.",
  );
  process.exit(1);
}
const packages = lock.packages ?? {};

// A native binding is an optional dependency pinned to a concrete os + cpu.
// Restrict to TOP-LEVEL entries (`node_modules/<name>`, no nesting): those are
// what the build's own toolchain resolves. Nested copies (e.g. storybook's
// private oxc) resolve up to the top-level install anyway.
const required = [];
for (const [path, meta] of Object.entries(packages)) {
  if (!path.startsWith("node_modules/")) continue;
  if (path.split("node_modules/").length - 1 !== 1) continue; // top-level only
  if (!meta?.optional) continue;
  if (!Array.isArray(meta.os) || !meta.os.includes(platform)) continue;
  if (!Array.isArray(meta.cpu) || !meta.cpu.includes(arch)) continue;
  if (Array.isArray(meta.libc) && !meta.libc.includes(GLIBC)) continue;
  const name = path.slice("node_modules/".length);
  required.push({ name, version: meta.version, dir: path });
}

const isMissing = (pkg) => !existsSync(join(pkg.dir, "package.json"));

if (required.length === 0) {
  console.log("No platform-matched native bindings in lockfile; nothing to verify.");
  process.exit(0);
}

let missing = required.filter(isMissing);
if (missing.length === 0) {
  console.log(`All ${required.length} platform native binding(s) present.`);
  process.exit(0);
}

const names = missing.map((p) => p.name).join(", ");
console.warn(
  `::warning::npm/cli#4828: ${missing.length}/${required.length} native binding(s) ` +
    `missing after npm ci (${names}); repairing the exact artifacts.`,
);

const spec = missing.map((p) => `${p.name}@${p.version}`).join(" ");
// No --force: we install exact lockfile name@version pairs on the matching
// platform, so there is nothing to force past, and dropping it lets npm's
// native error surface if a binding genuinely cannot install.
execSync(`npm install ${spec} --no-save --ignore-scripts --no-audit --no-fund`, {
  stdio: "inherit",
});

missing = required.filter(isMissing);
if (missing.length > 0) {
  const stillGone = missing.map((p) => p.name).join(", ");
  console.error(
    `::error::Native binding(s) still missing after repair (${stillGone}); ` +
      `this is reproducible, not the transient npm race. Investigate the lockfile / registry.`,
  );
  process.exit(1);
}

console.log("Native binding(s) repaired; all platform artifacts now present.");
