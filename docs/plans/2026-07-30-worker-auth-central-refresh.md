# Worker auth: central refresh of the shared harness token

Date: 2026-07-30
Status: draft (supersedes the original #014 scoping, which assumed token
death was a ~monthly expiry chore; verified evidence shows it is a
per-refresh revocation event, hours apart under active dispatch)

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

## Acceptance criteria

- [ ] Engine refreshes the shared home's token only while quiesced (no
      active claude workers), atomically and under a lock; new dispatches
      seed the refreshed credential.
- [ ] A dispatch whose access token has less remaining lifetime than the
      configured margin triggers drain-refresh-resume rather than seeding a
      near-dead token.
- [ ] Per-launch seeds contain no refresh token (`envelope.py`); a worker
      attempting refresh fails without side effects on the shared home or
      token family.
- [ ] `harness doctor claude` detects server-side revocation (authenticated
      probe) and prints access/refresh expiry with a warning threshold.
- [ ] `orchestra harness login <name>` performs the isolated-home login in
      one command.
- [ ] Focused tests: quiescence gating, atomic locked write-back, refresh-
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
6. Propose the decision of record for operator ratification.

## Open questions

- Does the harness CLI expose a no-cost refresh trigger, or is the cheapest
  trigger a minimal metered call?
- Does Anthropic's OAuth endpoint revoke the token family on refresh-token
  reuse? (Determines how bad a stale-seed refresh attempt is today;
  irrelevant once seeds carry no refresh token.)
