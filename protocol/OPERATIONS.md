# Operations

All operations are stateless: read state from files/git, act, exit. `dispatch` routes
by status; `reconcile` is the sole writer of `queue/`. (dispatch/reconcile/verify land
in Phase B; the deterministic helpers below are Phase A.)

## Phase A helpers (implemented)
- `tools/validate --root ROOT PROJECT NUMBER` — structural validation. Exit 0 valid,
  1 invalid (reasons on stdout), 2 not found / malformed.
- `tools/status-set --root ROOT PROJECT NUMBER STATUS [--reason] [--retries] [--worker]`
  — set fields on one issue.
- `tools/worktree-create --root ROOT PROJECT NUMBER` — create the issue worktree on
  branch `issue/NNN-slug` off the project's base branch; prints the path.
  Exit 0 success, 1 git/operation failure (message on stderr), 2 project/issue not found.
- `tools/merge-and-archive --root ROOT PROJECT NUMBER` — merge the issue branch, remove
  the worktree, move the issue to `queue/archive/<project>.md` as `archived`.
  Exit 0 success, 1 git/operation failure (message on stderr), 2 project/issue not found,
  3 issue not `awaiting_review`. The merge happens **in the project's main checkout** when it
  is on `<base>` and clean, so the working tree reflects the merge (a run imports the merged
  code). If the checkout is dirty or not on `<base>`, it falls back to a throwaway **detached
  worktree** + `update-ref` (robust to the dirty tree) — which advances the ref but leaves the
  checkout **stale**, so refresh it (`git -C <repo> reset --hard <base>`) before running code
  there. New issue worktrees always branch from the (updated) ref regardless.

## Engine operations (implemented, Phase B)

- `tools/dispatch --root ROOT` — status→agent router. Reads every project's queue,
  selects eligible issues (`role_for_issue`: status routable, no active handle,
  dependencies done) up to `config.slots`, lowest Priority first. Creates a worktree
  for first-time workers, launches a detached agent via the config `providers`
  adapter, and records a handle in `.orchestra/workers.json`. **Writes only
  workers.json — never the queue.** Routing: open→validator, validated|needs_rework→
  worker, committed→verifier.
- `tools/reconcile --root ROOT` — the **sole writer** of `queue/`. For each handle:
  live worker → stamp `in_progress`; exited → classify by role + result file +
  commit and apply the terminal transition; stalled (config `stall.idle_minutes`>0
  and log idle) → kill + `blocked`. Removes finished handles and consumed result
  files. `Retries` counts verify↔worker bounces; reject under `retries_cap` →
  needs_rework (Retries++), at cap → awaiting_review with the verifier's complaints
  in `### Verifier Feedback`.
- Agents report via JSON result files in `.orchestra/results/` (schema:
  `{result, decisions, blocked_reason}`); the commit on the branch is authoritative.
- Provider specifics (the `claude -p` invocation, autonomy flags, optional sandbox
  wrapper) live entirely in `config.yaml` (`providers:` / `sandbox:`). Swapping to
  Codex is a config change, not a code change.

**Validation is deterministic by default.** `validate_structural` (title, ≥1 acceptance,
known deps, and that any referenced Plan/Spec exists in the project's **base branch** —
not merely on disk in the root checkout, which can sit on a different/ahead branch) is the
gate. With `validate.semantic: false` (default) `reconcile` promotes `open→validated`
directly when those checks pass — **no validator agent is launched** (one fewer stage).
Set `validate.semantic: true` to additionally run the LLM validator agent for fuzzy
judgment (vague acceptance / insufficient context / unbounded scope) before promotion.

## Scheduling (Phase C)
A **tick** is one `dispatch` then `reconcile`:
- `tools/tick --root ROOT` — runs both as subprocesses (workers orphan and are reaped by
  init, the cron model). Stateless; safe to run on any cadence.
- **systemd (preferred):** install the CLI from the engine checkout first —
  `uv tool install --force --editable /path/to/orchestra` (its venv has the dependencies;
  the bare `tools/tick` runs under the system Python, which may not). Keep operational state
  in a separate workspace and select it with `orchestra workspace set /path/to/workspace`.
  Then use
  `systemd/orchestra.service` (oneshot → `%h/.local/bin/orchestra tick`; workspace comes
  from `orchestra workspace set PATH`)
  + `systemd/orchestra.timer` (`OnCalendar=*:0/15`, `Persistent=true`). Per-user:
  from the engine checkout, run `cp systemd/* ~/.config/systemd/user/`,
  `loginctl enable-linger "$USER"`, `systemctl --user daemon-reload`,
  `systemctl --user enable --now orchestra.timer`. Logs via journald
  (`journalctl --user -u orchestra.service`).
  - **systemd gotchas the shipped unit handles (keep them):** `KillMode=process` — a
    oneshot's default cgroup kill reaps the detached agents the instant `tick` exits, so
    they die mid-run (logged as "crash: no new commit and no result"). `Environment=PATH=…`
    — user services get a minimal PATH; it must include the provider CLI (claude/codex) and
    the tools agents run (uv, git, node) — e.g. `~/.local/bin` plus nvm/cargo bins (find
    them with `command -v claude uv git node`). Without it every tick crashes
    `FileNotFoundError: 'claude'`.
- **crontab fallback:** create `.orchestra/logs` in the workspace, then install a job using
  the installed CLI:

  ```cron
  */15 * * * * orchestra tick --root /path/to/workspace >> /path/to/workspace/.orchestra/logs/cron.log 2>&1
  ```

  `tools/tick` also works from the engine checkout when run under its project environment
  (`uv run tools/tick --root /path/to/workspace`).

### Tick latency
A single tick advances an issue by about one lifecycle stage: `dispatch` launches
agents and `reconcile` reaps the *previous* tick's finishers. End-to-end latency for one
issue is therefore ≈ (number of stages: validate → work → verify → human approve) ×
tick interval. At a 15-minute cadence an issue reaches `awaiting_review` in roughly an
hour of wall-clock once it starts, plus the worker's own run time. Tighten the interval
for faster pickup at the cost of more git/file churn; the agents themselves run detached
across many ticks regardless.

## Harnesses and providers (Phase C)
Launch mechanics currently live in the workspace's `config.yaml` under `providers:`; core
code does not hard-code executable flags. Claude and Codex are the supported harness targets.
For each role, set both `roles.<role>.provider` and `roles.<role>.model`, then define the
selected executable's `argv` template and `prompt: stdin|arg` transport. Model names differ
between harnesses.

The reliability design evolves this opaque provider process into supervised, harness-specific
Claude and Codex adapters while keeping lifecycle policy provider-independent. `PiJsonAdapter`
is a design sketch only: Pi has no implemented or verified harness integration yet. See
`HARNESS-RELIABILITY.md` for the capability contract and rollout status.

## Control surface (Phase E)
The `orchestra` CLI is the human/agent entrypoint (the `tools/*` scripts remain for
systemd). Commands: `issue add` (also `--from-plan`), `issue list`/`show`, `status`,
`logs`, `diff`, `approve`, `reject`, `kill`, `project add`, `pause`/`resume`,
`dispatch`/`reconcile`/`tick`, `guide`, `new-project` — scaffold + register a project
from the dual-language template (overlay `variants/`, `init.sh`); `--lang python|r`,
`--stage`. Read commands take `--json`. The queue stays markdown; the CLI is the
structured interface over it.
