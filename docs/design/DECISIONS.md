# Decisions

Append-only dated rulings with rationale and evidence. Operator-ratified
only: agents propose entries (in plan documents or reports) but never write
this file directly.

## 2026-07-30 — Worker auth: copy-per-launch stays; refresh is engine-owned

**Decision:** The copy-per-launch read-only-seed auth model (`envelope.py`)
stays. Token refresh is centralized in the engine as a single writer: the
engine refreshes the shared home's credential at dispatch boundaries, under
an exclusive lock, only while the harness is quiesced. Per-launch seeds
never carry a refresh token, so a worker physically cannot rotate or revoke
the shared credential.

**Reason:** The OAuth refresh rotates and revokes the prior token
server-side, and workers previously performed that refresh into throwaway
per-launch copies — persisting nothing back and revoking the shared token
for every subsequent worker (verified 2026-07-30; see
`docs/notes/2026-07-30-refresh-trigger-spike.md` and
`docs/plans/2026-07-30-worker-auth-central-refresh.md`). This replaces the
earlier proposed ruling (original #014 scoping) that rejected write-back on
the assumption that token death was a ~monthly expiry chore; the evidence
showed it is a per-refresh revocation event, hours apart under active
dispatch. Central refresh reproduces what makes interactive Claude Code
sessions long-lived — refresh-and-persist in place — while the quiescence
requirement prevents rotation from 401ing in-flight workers. Ratified by
the operator 2026-07-30.
