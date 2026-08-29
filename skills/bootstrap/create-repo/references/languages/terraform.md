# Language: Terraform / Infrastructure as Code

Language-level conventions for repositories that contain infrastructure as code.
Combine with any product shape and `ci.md`: this applies equally to a standalone
infrastructure repo and an app repo with an `infra/` subtree. The principles are
tool-agnostic across Terraform/OpenTofu, Pulumi, and CloudFormation/CDK; commands
and examples use Terraform/OpenTofu as the concrete default.

- **Deterministic toolchain.** Run `terraform fmt -check`, `terraform validate`,
  `tflint`, and one security scanner such as `tfsec` or `checkov`. These own
  syntax, formatting, provider-aware lint, and machine-detectable security
  findings; do not duplicate their checks in reviewer guidance or llmlint.
- **Command mapping.** Keep backend initialization separate from the read-only
  gate, and review a plan before applying it. The root recipes delegate to the
  orchestrator, which runs the per-project targets named below.
  - `just bootstrap` -> `terraform init` in each root module (one project at a
    time, via the orchestrator — every root module resolves its own providers)
  - `just check` -> `nx affected -t format lint typecheck` plus the repo-level
    `coverage` target, with the per-project targets running `terraform
    fmt -check`, `terraform validate`, `tflint`, and `tfsec` or `checkov`
  - `just upgrade` -> deliberately bump provider and module constraints, refresh
    each root module's dependency lock file, then run the full gate and review
    the plan
- **State discipline.** Use a remote backend with locking, encryption, access
  controls, and recovery appropriate to the provider. Never commit state or
  `.terraform/`. Treat state as sensitive data and make ownership and recovery
  explicit rather than relying on a developer's machine.
- **Boundaries and isolation.** Give modules one cohesive responsibility and a
  small typed interface; do not hide unrelated resources behind a convenience
  module. Separate environments, accounts/subscriptions/projects, regions, and
  independently operated systems into state and deployment units sized to limit
  blast radius. Do not use one mutable workspace as the isolation boundary for
  materially different environments.
- **Secrets.** Reference secrets from a managed secret store at deployment time,
  keep plaintext out of code and inputs, and mark secret-bearing outputs/variables
  sensitive. Because `sensitive` only redacts presentation and state can still
  contain the value, prefer designs that do not place secret material in state.
- **Declarative and drift-resistant.** Model desired state with native resources;
  avoid imperative provisioners, `local-exec`, and `null_resource` escape hatches
  when a provider resource fits. Imports, lifecycle choices, and ignored changes
  must preserve reconciliation rather than conceal drift. Choose `for_each` with
  stable domain keys when list reordering would make `count` addresses churn.
- **Intent is legible.** Use meaningful, stable names and a consistent tagging or
  labeling strategy that expresses ownership, environment, service, and cost or
  compliance intent where applicable. Encode real data flow through references
  and outputs; use explicit dependency metadata only when the provider cannot
  infer the dependency, never timing or incidental ordering.
- **Least privilege.** IAM roles and resource policies grant only the actions,
  resources, principals, and conditions the workload needs. Broad grants require
  a concrete, documented constraint or migration reason; generated policy syntax
  passing a scanner is not evidence that its authority matches the workload.
- **Version intent.** Constrain providers and external modules to compatible,
  reviewed versions and commit the dependency lock file where the tool supports
  one. Choose constraint breadth deliberately: upgrades must be possible, but an
  unreviewed upstream release must not silently change infrastructure. Record why
  unusually broad, narrow, or source-ref constraints are appropriate.

## Projects and the graph (the root module is the unit)

Terraform has no workspace primitive that spans directories. Its unit of
dependency resolution is the **root module** — the directory you run `init` in —
and `terraform workspace` is a state selector, not a workspace in the uv/Cargo/bun
sense (and, per "Boundaries and isolation" above, never the isolation boundary
for materially different environments). So the mapping inverts one rule and
keeps the rest:

- **One project per root module, plus one per shared module.** Each deployment
  unit (an environment × region × independently-operated system) is a root module
  directory with a `project.json` in it beside its `*.tf`; each reusable module
  under `modules/` is a project of its own. Nothing sits at the repo root but the
  graph itself.
- **The lockfile is per root module by design — this is the exception, and it is
  Terraform's, not yours.** `.terraform.lock.hcl` records the provider versions
  `terraform init` resolved *for that configuration*, so a deployment unit
  legitimately commits its own. Do not try to hoist them into one file, and do
  not skip committing them. The one-lockfile-per-ecosystem rule still binds
  everything else in the repo: the tooling that lints, scans, and tests the
  infrastructure (tflint plugins, checkov, a Python or TypeScript test harness)
  resolves through one root lockfile for its own ecosystem — per
  `languages/python.md` or `languages/typescript.md`, whichever that harness is
  written in.
- **Shared-module edges are declared.** A stack consuming `source =
  "../../modules/vpc"` depends on that module's project; where the source path
  isn't inferable, name it in `implicitDependencies`, so editing a shared module
  marks every consuming stack affected and leaves the rest untouched. That edge
  is the whole point: without it, one module edit plans every stack in the repo.
