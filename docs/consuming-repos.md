# Consuming repos

How an application repo adopts skills from this canonical repo
(`nickderobertis/dero-skills`).

## Model

- Skills are installed **project-scoped** into the consuming repo, for three
  agent targets: Cursor, Claude Code, and VS Code / GitHub Copilot.
- The consuming repo hard-codes **one** project skill set. There are no install
  profiles and no preview flow.
- Skills are **not pinned** by default. Agents update installed skills at the
  start of each session so they always use current approved guidance.

## One-time bootstrap

Copy the reusable setup files from `consumer-bootstrap/` into the application
repo:

```bash
tmp_dir="$(mktemp -d)"
gh repo clone nickderobertis/dero-skills "$tmp_dir" -- --depth 1
rsync -av "$tmp_dir/consumer-bootstrap/" ./
rm -rf "$tmp_dir"
```

This lands in the consuming repo:

```text
scripts/setup-agent-skills-runtime.sh
scripts/install-agent-skills.sh
scripts/update-agent-skills.sh
scripts/check-agent-skills-runtime.sh
docs/agent-skills.md
AGENTS.agent-skills.md      # paste its section into the repo's AGENTS.md
```

## Configure (edit one file only)

Edit **only** `scripts/install-agent-skills.sh`:

- Replace `<org>/<skills-repo>` with `nickderobertis/dero-skills`.
- Replace the `SKILLS=(...)` list with the project's required skills, using
  exact paths, e.g. `skills/bootstrap/create-repo`.
- Keep `AGENTS=(cursor claude-code github-copilot)`.
- Keep `--scope project`.
- Do **not** add `--pin`.

Then paste the contents of `AGENTS.agent-skills.md` into the repo's `AGENTS.md`.

## Install and run

```bash
./scripts/setup-agent-skills-runtime.sh   # one-time: gh, uv, python, node, bun
./scripts/install-agent-skills.sh         # install the hard-coded skill set
```

At the start of each agent session:

```bash
./scripts/check-agent-skills-runtime.sh   # verify runtime tools
./scripts/update-agent-skills.sh          # gh skill update --all
```

If the update fails because `gh` is unavailable, auth is missing, or the network
is down, report it briefly and continue with the currently installed skills.

## Running skill scripts

Run scripts as each skill's `SKILL.md` documents:

```bash
uv run --script scripts/<script>.py [args...]   # Python skill scripts
node scripts/<script>.mjs [args...]             # JavaScript skill scripts
```

Skills that ship their own `package.json` may instead require:

```bash
bun install --frozen-lockfile
bun run <script-name> [args...]
```

Do **not** run installed skill scripts through Nx — Nx belongs to this repo's
authoring workflow, not to runtime use in a consuming repo.

## Don'ts

- Don't manually copy skill contents into the consuming repo.
- Don't pin skill versions unless explicitly requested.
- Don't add install profiles or preview steps.
- Don't depend on Nx, asdf, or direnv at runtime.
