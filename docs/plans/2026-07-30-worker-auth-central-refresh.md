# Worker auth: central refresh of the shared harness token

Date: 2026-07-30
Status: implemented on this branch (feat/worker-auth-central-refresh); all
five tasks complete; pending operator ratification of the decision of record
below (supersedes the original #014 scoping, which assumed token death was a
~monthly expiry chore; verified evidence shows it is a per-refresh
revocation event, hours apart under active dispatch)

## Goal

Claude workers run indefinitely without human re-login, except at the true
refresh-token horizon (~30 days). No worker can revoke the fleet's shared
credential.

## Verified failure mechanism (2026-07-30)

- Each launch seeds a private copy of the shared auth home
  (`.orchestra/homes/claude`) into `.sessions/<harness>/<session_key>` and
  points `CLAUDE_CONFIG_DIR` at it (`envelope.py`). The harness's native
  OAuth refresh rewrites only that private copy, which is deleted after the
  run. The shared home is never written back (confirmed via
  `.credentials.json` mtime frozen at last login).
- The OAuth refresh rotates and revokes the prior token server-side. The
  first worker refresh therefore kills the token still sitting in the shared
  home; every subsequent worker seeds a revoked token and fails with
  `401 OAuth access token has been revoked`.
- Not concurrency-bound: a single worker's refresh causes it. `slots: 1`
  does not help.
- `harness doctor` reports ready because it checks credential presence and
  `expiresAt`, not server-side revocation.

Interactive Claude Code never hits this because its refresh persists in
place in `~/.claude`, rolling the chain forward. Orchestra performs the same
refresh and then discards the result.

## Design

Keep the copy-per-launch seed (isolation is still correct). Change who
refreshes and what gets seeded:

1. **Single-writer central refresher.** The engine owns the shared home's
   token lifecycle. It refreshes when the access token's remaining lifetime
   drops below `threshold + max expected run duration`, writing the new
   credential atomically under a lock.
2. **Quiescence requirement.** Rotation revokes the prior access token, so a
   refresh while workers are in flight would 401 them mid-run. The refresher
   runs only when no claude workers are active: hold new dispatches, drain,
   refresh, resume. Refresh checks happen at dispatch boundaries.
3. **Refresh mechanism.** Prefer invoking the harness itself against the
   shared home (`CLAUDE_CONFIG_DIR=<shared home>` plus the cheapest command
   that triggers its native refresh-and-persist) over reimplementing the
   OAuth token-endpoint call. Reuses the harness's own logic and matches
   interactive semantics exactly. Direct token-endpoint call is the fallback
   if no cheap CLI trigger exists (open question below).
4. **Seed without the refresh token.** Strip the refresh token from the
   per-launch copy in `envelope.py`. A long run that crosses the expiry
   threshold would otherwise attempt refresh with a stale rotated token;
   refresh-token reuse commonly trips reuse-detection that revokes the whole
   token family (unverified for Anthropic's endpoint — treated as a hazard).
   With no refresh token in the copy, a worker physically cannot rotate or
   revoke anything; its worst case is a clean 401 at true access expiry,
   which item 1's scheduling makes rare.
5. **Honest doctor.** `harness doctor claude` adds a cheap authenticated
   probe so server-side revocation is visible, and keeps the expiry
   readout/warning from the original scoping.
6. **One-command re-login.** `orchestra harness login <name>` runs the
   isolated-home login flow that `harness setup` currently only prints.
   Needed at the ~30-day horizon or after a family revocation.

Proposed decision of record (operator-ratified, docs/design/DECISIONS.md
once that tree exists): copy-per-launch seeding stays; token refresh is
centralized in the engine as single writer; workers never carry a refresh
token. This replaces the earlier proposed ruling that rejected write-back —
that ruling assumed revocation was expiry-driven and ~monthly, which the
2026-07-30 evidence disproved.

## Implementation deviations

- **Resume seeding.** Rather than seeding a resumed launch's whole home from
  the shared home, resume launches seed from the parent session's home (so
  `--resume` can still resolve its transcript inside `CLAUDE_CONFIG_DIR`) and
  then `envelope.reseed_credentials` swaps in the shared home's credential,
  re-stripped of its refresh token. Shared-home seeding wholesale was
  rejected because it would have lost the transcript the resume exists for.
