# Issue State Machine

Statuses an issue moves through. `dispatch` records handles in workers.json and never writes the queue; `reconcile` stamps `in_progress` for live workers and applies every terminal transition.

| Status | Meaning | Set by |
|---|---|---|
| open | newly promoted by a human; not yet validated | human |
| validated | passed validate (structural + optional semantic); dispatchable to a worker | reconcile |
| held | explicitly parked before validation or worker dispatch | human / reconcile network gate |
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
open + unapproved Network + hold_network_issues --> held --release--> open
open --validate(pass)--> validated
open --validate(fail)--> blocked (invalid)
in_progress --commit--> committed --verify(accept)--> awaiting_review --human approve (merge)--> archived
in_progress --self-reported stuck--> blocked
committed --verify(reject)--> needs_rework --dispatch--> in_progress
needs_rework --retry cap hit--> awaiting_review
```
`Retries` counts only verify↔worker bounces. Verifier feedback (`### Verifier Feedback`) carries reject complaints and is included in the issue when a worker bounces.

## Attempt recovery
Every harness execution finalizes a durable attempt manifest. Reconcile derives recovery from
its structured failure category, session capability, Git evidence, and bounded attempt count;
it never scrapes prose logs. Provider quota/upstream interruptions and configured time limits
resume the same durable session when possible. Harness/protocol/environment failures receive a
bounded fresh attempt. Authentication, acceptance, human-required, and intentional
cancellation failures block without retry. A worker result and branch delta must agree.

`Crash-Retries` (issue field) counts these bounces and resets to 0 whenever the issue
reaches a terminal via a real result (worker commit, verifier accept/reject), so the cap
bounds a crash *loop*, not the issue's lifetime. Past the cap → `blocked` with the crash
reason. A network worker crash re-queues normally; the optional hold policy is a one-time
pre-validation gate, not a retry gate.

## Network metadata and optional gate
An issue with `Network: true` is dispatchable by default. The field records that a task
uses external data or services so operators and prompts can treat it accordingly; ordinary
network access does not require a manual state transition.

Set `hold_network_issues: true` in `config.yaml` to require an explicit gate for every new
network issue. Dispatch refuses an unapproved `open` network issue before semantic validation,
and reconciliation records it as `held`. `orchestra release <project> <number>` records
`Network-Approved: true` and moves `held → open`, so normal validation runs next. Approval is
persistent across validation, worker recovery, and verifier rework. `orchestra hold` revokes
approval for a network issue.

`held` is sticky operator state. Issues may be submitted with `issue add --held`, changed with
`orchestra hold`, or edited directly in the queue. Reconciliation never releases them, including
when `hold_network_issues` is turned off. Only explicit `release` returns them to `open`.
`orchestra hold` accepts inactive `open`, `validated`, `needs_rework`, and `blocked` issues; it
refuses active, committed, review, and archived work.

At run time the `Network` flag is **advisory** — there is no per-issue network jail.
`sandbox.enabled` provides **filesystem** confinement, not network isolation. Orchestra runs
the supervisor under `bwrap` inside a transient user systemd service, with the root filesystem
read-only and only the attempt, role-specific worktree, Git metadata, temporary directory, and
isolated harness state writable. The network is shared because the harness must reach its own
model API to run at all. Real per-issue network isolation would require an egress allowlist or
proxy; it is deliberately out of scope for a lean orchestrator.

(Historical note: issue #004 shipped a `bwrap --unshare-all` prefix that claimed to
network-isolate `Network: false` agents. It was broken two ways — the prefix bound no
rootfs so `bwrap` could not even exec the agent, and `--unshare-all` would also have
severed the agent's own model-API egress, so a `Network: false` worker could never complete
a call. Issue #005 removed the false run-time-isolation claim and made the sandbox a real,
tested filesystem confinement.)
