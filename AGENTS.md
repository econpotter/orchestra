Stage: development

## What this repo is

Orchestra is the public, harness-neutral engine and CLI for scheduling validated development
issues across multiple Git repositories. This repository owns reusable orchestration code,
packaged prompts, lifecycle protocol, tests, and scheduler units. It does not own a user's
workspace configuration, project registry, queues, runtime state, private notes, or project
checkouts.

## Layout

- `src/orchestra/` — installable engine and CLI package.
- `tests/` — unit, integration, fixture, and fake-harness coverage.
- `protocol/` — lifecycle, operations, issue-writing, and harness reliability contracts.
- `prompts/` — default role prompts packaged with the distribution.
- `tools/` — thin executable wrappers around engine operations.
- `systemd/` — user service and timer for host scheduling.
- `config.example.yaml` — public workspace configuration example.

## Commands

- Setup: `uv sync`
- Test: `uv run pytest`
- Lint: `uv run ruff check src tests`
- Type check: `uv run mypy src`
- Run from checkout: `uv run orchestra --help`
- Install this checkout: `uv tool install --force --editable "$PWD"`

## Hard constraints

- Reconciliation is the sole automated lifecycle writer of queue files; explicit operator
  commands perform intentional human-directed transitions.
- Harness adapters normalize evidence; they do not choose queue transitions or retry policy.
- Keep the engine independent of any one user's workspace paths, project names, models, or
  private prompts. Domain values belong in configuration or registries.
- Treat engine source and workspace state as separate roots. Never require a workspace to be
  the engine checkout, and never store operational queue or runtime state in this repository.
- Preserve raw harness evidence separately from normalized outcomes; failures must remain
  explicit and actionable.
- Claude and Codex are supported harness targets. Pi remains a protocol sketch until a later
  feature implements and verifies `PiJsonAdapter`.

## First reads

1. `README.md`
2. `protocol/STATES.md`
3. `protocol/OPERATIONS.md`
4. `protocol/HARNESS-RELIABILITY.md`

If working with orchestra, run `orchestra guide`. Otherwise ignore it — normal coding needs
nothing from orchestra.
