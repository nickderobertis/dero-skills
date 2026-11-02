#!/usr/bin/env node
// Copied from dero-skills' create-repo skill by `compose_repo_plan.py --ci-cd`.
//
// Holds a release pull request's branch to the release tool's own edits, so the test
// jobs it skips have nothing to prove. ci.yml's `lint` job runs it on the release
// tool's branch:
//
//   node scripts/release-branch-guard.mjs --base <sha> [--head <ref>]
//
// It compares the merge base of <sha> and <ref> (default HEAD) with <ref>, file by
// file, by content rather than by name:
//
//   * CHANGELOG.md (any directory) may change, or be added, freely: it runs nothing.
//   * Cargo.toml may change only the `version` of [package] / [workspace.package] and
//     the `version` of a dependency that names a `path` (a crate of this workspace).
//   * Cargo.lock may change only the `version` of a package with no `source` (a
//     workspace member), and that member's version where a dependency list names it.
//   * package.json may change only its top-level "version" (formatting is free).
//   * .release-please-manifest.json may change only its version values.
//
// Every new version must be a semantic version. Anything else — another file, a
// deletion or rename, a dependency, a build setting, a script — exits 1 naming each
// offending file and why; the test jobs must run, so land it through an ordinary
// branch. Exit 0 prints one line; exit 2 is an invalid invocation or a failed git read.
import { execFileSync } from "node:child_process";
import { isDeepStrictEqual } from "node:util";

const SEMVER = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/;
const V = "<release-version>";

function usage(message) {
  console.error(`release-branch-guard: ${message}`);
  console.error("usage: node scripts/release-branch-guard.mjs --base <sha> [--head <ref>]");
  process.exit(2);
}

function git(...args) {
  try {
    return execFileSync("git", args, { encoding: "utf8", maxBuffer: 1 << 28 });
  } catch (error) {
    console.error(`release-branch-guard: git ${args.join(" ")} failed: ${error.stderr || error.message}`);
    console.error("  next: fetch the full history (actions/checkout with fetch-depth: 0) and re-run");
    process.exit(2);
  }
}

// A `version = "..."` assignment's value replaced, when it is a semantic version.
const versionLine = /^(\s*version\s*=\s*")([^"]*)("\s*(?:#.*)?)$/;
function placeholder(line) {
  const match = versionLine.exec(line);
  return match && SEMVER.test(match[2]) ? `${match[1]}${V}${match[3]}` : line;
}

function normalizeCargoToml(text) {
  let section = "";
  return text.split("\n").map((line) => {
    const header = /^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(?:#.*)?$/.exec(line);
    if (header) {
      section = header[1];
      return line;
    }
    if (section === "package" || section === "workspace.package") return placeholder(line);
    // A workspace crate named inline: `name = { path = "...", version = "..." }`.
    if (/(^|\.)(dev-|build-)?dependencies$/.test(section) && /^\s*[\w-]+\s*=\s*\{.*\bpath\s*=/.test(line))
      return line.replace(/(\bversion\s*=\s*")([^"]*)(")/, (all, a, v, b) => (SEMVER.test(v) ? `${a}${V}${b}` : all));
    return line;
  }).join("\n");
}

function normalizeCargoLock(text, members) {
  const blocks = text.split(/(?=^\[\[package\]\]$)/m);
  return blocks
    .map((block) => {
      if (!block.startsWith("[[package]]")) return block;
      const local = !/^source\s*=/m.test(block);
      return block
        .split("\n")
        .map((line) => (local ? placeholder(line) : line))
        .join("\n")
        .replace(/"([\w-]+) ([^"\s]+)"/g, (all, name, v) =>
          members.has(name) && SEMVER.test(v) ? `"${name} ${V}"` : all,
        );
    })
    .join("");
}

function lockMembers(text) {
  const names = new Set();
  for (const block of text.split(/^\[\[package\]\]$/m).slice(1))
    if (!/^source\s*=/m.test(block)) {
      const name = /^name\s*=\s*"([^"]+)"/m.exec(block);
      if (name) names.add(name[1]);
    }
  return names;
}

