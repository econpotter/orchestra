# Claude print adapter

This note specifies the Claude-specific portion of
[`HARNESS-RELIABILITY.md`](HARNESS-RELIABILITY.md). The first implementation uses Claude
Code's non-interactive print mode.

## Supported surface

Required invocation features are:

- `-p` for non-interactive execution;
- `--output-format stream-json --verbose` for stdout JSONL;
- `--json-schema` for the role result contract;
- explicit model, tools, permission mode, and MCP configuration;
- `--resume <session_id>` for bounded recovery.

Use `--setting-sources project,local` only as transitional containment. The durable
`project_only` policy in the harness contract resolves and fingerprints project instructions,
then disables ambient customizations. `--bare` is not an automatic answer: it skips
`CLAUDE.md` discovery and keychain/OAuth reads, so it requires explicit instruction and
authentication provisioning.

## Stream ownership

Stdout is JSONL protocol data. Stderr is diagnostics. The adapter maps at least:

- `system/init` -> `session_started`, retaining session ID, version, model, tools, plugins,
  plugin errors, and advertised capabilities;
- `system/api_retry` -> `provider_retrying` with the structured error category;
- tool lifecycle events -> normalized tool events;
- assistant messages -> `agent_message`;
- terminal `result` -> completion or failure after evaluating every error field.

Never treat `subtype: success` or process exit zero as sufficient success. The real fixture at
`tests/fixtures/harness_protocols/claude-authentication-failure.jsonl` exited zero and emitted a
terminal success subtype while also reporting `is_error: true`, HTTP 401, and structured
`authentication_failed` retries.

## Completion

A successful terminal attempt requires:

1. process exit zero;
2. one usable `system/init` event;
3. a terminal result with `is_error: false`;
4. no structured API, authentication, hook, or protocol failure that invalidates the run;
5. a `structured_output` object validating against the role schema.

Settings or plugin load errors are structured evidence. Configuration declares whether an
optional customization may fail or whether preflight must reject the attempt.

## Resume

Use `--resume <session_id>` only after a terminal recoverable attempt. The resumed process is a
new Orchestra attempt linked to the original, using the same worktree and role schema. Verify
the installed CLI's resume flag compatibility during preflight.

## Preflight and compatibility

Preflight records `claude --version`, validates required flags, and inspects `system/init` for
the actual model, tool set, MCP servers, plugins, plugin errors, and capabilities. Prefer
advertised capabilities over brittle version comparisons when available.

The initial capture used Claude Code `2.1.203`. `claude auth status` reported a logged-in Pro
subscription, but the canary returned HTTP 401 after structured retries. That failure is a real
adapter fixture and a fail-loud rollout blocker, not a reason to fabricate success data.

## Claude canaries

Before rollout, exercise the same cases as the Codex adapter, plus:

1. deterministic instruction loading without ambient user hooks or plugins;
2. hook or plugin initialization failure;
3. structured `system/api_retry` classification;
4. a terminal optimistic label contradicted by `is_error`;
5. a successful schema-conforming result after authentication is repaired.

Claude support is not complete until the successful canary traverses real dispatch and
reconciliation.
