# Harness protocol fixtures

These fixtures are minimized, sanitized records captured from real installed harness CLIs.
They preserve fields that affect lifecycle normalization while replacing session IDs, event
IDs, paths, timing jitter, and token counts. Adapter tests must accept additional unknown
fields because harness event contracts can grow without changing their required lifecycle.

## Codex success

- Captured with `codex-cli 0.144.4` on 2026-07-14.
- Invocation used `codex exec --json --ignore-user-config --strict-config`, an explicit model,
  `--output-schema`, and `--output-last-message` in a disposable Git repository.
- Stdout contained four valid JSONL events; stderr contained only startup diagnostics.
- The final-message file contained JSON satisfying the requested schema.

## Claude authentication failure

- Captured with Claude Code `2.1.203` on 2026-07-14.
- Invocation used `claude -p --output-format stream-json --verbose`, an explicit model,
  `--tools ''`, `--setting-sources project,local`, `--strict-mcp-config`, and `--json-schema`
  in the same disposable repository.
- The installed CLI reported a logged-in Claude subscription, but the request received HTTP
  401. A successful Claude protocol canary therefore remains a rollout prerequisite.
- The process exited zero and its terminal event had `subtype: success`, but also had
  `is_error: true`, `api_error_status: 401`, and prior `authentication_failed` retry events.
  Normalization must use the complete structured evidence rather than one optimistic field or
  the process return code.

Raw captures remain outside the repository because they contain volatile machine paths and
session identifiers. Never commit credentials, raw authentication state, or unsanitized logs.