- **Pulumi/CDK repos follow their language.** When the IaC is written in a
  general-purpose language, the project/workspace/lockfile mapping is that
  language's (`languages/typescript.md`, `languages/python.md`); this section's
  root-module rule applies to the stacks it deploys.

### Splitting the work

- **The offline tier is per project and needs no credentials.** A project's
  `format` target runs `terraform fmt -check`, `lint` runs `tflint` plus the
  security scanner (`tfsec`/`checkov`), and `typecheck` runs `terraform validate`
  — all against that project's directory only, all offline. This is the tier
  every change pays for, so it must stay credential-free.
- **Anything that reaches the cloud is its own project.** `terraform plan` needs
  real credentials and real state; an integration test (Terratest,
  `terraform test` with a real provider) actually applies and destroys
  infrastructure. Put each behind its own project — `<stack>-plan` (its `build`
  target produces the reviewed plan file) and `<stack>-e2e` (its `test` target
  runs the apply/destroy journey) — depending only on the stack it exercises, so
  an unrelated change never authenticates to a cloud account, and a change to one
  stack never plans another.
- **The coverage gate for HCL is reachability, and it is still a gate.** Line
  coverage does not apply to declarative configuration, so the equivalent
  threshold is that the gate reaches *every* deployment unit: no `*.tf` file
  belongs to no project. Enforce it mechanically — a `coverage` target that
  fails when a directory containing `*.tf` maps to no project, and that requires
  every project to have run `fmt`/`validate`/`tflint`/the scanner — so adding a
  stack without wiring it into the graph fails the build instead of silently
  going unchecked. Findings from the scanner fail the gate as they did before;
  splitting the repo into projects must not turn a full sweep into a partial one.
  Where the IaC is a general-purpose language, that language's real line-coverage
  gate applies to the harness on top of this.

### Target names

Each infrastructure project declares the repo-uniform target names, each calling
the IaC tool directly so `nx run-many -t lint` reaches it alongside a Python or
TypeScript project: `format` -> `terraform fmt -check`, `lint` -> `tflint` +
`tfsec`/`checkov`, `typecheck` -> `terraform validate`, `build` -> `terraform
plan -out=` on the plan projects, and `test` -> the integration journey on the
e2e projects. The repo-level `coverage` target is the one aggregate and carries
the same name in every language.

## Verification

- [ ] **Native gate wired.** `just bootstrap` runs `terraform init` per root
  module; `just check` delegates to the orchestrator so every affected project
  runs `terraform fmt -check`, `terraform validate`, `tflint`, and `tfsec` or
  `checkov`; `just upgrade` deliberately updates provider/module versions and the
  lock files before re-running the gate and reviewing the plan.
- [ ] **State protected.** State uses a remote, locked, encrypted, access-controlled
  backend with recovery and ownership defined; state files and `.terraform/` are
  ignored; state is treated as sensitive.
- [ ] **Boundaries isolate blast radius.** Modules are cohesive with small
  interfaces, and environments and independently operated systems have separate
  state/deployment units rather than one mutable workspace.
- [ ] **Secrets stay out of code and state.** Secrets come from a managed store,
  secret-bearing values are marked sensitive, and designs avoid persisting secret
  material in state.
- [ ] **Declarations reconcile safely.** Native resources replace imperative
  escape hatches where possible, lifecycle choices do not hide drift, and
  `for_each`/`count` choices preserve stable resource addresses.
- [ ] **Intent and dependencies are explicit.** Names and tags communicate
  ownership and environment intent; references/outputs encode real data flow and
  explicit dependencies are used only where inference is impossible.
- [ ] **Authority is least-privilege.** IAM and resource policies match the
  workload's required actions, resources, principals, and conditions; broad
  grants carry a concrete documented rationale.
- [ ] **Versions are deliberate.** Provider/module constraints and the dependency
  lock file prevent silent upstream changes while leaving a reviewed upgrade path;
  unusual constraints have a recorded reason.
- [ ] **Root modules are projects.** Every deployment unit and every reusable
  module is its own project with a `project.json` beside its `*.tf`; consuming
  stacks declare the shared-module edge (`source` path or
  `implicitDependencies`) so a module edit marks exactly its consumers affected.
- [ ] **Lockfiles match the tool's unit of resolution.** Each root module commits
  its own `.terraform.lock.hcl` (Terraform resolves providers per configuration —
  the documented exception), while any lint/scan/test harness written in another
  language keeps that ecosystem's single root lockfile.
- [ ] **Cloud-touching work split out.** The offline tier (`fmt -check`,
  `validate`, `tflint`, the scanner) runs per project without credentials, and
  `plan` and any apply/destroy integration test live in their own
  `<stack>-plan` / `<stack>-e2e` projects depending only on the stack they
  exercise.
- [ ] **The gate reaches every deployment unit.** A `coverage` target fails when
  a directory containing `*.tf` belongs to no project or a project skipped its
  checks, so the split cannot turn a full sweep into a partial one; scanner
  findings still fail the gate.
- [ ] **Uniform target names.** Each infrastructure project declares `format` /
  `lint` / `typecheck` (plus `build` on plan projects and `test` on integration
  projects) calling the IaC tool, so `nx affected` and `run-many` reach them by
  name in a polyglot repo.
