# Agent Skills

This repo uses project-scoped Agent Skills from the canonical skills repo.

Skills are not pinned by default. Agents update installed skills at the beginning of each session so they use current approved guidance.

## Supported platforms

- Cursor
- Claude Code
- VS Code / GitHub Copilot

## Setup

```bash
./scripts/setup-agent-skills-runtime.sh
./scripts/install-agent-skills.sh
```

## Session start

```bash
./scripts/check-agent-skills-runtime.sh
./scripts/update-agent-skills.sh
```

## Runtime rules

Python skill scripts should be run with:

```bash
uv run --script scripts/<script>.py [args...]
```

JavaScript skill scripts should be run with:

```bash
node scripts/<script>.mjs [args...]
```

Skills with local JavaScript dependencies may require:

```bash
pnpm install --frozen-lockfile
pnpm run <script-name> -- [args...]
```

Do not run installed skill scripts through Nx.
