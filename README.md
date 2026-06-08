# dero-skills

Canonical, private repository for organization **Agent Skills** and the reusable
setup automation that consuming application repos copy in.

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
scripts/                       # repo-wide helpers (validate-skills.sh)
consumer-bootstrap/            # files application repos copy in
docs/                          # authoring, consuming, and platform docs
```

## For skill maintainers

```bash
# one-time
curl -LsSf https://astral.sh/uv/install.sh | sh

# validate + smoke + test every skill
./scripts/validate-skills.sh
uv run pytest

# Nx (dev/CI), via pnpm
pnpm install --frozen-lockfile
pnpm nx run-many -t validate smoke test
```

See [`docs/authoring-skills.md`](./docs/authoring-skills.md).

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
