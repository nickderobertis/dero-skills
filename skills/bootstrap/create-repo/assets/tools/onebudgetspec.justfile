# The onebudgetspec budgets `check` runs at its tier: every domain's budget
# targets, then the root `budgets.yaml` (references/tools/onebudgetspec.md) —
# strictly, so it leaves out the `release-tail` budgets the release's `publish`
# job reports and the `host-variable` one `just gate` checks as a warning.
budgets tier="affected":
    {{ if tier == "all" { "bunx nx run-many" } else if tier == "affected" { "bunx nx affected --base=" + base } else { error("unknown tier '" + tier + "' — use 'affected' (the default) or 'all'") } }} -t budgets budgets-host
    [ ! -f budgets.yaml ] || bunx onebudgetspec check budgets.yaml --exclude-label release-tail --exclude-label host-variable
