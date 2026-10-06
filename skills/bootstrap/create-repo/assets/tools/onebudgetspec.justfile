# The onebudgetspec budgets `check` runs at its tier: every domain's budget
# targets, then the root `budgets.yaml` (references/tools/onebudgetspec.md).
budgets tier="affected":
    {{ if tier == "all" { "bunx nx run-many" } else if tier == "affected" { "bunx nx affected --base=" + base } else { error("unknown tier '" + tier + "' — use 'affected' (the default) or 'all'") } }} -t budgets budgets-host
    [ ! -f budgets.yaml ] || bunx onebudgetspec check budgets.yaml
