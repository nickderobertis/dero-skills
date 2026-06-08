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
  appropriate (see `ci.md` and the language reference for the mechanics).
- **Testing.** Cover the public API's success and failure paths. For a service,
  add e2e tests that drive it over its real interface (HTTP, queue, etc.), not
  just in-process unit tests.
- **Docs.** Provide usage docs and a changelog written for consumers.
