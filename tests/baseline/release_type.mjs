// The release type semantic-release's commit-analyzer, configured as
// .releaserc.json configures it, gives each commit message in argv[2] (a JSON
// array). Prints a JSON object from message to "major" | "minor" | "patch" | null.
import { readFileSync } from "node:fs";
import { analyzeCommits } from "@semantic-release/commit-analyzer";

const ANALYZER = "@semantic-release/commit-analyzer";

function fail(message) {
  console.error(`release_type: ${message}`);
  process.exit(1);
}

const isObject = (value) =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const releaserc = JSON.parse(readFileSync(".releaserc.json", "utf8"));
if (!isObject(releaserc) || !Array.isArray(releaserc.plugins)) {
  fail(".releaserc.json must be an object with a `plugins` array");
}
const entry = releaserc.plugins.find(
  (plugin) => (Array.isArray(plugin) ? plugin[0] : plugin) === ANALYZER,
);
if (entry === undefined) {
  fail(`.releaserc.json does not configure ${ANALYZER}`);
}
const pluginConfig = Array.isArray(entry) ? (entry[1] ?? {}) : {};
if (!isObject(pluginConfig)) {
  fail(`${ANALYZER}'s options in .releaserc.json must be an object`);
}

const messages = JSON.parse(process.argv[2] ?? "null");
if (!Array.isArray(messages) || !messages.every((m) => typeof m === "string")) {
  fail("pass the commit messages as one JSON array of strings");
}

const logger = { log() {}, error() {}, warn() {}, success() {} };
const types = {};
for (const message of messages) {
  types[message] = await analyzeCommits(pluginConfig, {
    commits: [{ hash: "0000000", message }],
    logger,
    cwd: process.cwd(),
    options: {},
  });
}
console.log(JSON.stringify(types));
