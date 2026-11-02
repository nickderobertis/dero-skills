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
//     the `version` field of an inline dependency table whose own `path` field is a
//     string (a crate of this workspace) — read field by field, so a `path` inside a
//     comment or another string does not count. A file holding a multi-line string,
//     or a dependency it cannot read exactly, may not change at all.
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

// The tokens of one TOML line: strings (basic, with escapes, and literal), the
// punctuation of an inline table, bare words, and a trailing comment — a `#` counts
// only outside a string. Null when the line does not tokenize (an unterminated
// string), so the caller leaves it as it is.
function tokenize(line) {
  const tokens = [];
  let i = 0;
  while (i < line.length) {
    const c = line[i];
    if (c === " " || c === "\t") i += 1;
    else if (c === "#") {
      tokens.push({ kind: "comment", start: i, end: line.length });
      break;
    } else if (c === '"' || c === "'") {
      let j = i + 1;
      while (j < line.length && line[j] !== c) j += c === '"' && line[j] === "\\" ? 2 : 1;
      if (j >= line.length) return null;
      tokens.push({ kind: "string", start: i, end: j + 1, value: line.slice(i + 1, j) });
      i = j + 1;
    } else if ("{}[]=,".includes(c)) {
      tokens.push({ kind: c, start: i, end: i + 1 });
      i += 1;
    } else {
      const word = /^[A-Za-z0-9_.+-]+/.exec(line.slice(i));
      if (!word) return null;
      tokens.push({ kind: "word", start: i, end: i + word[0].length, value: word[0] });
      i += word[0].length;
    }
  }
  return tokens;
}

// A dependency written as one inline table, `name = { key = value, ... } # comment`,
// read field by field: each top-level key, and the token holding its value when that
// value is a string. Null for anything else — a dotted or quoted name, a nested
// table, a value spanning lines — so only a line read exactly is ever normalized.
function inlineDependency(line) {
  const tokens = tokenize(line);
  if (!tokens) return null;
  const at = (n, kind) => tokens[n]?.kind === kind;
  if (!(at(0, "word") && at(1, "=") && at(2, "{"))) return null;
  const fields = new Map();
  let n = 3;
  while (!at(n, "}")) {
    if (!(at(n, "word") || at(n, "string")) || !at(n + 1, "=")) return null;
    const key = tokens[n].value;
    if (fields.has(key)) return null;
    n += 2;
    if (at(n, "string") || at(n, "word")) {
      fields.set(key, tokens[n]);
      n += 1;
    } else if (at(n, "[")) {
      n += 1;
      while (!at(n, "]")) {
        if (!(at(n, "string") || at(n, "word"))) return null;
        n += 1;
        if (at(n, ",")) n += 1;
        else if (!at(n, "]")) return null;
      }
      fields.set(key, null);
      n += 1;
    } else return null;
    if (at(n, ",")) n += 1;
    else if (!at(n, "}")) return null;
  }
  n += 1;
  if (at(n, "comment")) n += 1;
  return n === tokens.length ? fields : null;
}

function normalizeCargoToml(text) {
  // A multi-line string could make any line read as something it is not, so a file
  // holding one is compared as it stands: every change to it fails.
  if (text.includes('"""') || text.includes("'''")) return text;
  let section = "";
  return text.split("\n").map((line) => {
    const header = /^\s*\[\[?\s*([^\]"'#]+?)\s*\]\]?\s*(?:#.*)?$/.exec(line);
    if (header) {
      section = header[1];
      return line;
    }
    if (section === "package" || section === "workspace.package") return placeholder(line);
    // A workspace crate named inline, `name = { path = "...", version = "..." }`: its
    // `path` and `version` must be fields of the table itself, never text inside a
    // comment or another field's string.
    if (/(^|\.)(dev-|build-)?dependencies$/.test(section)) {
      const fields = inlineDependency(line);
      const path = fields?.get("path");
      const version = fields?.get("version");
      if (path?.kind === "string" && version?.kind === "string" && SEMVER.test(version.value))
        return `${line.slice(0, version.start)}"${V}"${line.slice(version.end)}`;
    }
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
