# Refresh-trigger spike (worker auth central refresh, Task 1)

Date: 2026-07-30
Scope: read-only investigation for `docs/plans/2026-07-30-worker-auth-central-refresh.md`,
Task 1. No production code changed. All commands below ran with `CLAUDE_CONFIG_DIR`
pointed at a synthetic, throwaway credential directory under the scratchpad
(`/tmp/.../scratchpad/fake-claude-home`), never at the live shared home
(`.orchestra/homes/claude`) or the operator's interactive home (`~/.claude`). No
`auth login`, `auth logout`, or metered `-p` call was run against a real credential.

Installed CLI: `claude` 2.1.220 (native/bun-compiled single binary,
`/home/potterzot/.local/share/claude/versions/2.1.220`, no separate readable JS
source tree — inspected via `--help` output and `strings` over the binary).

## 1. Refresh trigger

**Finding: `claude auth status --json` is a free (non-metered) invocation that
performs the native refresh-and-persist when the access token is expired, and the
engine already runs this exact command against a harness home today.**

Evidence, in order:

- `claude --help` lists `auth` as a subcommand with `login`, `logout`, `status`
  children; `claude auth status --help` shows only `--json`/`--text`, no flags that
  suggest a network call is optional or skippable.
- Empirical test: created a synthetic `.credentials.json` (fabricated
  `accessToken`/`refreshToken` strings, real-shaped `expiresAt` set to 1 hour in the
  past, `refreshTokenExpiresAt` 30 days out) in an isolated `CLAUDE_CONFIG_DIR`, then
  ran:
  ```
  CLAUDE_CONFIG_DIR=<synthetic dir> claude auth status --json
  ```
  Output was a plain JSON status blob (`loggedIn`, `authMethod`, `apiProvider`,
  `email`, `orgId`, `orgName`, `subscriptionType`) — no model call, no token usage.
  Exit code 0. But the on-disk credential file was rewritten in place: the
  (fabricated) refresh attempt was rejected server-side (the refresh token was
  fake), and the CLI persisted the failure by zeroing `accessToken`/`refreshToken`
  to `""` while leaving `refreshTokenExpiresAt`, `scopes`, `subscriptionType`, and
  `rateLimitTier` untouched. This confirms two things: (a) `auth status` reads the
  credential, sees the access token is past `expiresAt`, and attempts the native
  refresh flow before returning status, and (b) it persists the *result* of that
  attempt — success or failure — back to `.credentials.json` unconditionally, not
  just on success.
  - Repeated the same test with `claude doctor` (no `--json`, plain native health
    check) against the same synthetic near/expired token: identical behavior — it
    also triggers the refresh attempt and rewrites the credential file in place.
    So both `auth status --json` and plain `doctor` share this trigger path; `auth
    status --json` is preferable because its output is structured and it is what
    the engine already invokes (see Q4).
- `strings` over the binary surfaces the underlying token-manager vocabulary that
  explains the mechanism: `getToken`, `doRefresh`, `backgroundRefresh`, plus a debug
  log template `"Token for sessionId=... expires=... (past or within buffer),
  refreshing immediately"` and `"Scheduled token refresh for sessionId=...
  (expires_in=Xs, buffer=Y)"`. This is a proactive-refresh-with-buffer design: a
  token past expiry (or within an internal buffer the CLI does not expose as a
  flag) is refreshed on the next call that resolves a token, which `auth status`
  and `doctor` both do internally (confirmed empirically above) even though neither
  makes an inference call.

**No-cost trigger identified — no fallback to a metered `-p` call is needed.**
`claude auth status --json` (or `claude doctor`) against `CLAUDE_CONFIG_DIR=<shared
home>` is the cheapest native trigger: zero tokens billed, structured JSON output,
mutates the credential file in place exactly as an interactive session's background
refresh would.

**Fallback (direct token-endpoint call), for completeness only — not needed given
the above, and never executed:**
- Token endpoint (from binary strings, `TOKEN_URL` constant resolves to):
  `https://platform.claude.com/v1/oauth/token`
- Grant type: literal string `"refresh_token"` appears alongside `expires_in`,
  `refresh_token_expires_in`, and the event names `oauth_token_refresh`,
  `tengu_oauth_token_refresh_success`/`_failure`, `oauth_refresh_invalid_grant`,
  confirming a standard OAuth2 refresh-token grant POST with a JSON body.
