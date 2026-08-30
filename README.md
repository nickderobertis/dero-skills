# dero-skills

Canonical repository for organization **Agent Skills** and the reusable setup
automation that consuming application repos copy in.

This repo owns:

1. The Agent Skill folders (`skills/`).
2. Shared validation tooling for skills (`tools/`, `scripts/`).
3. Reusable bootstrap templates for consuming repos (`consumer-bootstrap/`).
4. Documentation for supported agent platforms (`docs/`).

Supported consuming platforms: **Cursor**, **Claude Code**, and
**VS Code / GitHub Copilot**.

## Layout

```text
skills/<scope>/<skill-name>/   # self-contained Agent Skills
tools/                         # stdlib-only validation + smoke tooling
scripts/                       # repo-wide dev-environment helpers
consumer-bootstrap/            # files application repos copy in
docs/                          # authoring, consuming, and platform docs
```

## For skill maintainers

This repo uses a `just` command surface and dogfoods its own
`bootstrap/create-repo` skill (`just check` audits the repo against the skill's
baseline invariants). The repo is an Nx project graph and every recipe below
delegates to it, so all three of [uv](https://astral.sh/uv), Node, and
[bun](https://bun.sh) are prerequisites.

```bash
just bootstrap   # one-time: bun install + uv sync + the uv-installed gate binaries
just check       # the gate, affected tier: format, lint, validation, tests, baseline
just check all   # the same gate as one full sweep over every project
just validate    # the validate + smoke targets
just test        # every affected project's pytest
```

See [`docs/authoring-skills.md`](./docs/authoring-skills.md) and
[`AGENTS.md`](./AGENTS.md).

## For consuming repos

Copy the bootstrap package and install the project skill set:

```bash
tmp_dir="$(mktemp -d)"
gh repo clone nickderobertis/dero-skills "$tmp_dir" -- --depth 1
rsync -av "$tmp_dir/consumer-bootstrap/" ./
rm -rf "$tmp_dir"
```

See [`docs/consuming-repos.md`](./docs/consuming-repos.md) and
[`docs/platform-compatibility.md`](./docs/platform-compatibility.md).

## Out of scope

No MCP servers, LangChain runtime code, n8n workflows, ChatGPT deployment
automation, custom skill package manager, or runtime dependence on Nx, asdf, or
direnv.
