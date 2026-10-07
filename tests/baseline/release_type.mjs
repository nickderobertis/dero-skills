// The release type semantic-release's commit-analyzer, configured as
// .releaserc.json configures it, gives each commit message in argv[2] (a JSON
// array). Prints a JSON object from message to "major" | "minor" | "patch" | null.
import { readFileSync } from "node:fs";
import { analyzeCommits } from "@semantic-release/commit-analyzer";

const ANALYZER = "@semantic-release/commit-analyzer";
const releaserc = JSON.parse(readFileSync(".releaserc.json", "utf8"));
const entry = releaserc.plugins.find((p) => (Array.isArray(p) ? p[0] : p) === ANALYZER);
if (entry === undefined) {
  console.error(`.releaserc.json does not configure ${ANALYZER}`);
  process.exit(1);
}
const pluginConfig = Array.isArray(entry) ? (entry[1] ?? {}) : {};
const logger = { log() {}, error() {}, warn() {}, success() {} };
const types = {};
for (const message of JSON.parse(process.argv[2])) {
  types[message] = await analyzeCommits(pluginConfig, {
    commits: [{ hash: "0000000", message }],
    logger,
    cwd: process.cwd(),
    options: {},
  });
}
console.log(JSON.stringify(types));
