## Agent Skills

This repo uses project-scoped organization Agent Skills installed from the canonical skills repo.

At the beginning of each new agent session, before substantive work:

1. Check runtime tools:

   ```bash
   ./scripts/check-agent-skills-runtime.sh
   ```

2. Update installed skills:

   ```bash
   ./scripts/update-agent-skills.sh
   ```

3. If skills are not installed yet, install the project skill set:

   ```bash
   ./scripts/install-agent-skills.sh
   ```

If the update fails because `gh` is unavailable, authentication is missing, or the network is unavailable, report the failure briefly and continue with currently installed skills if present.

Do not manually copy skill contents into this repo.

Do not pin skill versions unless explicitly requested.

Do not run installed skill scripts through Nx. Nx belongs to the canonical skills repo authoring workflow, not runtime use in this repo.

Python skill scripts should be run as documented by the skill, usually:

```bash
uv run --script scripts/<script>.py [args...]
```

JavaScript skill scripts should be run as documented by the skill, usually:

```bash
node scripts/<script>.mjs [args...]
```

Skills with local JavaScript dependencies may require:

```bash
pnpm install --frozen-lockfile
pnpm run <script-name> -- [args...]
```
