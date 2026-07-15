# Codex exec adapter

This note specifies the Codex-specific portion of
[`HARNESS-RELIABILITY.md`](HARNESS-RELIABILITY.md). Queue transitions, retry policy, attempt
persistence, monitoring thresholds, and canonical role schemas belong to the harness-neutral
design.

## Supported surface

Start with `codex exec`, not app-server. The installed CLI exposes the lifecycle evidence and
structured final output required for the first implementation with a smaller integration
surface. The adapter boundary permits a later SDK or app-server implementation without
changing reconciliation.

Required invocation features are:

- `--json` for stdout JSONL events;
- `--output-schema` for the role result contract;
- `--output-last-message` for an attempt-local provider output;
- `--ignore-user-config` plus explicit run configuration, which isolates only
  `$CODEX_HOME/config.toml` and must not be treated as complete user-state isolation;
- `--strict-config` to reject unsupported configured fields;
- explicit model, cwd, sandbox, approval, and color settings.

The execution envelope preserves the operator's ordinary `HOME` so Git, package managers,
project tools, and their credentials continue to work. It sets `CODEX_HOME` to an
Orchestra-specific writable directory containing minimal generated automation configuration,
session state, and harness-owned authentication. Personal `$HOME/.agents` is masked by
Bubblewrap inside the transient systemd service so user skills cannot be discovered through
the preserved home. The service owns process lifetime only. Systemd path sandbox properties
are not combined with Bubblewrap: some hosts do not enforce them, and on others their mount
namespace prevents Bubblewrap from creating the enforcing namespace.

Authentication is established separately in the dedicated `CODEX_HOME`; Orchestra never copies,
symlinks, logs, or places `auth.json` in attempt artifacts. Setup and doctor commands report the
state directory, executable version, authentication readiness, and instruction-file drift
without printing credentials.

Instruction transport uses `native_project`: Codex discovers repository `AGENTS.md` files once,
while Orchestra records the resolved sources and hashes without appending them to stdin. A
minimal automation-level `AGENTS.md` may live in the dedicated `CODEX_HOME`. Codex does not use
`explicit_bundle` unless a supported CLI surface can disable all native instruction discovery;
`--ignore-user-config` does not provide that guarantee.

Delegation is role-owned and defaults to `disabled`. The adapter passes an explicit disabled
`multi_agent` feature state for that policy, passes no override for `allowed`, and enables it
only for `required`. Harness `extra_args` cannot override the role policy.

## Stream ownership

Stdout is protocol-only JSONL. Stderr is diagnostic text. They must be captured separately.
Every nonblank stdout line must decode as one JSON object. A malformed line is retained and
produces `protocol_failure`; it is never repaired by concatenating stderr or scraping prose.

Minimum lifecycle mapping:

- `thread.started` -> `session_started`, retaining `thread_id`;
- `turn.started` -> `turn_started`;
- command `item.started` -> `tool_started`;
- command `item.completed` -> `tool_completed`;
- agent-message `item.completed` -> `agent_message`;
- `turn.completed` -> `turn_completed`;
- `turn.failed` or `error` -> structured failure evidence.

Unknown item and event types are stored and ignored unless a required lifecycle event is
missing.

## Completion

A successful terminal attempt requires:

1. process exit zero;
2. one `thread.started` event;
3. a terminal `turn.completed` event after the final `turn.started`;
4. an output-last-message file containing JSON that validates against the role schema;
5. no structured error evidence that contradicts completion.

The adapter validates the final JSON again and returns it to Orchestra. It does not write the
canonical result or decide the queue transition.

## Long-running commands

An active command item keeps the attempt active even when no new events arrive. Orchestra does
not interpret tool-yield wording inside an agent message and does not start a recovery process
while the original Codex process lives. Command polling remains prompt-level defense in depth
until real fixtures establish whether the Codex event stream exposes intermediate yield state.

Bubblewrap startup failure remains an execution-boundary error. Inner Codex sandbox bypass is
permitted only when the supervisor proves Orchestra's outer Bubblewrap boundary is active.

## Resume

Use `codex exec resume <thread_id>` only after a terminal recoverable attempt. A resumed run is
a new Orchestra attempt with a parent attempt ID, the same worktree, and the same canonical
result schema. It never deletes partial changes or creates a fresh worktree.

Before resuming, verify that the configured Codex version accepts the required launch settings
on the resume subcommand. Do not assume every initial-run flag is valid after `resume`.

## Preflight and compatibility

Preflight records `codex --version`, verifies required flags from the installed CLI, and runs a
bounded protocol canary for newly encountered supported versions. Version minimums are a
configuration guard, not a substitute for capability checks.

The initial real fixture was captured with `codex-cli 0.144.4`. It produced:

- `thread.started`;
- `turn.started`;
- an agent-message `item.completed` containing schema-conforming JSON;
- `turn.completed` with usage;
- the same schema-conforming JSON in the output-last-message file.

The sanitized fixture lives at
`tests/fixtures/harness_protocols/codex-success.jsonl`. Startup diagnostics appeared only on
stderr, confirming why the supervisor must keep streams separate.

## Codex canaries

Before rollout, exercise the real dispatch and reconcile path for:

1. schema-conforming success with a commit;
2. a command that runs beyond the foreground tool wait and eventually completes;
3. a long quiet command;
4. command failure accurately represented in structured events and the final result;
5. malformed or absent final output;
6. process termination before terminal turn completion;
7. authentication, quota, and upstream failure fixtures;
8. same-thread resume with preserved worktree changes;
9. retry exhaustion;
10. `needs_human` with no retry.
11. the exact transient-service envelope hides a matching personal instruction/skill sentinel
    while the project sentinel is obeyed;
12. delegation disabled exposes no collaboration events or tools;
13. the attempt manifest records `native_project`, ordered instruction-source hashes,
    delegation policy, execution-envelope fingerprint, and effective transmitted-prompt hash.

The sentinel checks use launch, filesystem, init, and event evidence; a model statement that it
did not load personal state is not proof. After the adapter passes these canaries through the
same service path used by the scheduler, rerun a bounded real issue. The prompt instruction to
poll yielded sessions remains defense in depth, not the control plane. Large multi-phase work
such as the original `ai-due-diligence#042` is split into independently verifiable issues; this
protocol does not add a checkpoint outcome.