- **Non-blocking dispatch-side locking.** Dispatch's credential-lock
  acquisitions (`auth.credential_lock(..., blocking=False)`) are
  non-blocking: a tick that finds the lock held (e.g. by an operator running
  `doctor`/`login`) defers that harness to the next tick rather than
  stalling the whole engine. Operator-driven `harness doctor` and `harness
  login`, by contrast, block on the lock — they run once, interactively, and
  are expected to wait.
- **Doctor refuses rotation under load.** `harness doctor` refuses to trigger
  a rotation when the token is stale (within the refresh margin) and workers
  of that harness are active, reporting login `not_checked_workers_active`
  instead — the obvious operator flow (status shows a held refresh, operator
  runs doctor to see why) would otherwise 401 every in-flight worker.
- **`harness login` requires `--force` while workers are active.** Login
  rotates the shared credential outright, so it refuses unless the harness
  is quiesced or the operator passes `--force`.
- **Held rather than degraded on an expired token.** A failed refresh with
  an access token that is *already expired* holds that harness's dispatches
  (retried each tick) instead of proceeding degraded — proceeding would fail
  every issue on authentication preflight, which is a blocking outcome, so
  "degraded" would otherwise become "whole queue blocked."

## Acceptance criteria

- [x] Engine refreshes the shared home's token only while quiesced (no
      active claude workers), atomically and under a lock; new dispatches
      seed the refreshed credential.
- [x] A dispatch whose access token has less remaining lifetime than the
      configured margin triggers drain-refresh-resume rather than seeding a
      near-dead token.
- [x] Per-launch seeds contain no refresh token (`envelope.py`); a worker
      attempting refresh fails without side effects on the shared home or
      token family.
- [x] `harness doctor claude` detects server-side revocation (authenticated
      probe) and prints access/refresh expiry with a warning threshold.
- [x] `orchestra harness login <name>` performs the isolated-home login in
      one command.
- [x] Focused tests: quiescence gating, atomic locked write-back, refresh-
      token stripping, doctor probe, login subcommand. Ruff and strict mypy
      pass.

## Ordered steps

1. Spike: find the cheapest harness CLI invocation that refreshes and
   persists in place when near expiry; record it here. Fallback: direct
   token-endpoint refresh implementation note.
2. `envelope.py`: strip refresh token from seeds + test.
3. Engine refresher: lifetime check at dispatch boundaries, quiesce/drain,
   locked atomic write-back + tests.
4. Doctor probe + expiry readout + tests.
5. `harness login` subcommand + test.
6. Propose the decision of record for operator ratification (handled at
   finish: the controller presents the proposal from the Design section).

## Global constraints

- Python 3.13, src layout, `uv`. Run checks the repo way (see `AGENTS.md`
  and `pyproject.toml`): pytest suite, ruff, strict mypy — all must pass.
- NEVER run a real OAuth refresh, login, or logout against the live shared
  home (`.orchestra/homes/claude`) or any real credential in spikes or
  tests. Tests use synthetic credential files in tmp dirs only. A stray
  refresh revokes the operator's live fleet token.
- The credential file is the harness's own (`.credentials.json` in the
  config dir): treat its schema as external data — parse defensively,
  preserve unknown fields byte-for-byte where the design says "intact."
- Match existing code style and module boundaries; no new dependencies
  without strong reason.

## Tasks

### Task 1 — Spike: refresh trigger, credential schema, probe endpoint

Read-only investigation; no production code. Deliverable is a note at
`docs/notes/2026-07-30-refresh-trigger-spike.md` answering:

1. **Refresh trigger:** the cheapest `claude` CLI invocation that, with
   `CLAUDE_CONFIG_DIR` pointed at a config dir whose access token nears
   expiry, performs the native refresh and persists the rotated credential
   in place. Inspect `claude --help`, auth-related subcommands, and the
   installed CLI's code if inspectable. If no free trigger exists, identify
   the cheapest metered one (e.g. a minimal `-p` call) and, as fallback,
   document what a direct token-endpoint refresh would require (endpoint,
   client id, grant type) from CLI inspection only.
