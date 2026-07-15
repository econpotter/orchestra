# Reliable harness execution

## Purpose

Orchestra must run unattended coding workers reliably through interchangeable agent
harnesses. Codex and Claude are implemented adapter targets for the first implementation, but
an installation is not rollout-supported until its exact envelope passes the real canaries. Pi
is represented in the contract so the core does not accidentally require Codex- or
Claude-specific behavior, but a real Pi adapter is a later feature.

A run must either complete with mutually consistent evidence or stop with an explicit,
actionable failure. Queue decisions must never depend on scraping human prose when structured
evidence exists, and models must not write Orchestra control-plane files themselves.

## Architecture decision

Status quo: provider configuration supplies an argument vector and prompt transport;
Orchestra treats the process as opaque, combines stdout and stderr, and expects the agent to
write a result file.

From-scratch design: harness-specific adapters feed one attempt supervisor, append-only
attempt store, normalized outcome contract, and provider-independent reconciler.

Use the from-scratch design. This repository is in development, so preserving the opaque
provider interface is not a reason to retain it. Rename the current `providers` concept to
`harnesses`: Codex, Claude, and Pi are agent harnesses, while each harness may itself use a
model provider.

## Invariants

1. Reconciliation is the sole queue writer.
2. Harness adapters never choose queue transitions or retry policy.
3. Raw stdout events, raw stderr diagnostics, the process result, the branch delta, and the
   normalized terminal outcome are distinct evidence sources.
4. Stdout and stderr are never combined for a structured harness.
5. Every dispatch creates its durable attempt manifest before process launch.
6. Attempt artifacts are append-only except for atomic manifest state replacement.
7. A model cannot declare infrastructure failure retryable. Orchestra derives retry policy
   from structured evidence and configuration.
8. A new commit is implementation evidence, not sufficient evidence of successful completion.
9. A quiet active command is not a stall, and a yielded command is not a termination.
10. Unknown optional event fields and event types are retained and ignored. Missing required
    lifecycle evidence fails loudly.
11. A verifier `reject` is an ordinary review outcome, not an infrastructure failure.
12. Instruction isolation is established from the execution envelope and observed harness
    evidence, never from a model's claim about what it loaded.

## Components

```text
CodexExecAdapter ---+
                    |
ClaudePrintAdapter -+-> AttemptSupervisor -> AttemptStore -> Reconciler -> Queue
                    |          |
PiJsonAdapter sketch+          +-> raw stdout, raw stderr, normalized snapshot,
                                   process completion, canonical role result
```

### HarnessAdapter

Each adapter owns only harness-specific behavior:

- declare capabilities;
- preflight the executable, version, configuration, and protocol;
- construct launch, resume, and cancel operations;
- decode stdout events without reading stderr as protocol data;
- project raw events into normalized lifecycle evidence;
- extract and validate the harness's structured final response.

The interface must support a fake adapter so core behavior can be tested without a paid model.

### AttemptSupervisor

The supervisor owns operating-system process behavior:

- start one process group per attempt;
- capture stdout and stderr into separate files without pipe backpressure;
- incrementally append raw events and update a normalized observation snapshot;
- record PID plus process start time, exit code or signal, and completion time;
- terminate the complete process group on explicit cancellation;
- atomically finalize the attempt manifest even after malformed output or wrapper failure.

The current `worker_process` wrapper evolves into this supervisor. It must never infer queue
state.

### AttemptStore

Each attempt receives an immutable ID and directory. The durable manifest records at least:

- schema version and attempt ID;
- project, issue, role, harness, model, and harness version;
- adapter version and declared capabilities;
- parent attempt and session ID when resumed;
- base prompt, effective transmitted prompt, instruction-bundle, configuration, and
  result-schema fingerprints;
- instruction policy, ordered source paths and per-source fingerprints, delegation policy,
  and execution-envelope fingerprint;
- worktree, branch, start commit, and observed terminal commit;
- process ID, process start time, exit status or signal, and timestamps;
- raw stdout event path, raw stderr path, provider-output path, and canonical-result path;
- latest normalized event, active tool, and terminal normalized outcome.

The worker registry points to active attempt IDs. It is not the durable attempt history.

### Reconciler

Reconciliation consumes only finalized attempt manifests plus Git evidence. It validates the
role result, applies the commit/result truth table, derives recovery policy, writes the queue,
and retains all contradictory evidence.

## Capability contract

Capabilities are data, not executable-name checks. Harness protocol fields include:

