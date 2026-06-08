#!/usr/bin/env node
// Format release notes from conventional-commit subject lines.
//
// Usage:
//   node scripts/format_release_notes.mjs [FILE]
//
// Reads commit subjects (one per line) from FILE, or from stdin if no file is
// given, and prints grouped markdown release notes. No third-party
// dependencies — Node.js built-ins only.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const SECTIONS = [
  { key: "breaking", title: "Breaking changes" },
  { key: "feat", title: "Features" },
  { key: "fix", title: "Fixes" },
  { key: "perf", title: "Performance" },
  { key: "other", title: "Other" },
];

const TYPE_TO_SECTION = { feat: "feat", fix: "fix", perf: "perf" };

export function parseLine(line) {
  const text = line.trim();
  if (!text) return null;
  const match = text.match(/^(\w+)(\([^)]*\))?(!)?:\s*(.+)$/);
  if (!match) {
    return { section: "other", text };
  }
  const [, type, , bang, summary] = match;
  const isBreaking = Boolean(bang) || /BREAKING CHANGE/.test(text);
  if (isBreaking) return { section: "breaking", text: summary };
  const section = TYPE_TO_SECTION[type.toLowerCase()] ?? "other";
  return { section, text: summary };
}

export function formatNotes(input) {
  const buckets = new Map(SECTIONS.map((s) => [s.key, []]));
  for (const line of input.split("\n")) {
    const parsed = parseLine(line);
    if (parsed) buckets.get(parsed.section).push(parsed.text);
  }

  const out = ["# Release notes", ""];
  for (const { key, title } of SECTIONS) {
    const items = buckets.get(key);
    if (items.length === 0) continue;
    out.push(`## ${title}`, "");
    for (const item of items) out.push(`- ${item}`);
    out.push("");
  }
  if (out.length === 2) out.push("_No notable changes._", "");
  return `${out.join("\n").trimEnd()}\n`;
}

function main() {
  const file = process.argv[2];
  const input = file ? readFileSync(file, "utf8") : readFileSync(0, "utf8");
  process.stdout.write(formatNotes(input));
}

// Run only when executed directly, not when imported by a test harness.
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main();
}
