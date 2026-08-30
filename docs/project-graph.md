# Project graph

Nx owns *running* targets; each `project.json` owns what its targets do. The
split is by **test tier** and by **cost**, per the create-repo skill's
`references/project-graph.md` and `references/languages/python.md`.

| Project | Root | Targets |
| --- | --- | --- |
| `authoring-tools` | `tools/` | `format` `format-check` `lint` `validate` `test` |
| `authoring-scripts` | `scripts/` | `lint` |
| `consumer-bootstrap` | `consumer-bootstrap/` | `lint` |
| `bootstrap-create-repo` | `skills/bootstrap/create-repo/` | `format` `format-check` `lint` `validate` `smoke` `test` |
| `bootstrap-create-repo-e2e` | `.../create-repo/tests/e2e/` | `format` `format-check` `lint` `test` |
| `bootstrap-create-repo-skilltest` | `.../create-repo/tests/skilltest/` | `format` `format-check` `lint` **`skilltest`** |
| `project-graph-e2e` | `tests/project-graph/` | `format` `format-check` `lint` `test` |
| `llmlint-tier` | `tests/llmlint/` | `format` `format-check` `lint` `validate` `test` **`lint-llm`** |
| `repo-baseline` | `tests/baseline/` | `format` `format-check` `lint` `test` |

`project-graph-e2e` covers this seam from both sides: `test_project_graph.py`
asserts what `nx affected` selects, and `test_command_surface.py` drives the real
`just` to assert what the recipes ask Nx for (the tier, the merge base, and the
boundary refusals).

What is not obvious from the table:

- **Target names are uniform, and that is what makes one root recipe cover the
  repo.** `format` formats in place, `format-check` verifies, `lint` fails on
  findings (ruff for Python, shellcheck for shell), `validate` is a project's
  deterministic, model-free contract check, `smoke` runs each bundled skill
  script once, `test` is that project's pytest. `just check` fans out over
  `format-check lint validate smoke test` — nothing else.
- **The two bold targets are the promoted tiers.** `skilltest` drives a real
  ~20-30 minute harness bootstrap; `lint-llm` drives a judged model. Both leave
  the affected tier *unconditionally* because of what they contact
  (`ci.md`), not because of how long they take — so their names are absent from
  the gate's fan-out, and `llmlint-tier` depends on nothing, which is what keeps
  a change elsewhere in the graph from reaching it. `tests/project-graph/`
  asserts both properties against the real `nx` binary; do not weaken them by
  adding either name to `just check`.
- **The nested projects under the skill are deliberate.** Nx assigns each file to
  the project with the longest matching root, so `tests/e2e/` and
  `tests/skilltest/` own their files and the skill project does not. That is why
  the skill's `ruff`/`pytest` commands carry `--exclude`/`--ignore` for those two
  directories: without them the parent would re-check files it does not own, and
  its cache key would not cover them.
- **Cross-project edges that are not imports live in `nx.json`'s named inputs.**
  `tooling` attaches the shared validator to the skills it validates,
  `dogfoodedContracts` attaches the oneharness template to its drift gate,
  `repoBaseline` attaches this repo's root configuration (justfile, workflows,
  `AGENTS.md`, every `project.json`) to the audit that reads it, and
  `llmlintTier` attaches `llmlint.yml` and the rule fragments to the tier they
  configure, and `skillEval` attaches the harness-driven eval to the fast tier
  that checks its path constants (`tests/test_eval_wiring.py` — the eval never
  runs in the gate, so a broken constant inside it is otherwise invisible). An input is the only thing keeping a cached pass from outliving an
  edit to a file outside the project — add one whenever a target reads across a
  boundary.