- `structured_events`;
- `native_result_schema`;
- `durable_session`;
- `resume_session`;
- `active_tool_events`;
- `token_usage`;
- `graceful_cancel`.

Isolation is not one boolean. Adapters declare each boundary independently:

- `isolates_user_config`;
- `isolates_user_instructions`;
- `isolates_user_skills`;
- `isolates_user_integrations`;
- `isolates_session_state`;
- `supports_dedicated_auth_home`.

For example, ignoring a harness configuration file does not prove that global instructions,
skills, plugins, MCP servers, or sessions are absent. An adapter must not advertise a broader
capability than its launch envelope and a real sentinel canary demonstrate.
Configured boundaries and verified capabilities are recorded separately. Isolation capabilities
remain false until the exact installed-version transient-service sentinels have passed and the
operator explicitly records those verified capability names in harness environment config.

A role may declare required capabilities. Configuration validation rejects an incompatible
harness before dispatch.

Initial profiles:

| Harness | Events | Native schema | Resume | Initial implementation |
|---|---:|---:|---:|---:|
| Codex exec | yes | yes | yes | yes |
| Claude print | yes | yes | yes | yes |
| Pi JSON/RPC | yes | no documented CLI schema | yes | no; design sketch only |

The future `PiJsonAdapter` will consume `pi --mode json` lifecycle and tool events, use a
durable session ID/file for resume, and validate final assistant JSON in Orchestra unless a
narrow result extension is adopted. No Pi executable code, dependency, live fixture, or
rollout gate belongs in the current feature.

## Normalized lifecycle

Raw harness events project into a deliberately small vocabulary:

- `session_started`;
- `turn_started`;
- `tool_started`;
- `tool_progress`;
- `tool_completed`;
- `provider_retrying`;
- `agent_message`;
- `turn_completed`;
- `turn_failed`;
- `protocol_error`.

Every normalized event includes the raw-event offset, observed timestamp, harness-native type,
and structured details relevant to classification. Raw events remain authoritative evidence;
the normalized stream is the stable contract used by monitoring and reconciliation.

A process exit without a terminal turn is `protocol_failure`. A malformed final JSONL record
after abrupt process death is retained as a truncated tail and classified explicitly; it does
not make earlier valid events disappear.

## Orchestra-owned role results

Worker, validator, and verifier schemas are versioned separately. Shared fields are:

- `schema_version`;
- `outcome`;
- `decisions`;
- `failure_category`;
- `evidence`;
- `requires_human`.

Role-specific outcome enums remain narrow:

- worker: `committed`, `blocked`;
- validator: `validated`, `blocked`;
- verifier: `accept`, `reject`, `blocked`.

All fields are present in every result. Only `blocked` may use a non-empty
`failure_category`, and every `blocked` result must use a stable category. `committed`,
`validated`, `accept`, and `reject` leave it empty. In particular, verifier `reject` carries
actionable review findings in `decisions` and `evidence`; it transitions to rework without
protocol recovery or another verifier attempt. Cross-field rules are enforced by Orchestra's
semantic parser rather than advanced conditional JSON Schema features that are not portable
across harness-native schema implementations.

Codex and Claude receive the role schema through their native structured-output interfaces.
Their adapters validate the returned object again. The future Pi adapter may validate a final
assistant JSON object or expose an Orchestra result tool; this difference does not change the
canonical role schema.

The adapter writes provider output to an attempt-local path. Orchestra validates it and
atomically writes the canonical result. Prompts no longer instruct agents to create result
files with shell commands.

## Failure taxonomy and recovery policy

Failure category describes what happened. Retry disposition is a separate Orchestra-derived
decision.

Stable categories are:

- `authentication_failure`;
- `quota_failure`;
- `upstream_failure`;
- `harness_failure`;
- `protocol_failure`;
- `tool_observation_failure`;
- `environment_failure`;
- `acceptance_failure`;
- `needs_human`;
- `cancelled`;
- `time_limit`.

A command returning nonzero is ordinary agent evidence, not automatically a
`tool_observation_failure`. That category means Orchestra cannot reliably establish tool
state—for example, a tool started but neither completed nor remained observable.

Retry disposition is one of `never`, `resume`, `fresh_attempt`, or `human`. Configuration maps
structured categories and evidence to bounded policy. `needs_human`, `acceptance_failure`, and
intentional cancellation never retry automatically.

## Commit and result truth tables

### Worker

