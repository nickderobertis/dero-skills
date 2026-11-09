// Copied from dero-skills' create-repo skill; edit only the source.
// source: github.com/nickderobertis/dero-skills/skills/bootstrap/create-repo/assets/ci/coverage-merge.mjs
// version: 1
// sha256: 93fb9c990694cfebf8033cdd98432f7db91926182ea716e1d59289bcfd19cb63
//
// The repository's combined coverage check over its coverage units' cached LCOV reports.
//
//   node coverage-merge.mjs --fail-under-lines <N> --dir <dir> --unit <unit>...
//
// <N> is a floor on the line percentage, a decimal of at most two places. `--unit` takes one
// or more names and may repeat.
//
// Reads exactly <dir>/<unit>.lcov for each named unit, and lists <dir> to refuse what no
// named unit owns. Writes nothing. It runs no build and no llvm tool.
//
// DA: records are summed per file and line: a line counts once in the total, and as covered
// when any unit's count for it is above zero. Prints lines covered, lines total and the
// percentage; below the floor, each file's uncovered lines too.
//
// Exit: 0 at or above the floor (compared as an exact ratio in integer arithmetic). 1 below
// it. 2 for a usage error, a named unit whose report is missing, a file under <dir> no named
// unit owns, a malformed record, an absolute SF: path, or reports holding no line at all.
//
// Every file read was restored under its unit's current key or written by its run in this
// same Nx invocation, because the `coverage` target depends on every unit; a file that is
// neither is what this refuses, so a leftover report cannot reach the total.
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

const refuse = (message) => {
  console.error(`coverage-merge: ${message}`);
  process.exit(2);
};
const usage = () =>
  refuse('usage: node coverage-merge.mjs --fail-under-lines <N> --dir <dir> --unit <unit>...');

let floorText;
let dir;
const units = [];
const args = process.argv.slice(2);
for (let i = 0; i < args.length; ) {
  const flag = args[i++];
  if (flag === '--fail-under-lines' && floorText === undefined && i < args.length) {
    floorText = args[i++];
  } else if (flag === '--dir' && dir === undefined && i < args.length) {
    dir = args[i++];
  } else if (flag === '--unit') {
    const start = i;
    while (i < args.length && !args[i].startsWith('--')) units.push(args[i++]);
    if (i === start) usage();
  } else {
    usage();
  }
}
if (floorText === undefined || dir === undefined || units.length === 0) usage();
if (!/^\d{1,3}(\.\d{1,2})?$/.test(floorText)) {
  refuse(`--fail-under-lines ${floorText}: a percentage of at most two decimals`);
}
const [whole, fraction = ''] = floorText.split('.');
const floorHundredths = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'));
if (floorHundredths > 10000n) refuse(`--fail-under-lines ${floorText}: above 100`);
if (new Set(units).size !== units.length) refuse('a unit is named twice');

const owned = new Set(units.map((unit) => `${unit}.lcov`));
const present = existsSync(dir) ? readdirSync(dir, { withFileTypes: true }) : [];
const stray = present.filter((entry) => !(entry.isFile() && owned.has(entry.name)));
if (stray.length) {
  refuse(`${dir} holds what no named unit owns: ${stray.map((e) => e.name).sort().join(', ')}`);
}
const missing = units.filter((unit) => !existsSync(join(dir, `${unit}.lcov`)));
if (missing.length) {
  refuse(`no report for unit(s) ${missing.join(', ')} under ${dir}: a task ran or restored nothing`);
}

// file -> line -> covered
const files = new Map();
const OTHER_RECORD = /^(TN|FN|FNDA|FNF|FNH|BRDA|BRF|BRH|LF|LH):/;
for (const unit of units) {
  const name = `${unit}.lcov`;
  let lines = null;
  const text = readFileSync(join(dir, name), 'utf8');
  for (const [index, line] of text.split(/\r?\n/).entries()) {
    const where = `${name}:${index + 1}`;
    if (line === '') continue;
    if (line.startsWith('SF:')) {
      const path = line.slice(3);
      if (!path) refuse(`${where}: malformed record ${line}`);
      if (path.startsWith('/') || path.startsWith('\\') || /^[A-Za-z]:/.test(path)) {
        refuse(`${where}: absolute SF: path ${path}`);
      }
      lines = files.get(path) ?? new Map();
      files.set(path, lines);
    } else if (line.startsWith('DA:')) {
      const match = /^DA:([1-9]\d*),(\d+)(?:,.*)?$/.exec(line);
      if (!match || lines === null) refuse(`${where}: malformed record ${line}`);
      const number = Number(match[1]);
      lines.set(number, (lines.get(number) ?? false) || match[2].replace(/^0+/, '') !== '');
    } else if (line === 'end_of_record') {
      if (lines === null) refuse(`${where}: end_of_record outside a record`);
      lines = null;
    } else if (!OTHER_RECORD.test(line)) {
      refuse(`${where}: malformed record ${line}`);
    }
  }
  if (lines !== null) refuse(`${name}: truncated, its last record has no end_of_record`);
}

let total = 0n;
let covered = 0n;
const uncovered = new Map();
for (const [path, lines] of [...files].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))) {
  for (const [number, isCovered] of [...lines].sort(([a], [b]) => a - b)) {
    total += 1n;
    if (isCovered) {
      covered += 1n;
    } else {
      uncovered.set(path, [...(uncovered.get(path) ?? []), number]);
    }
  }
}
if (total === 0n) refuse(`the named reports under ${dir} hold no line at all`);

const tenThousandths = (covered * 1000000n) / total;
const percent = `${tenThousandths / 10000n}.${String(tenThousandths % 10000n).padStart(4, '0')}`;
console.log(`lines covered: ${covered}`);
console.log(`lines total: ${total}`);
console.log(`lines percent: ${percent}`);

if (covered * 10000n < floorHundredths * total) {
  console.log(`below the floor of ${floorText}. Uncovered lines:`);
  for (const [path, numbers] of uncovered) console.log(`${path}: ${numbers.join(',')}`);
  process.exit(1);
}
