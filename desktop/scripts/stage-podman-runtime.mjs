import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { spawnSync } from "node:child_process";

const desktopRoot = path.resolve(import.meta.dirname, "..");
const target = path.join(desktopRoot, "vendor", "podman", "macos-arm64");
const krunkitEntitlements = path.join(desktopRoot, "krunkit-entitlements.plist");
const source =
  process.env.PODMAN_BUNDLE_ROOT ||
  process.env.LEMMA_PODMAN_BUNDLE_ROOT ||
  process.argv[2];

function usage() {
  console.error(
    "Usage: PODMAN_BUNDLE_ROOT=/path/to/podman-runtime npm run stage:podman"
  );
}

if (!source) {
  usage();
  process.exit(2);
}

const sourceRoot = path.resolve(source);
const sourcePodman = path.join(sourceRoot, "bin", "podman");

if (!fs.existsSync(sourcePodman)) {
  console.error(`Podman bundle must contain bin/podman: ${sourceRoot}`);
  process.exit(1);
}

fs.rmSync(target, { recursive: true, force: true });
fs.mkdirSync(path.dirname(target), { recursive: true });
fs.cpSync(sourceRoot, target, {
  recursive: true,
  force: true,
  filter: (src) => !src.split(path.sep).some((part) => part === ".DS_Store")
});

fs.chmodSync(path.join(target, "bin", "podman"), 0o755);
fixMacRuntime(target);
console.log(`Staged Podman runtime at ${target}`);

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    encoding: "utf8",
    stdio: "pipe",
    ...options
  });
  if (result.status !== 0) {
    const output = `${result.stdout || ""}${result.stderr || ""}`.trim();
    throw new Error(`${command} ${args.join(" ")} failed\n${output}`);
  }
  return result.stdout || "";
}

function maybeRun(command, args) {
  const result = spawnSync(command, args, {
    encoding: "utf8",
    stdio: "pipe"
  });
  return {
    ok: result.status === 0,
    output: `${result.stdout || ""}${result.stderr || ""}`.trim()
  };
}

function walkFiles(root) {
  const found = [];
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const fullPath = path.join(root, entry.name);
    if (entry.isDirectory()) {
      found.push(...walkFiles(fullPath));
    } else if (entry.isFile()) {
      found.push(fullPath);
    }
  }
  return found;
}

function fixMacRuntime(runtimeRoot) {
  if (process.platform !== "darwin") return;

  const krunkit = path.join(runtimeRoot, "bin", "krunkit");
  if (fs.existsSync(krunkit)) {
    const rpaths = maybeRun("otool", ["-l", krunkit]).output;
    if (!rpaths.includes("@executable_path/../lib")) {
      const added = maybeRun("install_name_tool", [
        "-add_rpath",
        "@executable_path/../lib",
        krunkit
      ]);
      if (!added.ok && !added.output.includes("would duplicate path")) {
        throw new Error(added.output);
      }
    }
  }

  for (const file of walkFiles(runtimeRoot)) {
    const relative = path.relative(runtimeRoot, file);
    if (relative.startsWith(`bin${path.sep}`)) fs.chmodSync(file, 0o755);
    if (relative === path.join("bin", "krunkit")) {
      run("codesign", ["--force", "--sign", "-", "--entitlements", krunkitEntitlements, file]);
    } else if (relative.endsWith(".dylib")) {
      run("codesign", ["--force", "--sign", "-", file]);
    }
  }
}