function parseJson(text, side) {
  try {
    return JSON.parse(text);
  } catch {
    throw Error(`its ${side} side is not JSON`);
  }
}

// Why `after` is not a release edit of `before`, or null when it is.
function problem(path, before, after) {
  const name = path.split("/").pop();
  if (name === "CHANGELOG.md") return null;
  if (before === null) return "it is new, and only a CHANGELOG.md may be added";
  if (name === "Cargo.toml")
    return normalizeCargoToml(before) === normalizeCargoToml(after)
      ? null
      : "it changes more than the package version and workspace crates' versions (a dependency, a build setting or another key)";
  if (path === "Cargo.lock") {
    const members = new Set([...lockMembers(before), ...lockMembers(after)]);
    return normalizeCargoLock(before, members) === normalizeCargoLock(after, members)
      ? null
      : "it changes more than workspace members' versions (an external package or a dependency list)";
  }
  if (name === "package.json") {
    const [old, now] = [parseJson(before, "base"), parseJson(after, "head")];
    if (typeof now?.version === "string" && !SEMVER.test(now.version))
      return `its version ${JSON.stringify(now.version)} is not a semantic version`;
    const strip = (pkg) => ({ ...pkg, version: undefined });
    return isDeepStrictEqual(strip(old), strip(now))
      ? null
      : "it changes more than its top-level version (scripts, dependencies or another field)";
  }
  if (path === ".release-please-manifest.json") {
    const [old, now] = [parseJson(before, "base"), parseJson(after, "head")];
    const keys = (o) => Object.keys(o ?? {}).sort();
    if (!isDeepStrictEqual(keys(old), keys(now))) return "it adds or removes a package";
    const bad = Object.entries(now).filter(([, v]) => typeof v !== "string" || !SEMVER.test(v));
    return bad.length ? `${bad.map(([k]) => k).join(", ")} is not given a semantic version` : null;
  }
  return "the release tool edits only versions and changelogs, and this file is neither";
}

const args = process.argv.slice(2);
let base = "";
let head = "HEAD";
for (let i = 0; i < args.length; i += 2) {
  if (!["--base", "--head"].includes(args[i])) usage(`unknown argument: ${args[i]}`);
  if (!args[i + 1]) usage(`${args[i]} needs a value`);
  if (args[i] === "--base") base = args[i + 1];
  else head = args[i + 1];
}
if (!base) usage("name the pull request's base with --base");

const mergeBase = git("merge-base", base, head).trim();
const listing = git("diff", "--name-status", "--no-renames", "-z", mergeBase, head).split("\0").filter(Boolean);
const findings = [];
for (let i = 0; i < listing.length; i += 2) {
  const [status, path] = [listing[i], listing[i + 1]];
  if (status === "D") {
    findings.push(`${path}: it is deleted`);
    continue;
  }
  if (status !== "M" && status !== "A") {
    findings.push(`${path}: its change (${status}) is no release edit`);
    continue;
  }
  const before = status === "A" ? null : git("show", `${mergeBase}:${path}`);
  const after = git("show", `${head}:${path}`);
  try {
    const why = problem(path, before, after);
    if (why) findings.push(`${path}: ${why}`);
  } catch (error) {
    findings.push(`${path}: ${error.message}`);
  }
}
if (findings.length) {
  console.error("release-branch-guard: this release branch carries more than the release tool's version and changelog edits:");
  for (const finding of findings) console.error(`  ${finding}`);
  console.error(
    "  next: the test jobs skip a release branch, so land these changes through an ordinary pull request and let the release tool regenerate this one",
  );
  process.exit(1);
}
console.log(`release-branch-guard: ${listing.length / 2} file(s) changed, all release-tool version and changelog edits`);