| New commit | Valid result | Meaning |
|---:|---|---|
| yes | `committed` | advance to `committed` |
| yes | `blocked` | retain partial commit; block with contradiction evidence |
| yes | absent/invalid | indeterminate; bounded finalization resume if safe, otherwise block |
| no | `committed` | contract failure; never claim success |
| no | `blocked` | apply failure and recovery policy |
| no | absent/invalid | classify from terminal attempt evidence; never scrape prose |

### Validator and verifier

These roles must not create a commit. A branch change is a contract violation. A valid role
result drives the existing queue transition; missing, malformed, or contradictory results use
the same structured failure and recovery policy as workers.

## Bounded resume

Resume is allowed only when:

- the original process is terminal;
- the attempt has a durable session identifier;
- the adapter declares resume support;
- structured evidence maps to `resume`;
- the configured attempt limit is not exhausted.

A running command is never "recovered" by launching a second process. The supervisor continues
observing the original attempt. A resume reuses the same worktree, records a parent attempt,
preserves partial changes, and tells the harness to inspect current state before acting.

Resume is recovery from a terminal harness interruption, not an unbounded cognitive
checkpoint protocol. There is no `checkpoint` role outcome or queue state. Oversized work is
split into dependency-linked, independently verifiable issues; a checkpoint feature may be
reconsidered only after evidence shows well-sized issues still exhaust context.

When `hold_network_issues` is enabled, unapproved network issues stop before validation.
Explicit release records durable approval; later worker recovery and verifier rework do not
reapply this one-time gate.

## Health monitoring

Monitoring combines process state with normalized events. It records the active tool and its
elapsed time rather than relying on log modification time.

Limits are separate configuration values:

- total attempt wall time;
- idle time while no tool is active;
- active-tool time;
- graceful cancellation period.

Each may be disabled during development calibration, but unattended production configuration
must declare a wall limit explicitly. When a threshold fires, the supervisor records its name,
configured value, last event, active tool, and elapsed time before cancellation.

## Deterministic execution envelope and instructions

Each adapter produces an explicit execution envelope containing environment additions and
removals, harness state directory, filesystem masks and read-only paths, native instruction
policy, integration policy, and delegation policy. The envelope preserves ordinary `HOME` so
Git, package managers, project tools, and their credentials continue to work. Harness-specific
state is isolated separately; changing `HOME` is not the default isolation mechanism.

Orchestra always resolves repository-owned instructions within the project/worktree boundary,
records their deterministic order and per-source fingerprints, and retains the bundle as audit
evidence. Transport is adapter-aware:

- `native_project`: the harness discovers project instructions natively. Orchestra does not
  append the captured bundle to the prompt.
- `explicit_bundle`: native instruction discovery is disabled and Orchestra supplies the
  captured bundle exactly once.

An adapter rejects a policy it cannot actually enforce. `ambient` may exist only as a visibly
non-reproducible compatibility mode; it is never mislabeled isolated. A native structured
result schema is passed through the harness interface and is not dumped into the user prompt.
Only concise terminal-result semantics are generated into the role prompt.

For Codex, the target envelope preserves `HOME`, uses a dedicated writable `CODEX_HOME`, and
masks personal `$HOME/.agents` discovery inside the outer service boundary. Authentication is
established in that dedicated harness home by an operator-facing setup flow and is never copied
into attempt artifacts. Claude uses a dedicated writable `CLAUDE_CONFIG_DIR`, masks personal
`$HOME/.claude`, disables slash skills and the delegation tool, and receives explicit bundled
instructions under safe mode. Its authentication is established separately in that directory.
Equivalent capabilities are not inferred merely because the adapter interface is shared.

Harness configuration explicitly controls model, reasoning effort, cwd, tool set, MCP servers,
sandbox/permission mode, color, event format, environment allowlist, instruction policy, and
delegation. Role-owned delegation policies are `disabled`, `allowed`, and `required`.
Unattended roles default to `disabled`; adapters translate that policy into a deterministic
harness feature/tool state. Raw extra arguments may not contradict the role policy.

Outer and inner sandbox ownership is explicit. A transient user systemd service owns process
lifetime only; Bubblewrap inside that service enforces the read-only root, explicit writable
paths, read-only seeds, and masked personal harness state. Systemd path sandbox properties are
not combined with Bubblewrap: some hosts do not enforce them, and on others their mount
namespace prevents Bubblewrap from creating the enforcing namespace. A harness may bypass its
own sandbox only when Orchestra verifies that this external Bubblewrap boundary is active.
Missing or failed Bubblewrap is an execution-boundary error, not a model or command-yield
failure. Configured tool-cache paths are private writable tmpfs mounts, not writable views of
the operator's cache. Network remains shared so the harness can reach its model API.

