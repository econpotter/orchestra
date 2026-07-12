# Issue State Machine

Statuses an issue moves through. `dispatch` records handles in workers.json and never writes the queue; `reconcile` stamps `in_progress` for live workers and applies every terminal transition.

| Status | Meaning | Set by |
|---|---|---|
| open | newly promoted by a human; not yet validated | human |
| validated | passed validate (structural + optional semantic); dispatchable to a worker | reconcile |
| held | passed validate but `Network: true`; parked pending an explicit `orchestra release` — never auto-dispatched | reconcile |
| in_progress | a worker is running on it | reconcile (on dispatch) |
| committed | worker finished with a commit on the branch; awaiting verify | reconcile |
| needs_rework | verify rejected; re-dispatch to worker with feedback (Retries++) | reconcile |
| awaiting_review | verify accepted OR retry cap hit; awaiting human sign-off | reconcile |
| blocked | unexpected stop; free-text `### Blocked Reason` (stuck / crash / invalid) | reconcile |
| archived | human approved; branch merged into base and issue moved to queue/archive/<project>.md | merge-and-archive |

## Stop model
- **Soft block** — worker logs a decision in `### Decisions` and keeps going; reviewed
  in batch at verify. Not a status.
- **Done** — commit on branch → `committed`.
- **Blocked** — self-reported stuck, or inferred crash, or invalid issue → `blocked`
  with a free-text reason. `awaiting_review` and `blocked` are deliberately distinct.

## Lifecycle
```
open --validate(pass)--> validated --dispatch--> in_progress
open --validate(pass) + Network--> held --release--> validated
open --validate(fail)--> blocked (invalid)
in_progress --commit--> committed --verify(accept)--> awaiting_review --human approve (merge)--> archived
in_progress --self-reported stuck--> blocked
committed --verify(reject)--> needs_rework --dispatch--> in_progress
needs_rework --retry cap hit--> awaiting_review
```
`Retries` counts only verify↔worker bounces. Verifier feedback (`### Verifier Feedback`) carries reject complaints and is included in the issue when a worker bounces.

## Crash-retry
A **crash** is unambiguous: the agent process is dead **and** wrote no result file (a
self-reported block writes a result with `### Blocked Reason`, so it is never a crash). The
work itself is intact — a worker's commit is on its branch; a verifier only reads a
committed diff — so `reconcile` re-queues a crashed agent to its prior dispatchable state
instead of blocking, bounded by `crash_retries_cap` (config, default 2):

| Crashed role | Re-queue to |
|---|---|
| validator | `open` (re-validate) |
| worker (no commit) | `validated`, or `needs_rework` if `Retries>0` — or `held` when `Network: true` |
| verifier | `committed` (re-verify) |

`Crash-Retries` (issue field) counts these bounces and resets to 0 whenever the issue
reaches a terminal via a real result (worker commit, verifier accept/reject), so the cap
bounds a crash *loop*, not the issue's lifetime. Past the cap → `blocked` with the crash
reason. A `Network: true` worker crash re-queues to `held`, never auto-re-dispatching.

## Network gate
An issue with `Network: true` that passes validation goes to `held`, not `validated`.
`held` is not in dispatch's candidate set, so a heavy/irreversible network job never runs
unattended. `orchestra release <project> <number>` promotes `held → validated` (the
explicit human go-ahead); it refuses any other status.

The gate governs *dispatch* only. At run time the `Network` flag is **advisory** — there is
no per-issue network jail. `sandbox.enabled` provides **filesystem** confinement, not
network isolation: `argv_prefix` runs each agent under `bwrap` with the rootfs ro-bound and
only its workdir/tmp/results_dir writable, so a confined agent cannot write outside its
worktree — but the network is shared, because the agent must reach its own model API to run
at all. Blocking a heavy/irreversible mass-fetch therefore relies on the dispatch gate
(`held`/`release`) plus the agent prompt, not on a run-time egress block. Real per-issue
network isolation would require an `api.anthropic.com`-only egress allowlist (proxy or
nftables); it is deliberately out of scope for a lean orchestrator.

(Historical note: issue #004 shipped a `bwrap --unshare-all` prefix that claimed to
network-isolate `Network: false` agents. It was broken two ways — the prefix bound no
rootfs so `bwrap` could not even exec the agent, and `--unshare-all` would also have
severed the agent's own model-API egress, so a `Network: false` worker could never complete
a call. Issue #005 removed the false run-time-isolation claim and made the sandbox a real,
tested filesystem confinement.)
