# Shape: Library / package / service

Language-agnostic principles for an importable library, distributable package,
or long-running service. Pair with the implementation language
(`languages/python.md`, `languages/rust.md`, `languages/typescript.md`) and
`ci.md`.

- **Stable public surface.** Treat the public API as a contract. Keep it small
  and intentional; document what is public vs internal.
- **Versioning.** Follow semver; document compatibility expectations and
  breaking-change policy. Avoid pinning consumers unnecessarily.
- **Boundary validation.** Validate inputs at the public surface and at IO
  boundaries; don't assume callers pass well-formed data.
- **Packaging and release.** If the repo produces installable artifacts, include
  packaging + release automation. Publish checksums and sign artifacts where
  appropriate (see `ci.md` and the language reference for the mechanics). Have CI
  install the package via the recommended end-user method and smoke-test it, so
  the documented install path is continuously proven, not just the dev bootstrap.
- **Testing.** Cover the public API's success and failure paths. For a service,
  add e2e tests that drive it over its real interface (HTTP, queue, etc.), not
  just in-process unit tests.
- **Docs.** Provide usage docs and a changelog written for consumers.

## Verification

- [ ] **Stable public surface.** The public API is treated as a contract, kept
  small and intentional, with public vs internal documented.
- [ ] **Versioning policy.** Semver is followed and compatibility / breaking-change
  expectations are documented; consumers are not pinned unnecessarily.
- [ ] **Boundary validation.** Inputs are validated at the public surface and at
  IO boundaries — callers are not assumed to pass well-formed data.
- [ ] **Tests cover success and failure.** The public API's success and failure
  paths are covered; a service has e2e tests driving it over its real interface
  (HTTP, queue, ...), not just in-process unit tests.
- [ ] **Install path proven (if installable).** If the repo ships an installable
  package, CI installs it via the recommended end-user method and smoke-tests it.
- [ ] **Consumer docs.** Usage docs and a changelog written for consumers exist.