## Compatibility and protocol drift

Preflight records the executable path and version, then exercises required capabilities or
validates them against a supported protocol range. Version comparison alone is insufficient.

Adapters must:

- ignore and retain unknown optional events;
- reject missing required lifecycle fields;
- fail when native schema support is requested but unavailable;
- distinguish a nonzero process exit from a structured terminal error;
- reject optimistic terminal labels contradicted by structured error fields.

The captured Claude fixture demonstrates the last rule: the CLI exited zero and emitted
`subtype: success`, but `is_error: true`, HTTP 401, and prior authentication failures make the
normalized outcome an authentication failure.

## Verification

Development is TDD. Required layers are:

1. sanitized real-event fixtures for every implemented adapter;
2. parser and normalization tests, including unknown fields and malformed/truncated JSONL;
3. supervisor tests for separate streams, atomic completion, process groups, and cancellation;
4. truth-table tests covering every role and evidence combination;
5. recovery-limit tests with preserved worktree changes;
6. opt-in real Codex and Claude canaries through the transient service, dispatch, and
   reconciliation;
7. personal-instruction and personal-skill sentinel canaries that inspect launch and event
   evidence rather than trusting model self-report;
8. a delegation-disabled canary with no collaboration tools or events, plus a long foreground
   command canary proving shell continuation is not confused with subagent waiting.

Mocked parser tests are necessary but insufficient. Completion requires observing the real
installed harness path inside the same transient service envelope used in operation. A real
Claude worker and verifier completed through the transient service with native structured
output; the older HTTP 401 fixture remains valuable contradictory-evidence coverage. That
lifecycle canary preceded the dedicated `CLAUDE_CONFIG_DIR` envelope. A later init probe showed
personal plugins disappear under the dedicated directory, but authentication was absent there.
The current Claude isolation envelope therefore remains unverified.

## Delivery plan

### Increment 0: contract and evidence

- Land this contract and harness-specific adapter notes.
- Capture and sanitize real Codex success and Claude failure fixtures.
- Require a successful Claude fixture before Claude rollout, not before parser development.
- Keep Pi implementation explicitly deferred.

### Increment 1: supervisor and attempt ledger

- Introduce adapter interfaces and capability validation.
- Separate stdout and stderr.
- Persist attempt manifests and raw artifacts.
- Run beside the current reconciliation path in shadow mode.

### Increment 2: Codex and Claude adapters

- Implement structured event parsing and preflight for both harnesses.
- Add native role schemas and Orchestra-owned canonical results.
- Switch reconciliation to the explicit truth tables.

### Increment 3: monitoring and recovery

- Add event-aware limits and process-group cancellation.
- Add bounded same-session resume.
- Exercise failure and recovery through real canaries.

### Increment 4: execution-envelope reliability

- Replace coarse configuration isolation with decomposed demonstrated capabilities.
- Add adapter-aware `native_project` and `explicit_bundle` instruction delivery.
- Preserve `HOME`, isolate harness state, and mask personal discovery paths.
- Make delegation role-owned and disabled by default.
- Record effective prompt, instruction-source, delegation, and envelope fingerprints.
- Record the loaded Orchestra package fingerprint and expose comparison against the checkout
  that passed rollout gates.

### Increment 5: rollout

- Run one bounded issue through Codex and one through Claude.
- Split multi-phase workloads such as the original `ai-due-diligence#042` into bounded,
  dependency-linked issues before rerunning them.
- Remove prose-log failure classification only after structured adapters cover active roles.

### Later feature: Pi

- Validate the documented capability sketch against an installed Pi version.
- Capture real JSON/RPC fixtures.
- Decide between final-message validation and a result extension.
- Implement and canary `PiJsonAdapter` without changing queue semantics.

## Acceptance criteria

The feature is complete when:

- every Codex and Claude attempt is explainable from retained structured evidence;
- stdout protocol data cannot be corrupted by stderr diagnostics;
- canonical results are schema-validated and written by Orchestra;
- every commit/result combination has deterministic behavior;
- quiet commands are not false stalls and genuine hangs are bounded;
- recoverable terminal failures resume within strict limits;
- human and acceptance failures never retry automatically;
- incompatible harness capabilities fail before dispatch;
- real Codex and Claude dispatch-to-reconcile canaries pass.
- instruction and skill sentinels prove that personal automation state is absent;
- delegation-disabled and long-command service canaries pass;
- the installed runtime provenance matches the engine commit that passed those gates.
