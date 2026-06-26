# Platform compatibility

These skills target three agent platforms, all consuming **project-scoped**
skills installed with `gh skill install ... --scope project`.

| Platform | `--agent` target | Project-scope location (typical) |
| --- | --- | --- |
| Cursor | `cursor` | `.cursor/` in the consuming repo |
| Claude Code | `claude-code` | `.claude/skills/` in the consuming repo |
| VS Code / GitHub Copilot | `github-copilot` | `.github/` in the consuming repo |

> VS Code is represented by the GitHub Copilot skill target; there is no
> separate VS Code target.

Installation is driven entirely by `consumer-bootstrap/scripts/install-agent-skills.sh`,
which loops over these three targets for every skill in the hard-coded list.

## Runtime requirements

A skill only needs the runtime for the scripts it actually bundles. A skill with
no scripts needs nothing beyond the agent itself.

| Need | When required | How skills invoke it |
| --- | --- | --- |
| `gh` (authenticated) | Always, to install and update skills | `gh skill install` / `gh skill update --all` |
| `uv` + Python 3.12+ | Skills with bundled Python scripts | `uv run --script scripts/<script>.py` |
| Node.js 18+ | Skills with bundled JavaScript scripts | `node scripts/<script>.mjs` |
| bun | Only skills shipping their own `package.json` | `bun install --frozen-lockfile` then `bun run <script>` |

`consumer-bootstrap/scripts/setup-agent-skills-runtime.sh` installs/verifies
these once; `check-agent-skills-runtime.sh` verifies them at session start.
Missing Node or bun is only a warning — they are needed solely by skills that
bundle the corresponding scripts.

## Portability rules that keep skills cross-platform

Skill scripts are self-contained so they behave identically across all three
platforms and in CI. They must not depend on Nx, the repo-root manifests or
lockfiles, asdf, direnv, or imports from this repo's source tree. See
[`authoring-skills.md`](./authoring-skills.md) for the full rule.

## Explicitly out of scope

This repo does not support or include: MCP servers, LangChain runtime code, n8n
workflows, ChatGPT deployment automation, a custom skill package manager, or
runtime dependence on Nx, asdf, or direnv.
