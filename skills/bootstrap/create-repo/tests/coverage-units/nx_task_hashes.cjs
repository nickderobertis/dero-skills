// Prints each coverage unit's real Nx input hashes, per input, for the workspace in argv[2].
const { createRequire } = require('node:module');
const path = require('node:path');

process.chdir(process.argv[2]);
const nxRoot = createRequire(path.join(process.cwd(), 'noop.js'));
const { createProjectGraphAsync } = nxRoot('nx/src/project-graph/project-graph');
const { readNxJson } = nxRoot('nx/src/config/nx-json');
const { createTaskGraph } = nxRoot('nx/src/tasks-runner/create-task-graph');
const { createTaskHasher } = nxRoot('nx/src/hasher/create-task-hasher');

(async () => {
  const graph = await createProjectGraphAsync();
  const nx = readNxJson();
  const units = process.argv.slice(3);
  const tasks = createTaskGraph(graph, nx.targetDefaults ?? {}, units, ['test'], undefined, {});
  const hasher = createTaskHasher(graph, nx);
  const hashes = {};
  for (const unit of units) {
    // Each input's own hash, so a file's effect is read without the runtime inputs' noise.
    hashes[unit] = (await hasher.hashTask(tasks.tasks[`${unit}:test`], tasks)).details.nodes;
  }
  console.log(JSON.stringify(hashes));
  process.exit(0);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
