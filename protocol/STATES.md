# Issue State Machine

Statuses an issue moves through. `dispatch` records handles in workers.json and never writes the queue; `reconcile` stamps `in_progress` for live workers and applies every terminal transition.

| Status | Meaning | Set by |
|---|---|---|
| open | newly promoted by a human; not yet validated | human |
| validated | passed validate (structural + optional semantic); dispatchable to a worker | reconcile |
| held | passed validate under the opt-in network-hold policy; parked pending `orchestra release` | reconcile |
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
open --validate(pass) + Network + hold_network_issues--> held --release--> validated
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
| worker (no commit) | `validated`, or `needs_rework` if `Retries>0` — or `held` when both `Network: true` and `hold_network_issues: true` |
| verifier | `committed` (re-verify) |

`Crash-Retries` (issue field) counts these bounces and resets to 0 whenever the issue
reaches a terminal via a real result (worker commit, verifier accept/reject), so the cap
bounds a crash *loop*, not the issue's lifetime. Past the cap → `blocked` with the crash
reason. A network worker crash re-queues normally unless the optional hold policy is on.

## Network metadata and optional gate
An issue with `Network: true` is dispatchable by default. The field records that a task
uses external data or services so operators and prompts can treat it accordingly; ordinary
network access does not require a manual state transition.

Set `hold_network_issues: true` in `config.yaml` to require an explicit gate for every
network issue. Under that policy, successful validation and transient worker retries go
to `held`, which is not dispatchable. `orchestra release <project> <number>` promotes
`held → validated`; it refuses any other status. Turning the policy off promotes existing
network issues from `held` to `validated` during the next reconcile.

At run time the `Network` flag is **advisory** — there is no per-issue network jail.
`sandbox.enabled` provides **filesystem** confinement, not
network isolation: `argv_prefix` runs each agent under `bwrap` with the rootfs ro-bound and
only its workdir/tmp/results_dir writable, so a confined agent cannot write outside its
worktree — but the network is shared, because the agent must reach its own model API to run
at all. Real per-issue network isolation would require an egress allowlist or proxy; it is
deliberately out of scope for a lean orchestrator.

(Historical note: issue #004 shipped a `bwrap --unshare-all` prefix that claimed to
network-isolate `Network: false` agents. It was broken two ways — the prefix bound no
rootfs so `bwrap` could not even exec the agent, and `--unshare-all` would also have
severed the agent's own model-API egress, so a `Network: false` worker could never complete
a call. Issue #005 removed the false run-time-isolation claim and made the sandbox a real,
tested filesystem confinement.)
