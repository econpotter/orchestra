# Coding standards

Read and apply `~/.config/agents/references/coding-standards.md`.

- Reconciliation is the sole automated lifecycle writer of queue files; explicit operator
  commands perform intentional human-directed transitions.
- Harness adapters normalize evidence; they do not choose queue transitions or retry policy.
- Keep the engine independent of any one user's workspace paths, project names, models, or
  private prompts. Domain values belong in configuration or registries.
- Treat engine source and workspace state as separate roots. Never require a workspace to be
  the engine checkout, and never store operational queue or runtime state in this repository.
- Preserve raw harness evidence separately from normalized outcomes; failures must remain
  explicit and actionable.
