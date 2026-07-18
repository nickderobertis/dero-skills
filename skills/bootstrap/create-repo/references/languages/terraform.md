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
  gate, and review a plan before applying it.
  - `just bootstrap` -> `terraform init`
  - `just check` -> `terraform fmt -check` + `terraform validate` + `tflint` +
    `tfsec` or `checkov`
  - `just upgrade` -> deliberately bump provider and module constraints, refresh
    the dependency lock file, then run the full gate and review the plan
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

## Verification

- [ ] **Native gate wired.** `just bootstrap` runs `terraform init`; `just check`
  runs `terraform fmt -check`, `terraform validate`, `tflint`, and `tfsec` or
  `checkov`; `just upgrade` deliberately updates provider/module versions and the
  lock file before re-running the gate and reviewing the plan.
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
