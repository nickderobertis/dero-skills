# The onebudgetspec budgets (references/tools/onebudgetspec.md), which `check`
# depends on at its tier. Each budget domain's project checks its own
# `budgets.yaml` through its `budgets` target (deterministic, cached) and its
# `budgets-host` target (elapsed or host-reading, never cached); the root
# `budgets.yaml` holds what every change must stay within, so it is checked on
# every run.
budgets tier="affected":
    {{ if tier == "all" { "bunx nx run-many" } else if tier == "affected" { "bunx nx affected --base=" + base } else { error("unknown tier '" + tier + "' — use 'affected' (the default) or 'all'") } }} -t budgets budgets-host
    [ ! -f budgets.yaml ] || bunx onebudgetspec check budgets.yaml