2. **Credential schema:** exact JSON shape of `.credentials.json` (field
   names for access token, refresh token, expiry, scopes, wrapping object).
   Use the live file's KEYS only — never copy, print, or move token VALUES.
3. **Probe endpoint:** a zero- or near-zero-cost authenticated HTTP request
   that returns success for a valid access token and 401 for a revoked one
   (candidate: the OAuth profile/userinfo endpoint the CLI itself calls).
4. Where in this repo the engine could invoke the trigger: confirm how
   `harness.py`/`envelope.py` compose the CLI invocation today.

Constraints: do not run any command that could refresh or revoke the live
token (no `auth login`, no `auth logout`, no metered calls against the real
home). Everything observational.

### Task 2 — Strip refresh token from per-launch seeds

In `src/orchestra/envelope.py`, after the per-launch home copy is seeded,
rewrite the seeded credential file to remove the refresh token (field name
per Task 1's spike note), leaving every other field and file intact. If the
seeded home has no credential file, do nothing. Tests
(`tests/test_envelope.py`): seeding a synthetic home whose credential file
contains a refresh token yields a copy without it, all sibling fields and
files byte-identical; a home without a credential file seeds as today.

### Task 3 — Central refresher in the engine

Single-writer refresh of the shared home's credential, owned by the
dispatch path (`supervisor.py` / `harness.py` — follow the repo's actual
seams):

- At each dispatch boundary for a harness with a managed home: read the
  shared home's credential expiry; if remaining lifetime < margin (config:
  `refresh_margin`, default generous enough to cover the longest expected
  run — pick from existing config patterns in `config.py`), do not seed a
  near-dead token; instead quiesce and refresh.
- Quiesce: refresh only when no workers of that harness are active. Hold
  the new dispatch, wait for active launches to drain (the supervisor knows
  its launches), refresh, then resume.
- Refresh mechanism: Task 1's trigger, invoked with `CLAUDE_CONFIG_DIR`
  pointing at the shared home; on success the harness has persisted the
  rotated credential itself. Write access to the shared home is serialized
  with an exclusive file lock; any orchestra-side rewrite of the credential
  file is atomic (tmp file + rename).
- Failure: if the refresh invocation fails, log loudly and dispatch anyway
  with the existing token (degraded, not dead-locked); surface the failure
  in status output.
- Tests: quiescence gating (no refresh while a launch is active; refresh
  before the next seed once drained), margin arithmetic on synthetic
  credentials, lock serialization, atomic write. Fake the refresh
  invocation; never a real one.

### Task 4 — Honest doctor

`orchestra harness doctor claude` (see `cli.py`/`harness.py` doctor path)
gains: (a) expiry readout — access and refresh expiry timestamps with
days-remaining, WARNING when the refresh horizon is within threshold
(default 5 days); (b) a revocation probe — the Task 1 probe endpoint called
with the shared home's access token, reporting valid/revoked/unreachable
(unreachable is a warning, not a failure). Probe is opt-out-able via flag
if it costs anything. Tests: fake HTTP responses for valid/revoked/
unreachable; expiry math on synthetic credentials.

### Task 5 — `orchestra harness login <name>`

New CLI subcommand running the isolated-home login flow that `harness
setup` currently only prints (`CLAUDE_CONFIG_DIR=<managed home> <exe>`
login invocation, interactive, exec'd so the operator drives it). Refuse
with a clear message when the home does not exist (point at `harness
setup`). Tests: command composition and error paths (no interactive login
in tests).

## Open questions

- Does the harness CLI expose a no-cost refresh trigger, or is the cheapest
  trigger a minimal metered call?
- Does Anthropic's OAuth endpoint revoke the token family on refresh-token
  reuse? (Determines how bad a stale-seed refresh attempt is today;
  irrelevant once seeds carry no refresh token.)