- Client id: two UUIDs sit near the OAuth endpoint list in the binary —
  `9d1c250a-e61b-44d9-88ed-5944d1962f5e` and `59637612-477b-4836-a601-b0589eda7704`
  (the latter appears under a `DESIGN_CLIENT_ID`-style constant name, so it may be a
  design-tool/staging client rather than the CLI's). Static string extraction
  cannot disambiguate which one is sent as `client_id` in the CLI's own refresh
  request body without instrumenting a real call, which the safety constraints
  forbid. **Do not treat either UUID as confirmed** without a lower-risk
  verification path (e.g. asking Anthropic, or capturing the CLI's own outbound
  request via a local mitmproxy against a disposable/free-tier test account — not
  attempted here). Since the CLI-invocation trigger above is free and sufficient,
  this ambiguity has no bearing on Task 3's design.
- A revoke endpoint also exists (`/revoke`, event `oauth_token_revoke`), invoked
  during `auth logout` local cleanup — a distinct code path from refresh-token
  rotation, not exercised or needed here.

## 2. Credential schema

Read the live shared home's credential files for **keys only** — no token value was
printed, copied, or transmitted. Two files were found in the shared home tree:

- `/home/potterzot/workspace/.orchestra/homes/claude/.credentials.json` (509 bytes,
  mtime 2026-07-30 08:16)
- `/home/potterzot/workspace/.orchestra/homes/claude/.claude/.credentials.json`
  (509 bytes, same mtime)

Both have the identical shape:

```json
{
  "claudeAiOauth": {
    "accessToken": "<string, 108 chars observed>",
    "refreshToken": "<string, 108 chars observed>",
    "expiresAt": 0,
    "refreshTokenExpiresAt": 0,
    "scopes": ["<string>", "..."],
    "subscriptionType": "<string>",
    "rateLimitTier": "<string>"
  }
}
```

Non-secret field values (safe to record — timestamps, scope names, tier labels,
never token bytes):

| field | outer `.credentials.json` | nested `.claude/.credentials.json` |
|---|---|---|
| `expiresAt` | 1785449770979 → 2026-07-30T22:16:10Z | 1784528195272 → 2026-07-20T06:16:35Z |
| `refreshTokenExpiresAt` | 1787988703979 → 2026-08-29T07:31:43Z | 1786945011272 → 2026-08-17T05:36:51Z |
| `scopes` | `user:file_upload`, `user:inference`, `user:mcp_servers`, `user:profile`, `user:sessions:claude_code` | same |
| `subscriptionType` | `max` | `max` |
| `rateLimitTier` | `default_claude_max_20x` | `default_claude_max_20x` |

Both `expiresAt` and `refreshTokenExpiresAt` are Unix epoch **milliseconds**, not
seconds (matches the CLI's own `expires_in`/refresh-token-derived arithmetic seen
in the binary strings).

**Repo-hygiene finding, not part of the four spike questions but worth flagging for
Task 2/3:** the managed home directory
`/home/potterzot/workspace/.orchestra/homes/claude/` contains a full home-directory
shape (`.cache`, `.claude/`, `.claude.json`, `.config`, `.local`, `backups/`,
`session-env/`, `sessions/`, `shell-snapshots/`) rather than a minimal
`CLAUDE_CONFIG_DIR`-only directory, and it has **two different, out-of-sync**
credential files (different `expiresAt`/`refreshTokenExpiresAt`, ten days apart).
`CLAUDE_CONFIG_DIR` makes the top-level `.credentials.json` authoritative — the CLI
reads/writes `$CLAUDE_CONFIG_DIR/.credentials.json` directly, not
`$CLAUDE_CONFIG_DIR/.claude/.credentials.json` (verified: the synthetic test file
placed directly at the config-dir root was the one read and rewritten). The nested
`.claude/.credentials.json` is stale and not on the path any harness launch uses;
it looks like a leftover from populating the managed home by copying a real
`~/.claude` directory rather than logging in fresh via `CLAUDE_CONFIG_DIR=... claude
auth login`. Task 3's atomic-write-back code should target the top-level file only
and this stale nested copy should probably be deleted by whoever owns workspace
hygiene (out of scope for this spike and this repo — `.orchestra/homes` is
workspace state, not engine source, per `AGENTS.md`).

## 3. Probe endpoint

Two authenticated-GET candidates surfaced in the binary, both under
`https://api.anthropic.com` (the default `BASE_API_URL`):

- **`/api/oauth/validate`** — purpose-built for exactly this check. A string
  literal elsewhere in the binary describes it directly: `"[Claude in Chrome]
  Disabled: OAuth token has no scope accepted by /api/oauth/validate (needs
  user:profile, user:office, or user:ccr_inference; ...)"`. The shared home's token
  already carries `user:profile` (see Q2 scopes table), so it satisfies this
  endpoint's scope requirement. This is the **recommended probe candidate**: it
  exists specifically to answer "is this token still good," should return a small
  response, and a revoked/expired token should 401 rather than needing scope logic
  to interpret a bigger payload.
- **`/api/oauth/profile`** — heavier: returns account/profile fields
  (`emailAddress`, `organization`, `subscriptionType`, `rateLimitTier`, `seatTier`,
  `accountCreatedAt`, etc. — field names taken from binary strings, e.g.
  `tengu_oauth_profile_fetch_success`, `rawProfile`, `profileFetchedAt`). Usable as
  a fallback if `validate` turns out to need a scope the shared token lacks, but
  costs more (bigger response) for the same yes/no signal.

Both are called with `Authorization: Bearer <access_token>` and
`Content-Type: application/json` (validate) / `Cache-Control: no-cache` (profile)
per the binary's request-building strings. Neither was invoked against the live
token in this spike — the safety constraint against HTTP requests carrying the real
token was honored throughout; this is inspection-only evidence. Task 4 (doctor's
revocation probe, out of scope for this task) should confirm the exact response
shape and 401 behavior with a disposable/synthetic OAuth token before wiring it in,
the same way this spike validated the refresh trigger.

## 4. Where the engine composes the CLI invocation today

`src/orchestra/envelope.py`:
- `build_execution_envelope()` (`envelope.py:127-182`) sets
  `environment=(("CLAUDE_CONFIG_DIR", str(state_dir)),)` for `harness.kind ==
  "claude"` (`envelope.py:176-181`).
- `state_dir` comes from `_launch_home()` (`envelope.py:116-124`), which returns
  `managed_auth_home()` (the shared home) when `session_key is None`, or
  `session_state_home()` (a private per-launch copy under
  `.orchestra/homes/.sessions/<harness>/<session_key>`) when a `session_key` is
  given.
- `seed_session_home()` (`envelope.py:87-113`) is what copies the shared home into
  that private per-launch directory before each launch — this is the copy the plan
  document identifies as the one whose OAuth refresh currently gets discarded.

`src/orchestra/harness.py`:
- `preflight_authentication()` (`harness.py:413-429`) is the actual trigger call
  site. For `kind == "claude"` it runs exactly
  `[executable, "auth", "status", "--json"]` (`harness.py:419`) with the caller-
  supplied `environment` dict (which carries `CLAUDE_CONFIG_DIR`), 15s timeout,
  `check=False`. This is the same command validated as the refresh trigger in Q1.

`src/orchestra/dispatch.py`:
- Around `dispatch.py:480-492`, after `preflight_harness()` succeeds, dispatch
  builds `environment = os.environ.copy(); environment.update(dict(envelope.environment))`
  and calls `preflight_authentication(harness.kind, harness.executable,
  environment)` — against the **per-launch session copy** (`envelope` here was
  built with a `session_key`, per the surrounding attempt-launch flow), not the
  shared home directly. This is the dispatch-time call that the plan's "Verified
  failure mechanism" section identifies as the one whose refresh gets thrown away
  when the private copy is later deleted.

`src/orchestra/cli.py`:
- `cmd_harness_doctor()` (`cli.py:136-174`) composes the **identical** command —
  `[harness.executable, "auth", "status", "--json"]` (`cli.py:162`) — but through
  `_isolated_harness()` (`cli.py:~85-95`), which calls `build_execution_envelope(...,
  home=Path.home(), ...)` **without** a `session_key`. Per `_launch_home()`'s branch
  above, that means `orchestra harness doctor claude` today runs `auth status
  --json` directly against the **shared** home, not a per-launch copy. If the
  shared token happens to be expired when an operator runs `harness doctor claude`,
  this existing code path would itself perform a real refresh-and-persist directly
  on the shared home — which is actually the *desired* single-writer behavior Task
  3 wants, just uncoordinated (no quiescence check, no lock) and not currently
  described as intentional anywhere in the code or its docstrings.

**Implication for Task 3:** the trigger the engine needs already exists and is
already called in two places (`dispatch.py:483` against a per-launch copy,
`cli.py:162` against the shared home directly). Centralizing refresh means: (a)
stop pointing this call at per-launch copies for the purpose of refreshing (the
per-launch `preflight_authentication` call can stay for its current job — verifying
the seeded copy is authenticated — but must not be relied on to refresh), and (b)
add a deliberate, quiesced, locked invocation of the same `[executable, "auth",
"status", "--json"]` command with `CLAUDE_CONFIG_DIR` pointed at the shared home
(`managed_auth_home()`, `envelope.py:40-61`) at dispatch boundaries, mirroring what
`cmd_harness_doctor` already does today by accident.

## Uncertainties / not verified

- The exact "buffer" window the CLI uses to decide "near expiry" (as opposed to
  "past expiry," which this spike did confirm triggers refresh) was not pinned
  down as a constant — the debug-log template names a `buffer` value but the
  binary's minified code did not yield a plain numeric constant under `strings`.
  Task 3 does not need this: the plan already specifies the engine choose its own
  `refresh_margin` independently of the CLI's internal buffer, and "past expiry"
  is confirmed sufficient to trigger the same trigger command either way.
- The two client-id UUIDs are unconfirmed (see Q1 fallback section) — irrelevant
  unless a future task needs the direct token-endpoint fallback, which the free CLI
  trigger currently makes unnecessary.
- The probe endpoints (`/api/oauth/validate`, `/api/oauth/profile`) were identified
  from binary strings only; their exact success/401 response shape was not
  observed live (would require a request carrying a real token, out of bounds for
  this spike). Task 4 should verify with a synthetic/disposable token before
  wiring the probe into `doctor`.
- Why the shared home carries two different, out-of-sync `.credentials.json` files
  (top-level vs. nested `.claude/`) was not investigated beyond noting it — that's
  workspace state, not engine source, and outside this task's scope.
