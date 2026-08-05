# Docs sweep — orchestra — 2026-08-02

## 1. Status findings

| doc:line | claim | evidence | proposed edit |
|---|---|---|---|
| docs/plans/2026-07-18-public-prep.md:28,33,44,52,63,73,82 | All 7 checkboxes unchecked, no Status line — plan reads as not started (boxes last changed 7cccf8a 2026-07-18) | Executed and merged same day: commit 9724612 "issue #009: Public-release prep: README pass + health check" (2026-07-18), merged to main ce9adcf (2026-07-18); branch issue/009-public-release-prep-readme-pass-health-c is in `git branch --merged main`; queue archive /home/potterzot/workspace/queue/archive/orchestra.md:174-193 records completion with gates re-run green | Aggregate: flip all 7 boxes to `[x]`; insert after line 1: `Status: complete — executed as orchestra#009, merged to main ce9adcf 2026-07-18.`; move file to docs/plans/completed/ |
| docs/plans/2026-07-30-worker-auth-central-refresh.md:71-72 | "Proposed decision of record (operator-ratified, docs/design/DECISIONS.md once that tree exists):" — implies the design tree does not exist and the ruling is still pending | docs/design/DECISIONS.md exists and records the ratified ruling (commit 78d91ac, 2026-07-30-dated entry at DECISIONS.md:7-28, "Ratified by the operator 2026-07-30") | Replace lines 71-72 "Proposed decision of record (operator-ratified, docs/design/DECISIONS.md\nonce that tree exists):" with "Decision of record (operator-ratified, recorded in docs/design/DECISIONS.md\n2026-07-30):" |
| docs/plans/2026-07-30-worker-auth-central-refresh.md:4 | "Status: complete — merged to main 2026-07-30; decision of record ratified" (line changed 35f1e06 2026-07-30) | Supported in-repo: merge 5c9fe36 (2026-07-30), ratification commit 78d91ac, plan-complete commit 35f1e06. CONFLICT with queue archive /home/potterzot/workspace/queue/archive/orchestra.md:305: issue #014 "Superseded 2026-07-30 ... archived as closed-outside-process, not as a verified match to this issue's original spec" — the archive entry predates/ignores the feature-branch implementation the repo shows merged | No repo edit. Conflict reported for operator: queue-archive #014 closure note does not reflect the merged implementation (5c9fe36); queue files are orchestra-CLI-owned, reconcile there if desired |
| docs/plans/2026-07-30-worker-auth-central-refresh.md (whole file) | Complete plan still sits in docs/plans/ root; docs/plans/completed/ does not exist | `ls docs/plans/` shows no completed/; plan Status complete per 35f1e06 | `mkdir -p docs/plans/completed && git mv docs/plans/2026-07-30-worker-auth-central-refresh.md docs/plans/completed/` (combine with the rename in Mechanical finding 1) |

## 2. Mechanical findings

- Dashed-date filenames (convention is YYYYMMDD-slug.md):
  - docs/plans/2026-07-18-public-prep.md → `git mv docs/plans/2026-07-18-public-prep.md docs/plans/completed/20260718-public-prep.md`
  - docs/plans/2026-07-30-worker-auth-central-refresh.md → `git mv docs/plans/2026-07-30-worker-auth-central-refresh.md docs/plans/completed/20260730-worker-auth-central-refresh.md`
  - docs/notes/2026-07-30-refresh-trigger-spike.md → `git mv docs/notes/2026-07-30-refresh-trigger-spike.md docs/notes/20260730-refresh-trigger-spike.md`
  - Renames orphan these path references, which must be updated in the same commit: docs/plans/2026-07-30-worker-auth-central-refresh.md:158, docs/notes/2026-07-30-refresh-trigger-spike.md:4, docs/design/DECISIONS.md:20-21 (DECISIONS.md is operator-ratified-only — that two-line path fix needs operator sanction), and queue archive references at /home/potterzot/workspace/queue/archive/orchestra.md:177,290 (queue files are orchestra-CLI-owned; do not hand-edit).
- Untracked or uncommitted files under docs/: none (`git status --short -- docs/` clean).
- Path references resolving to no file: none. README.md:81-82,107 (`queue/`, `projects/`) and the `.claude/`-relative paths in docs/notes/2026-07-30-refresh-trigger-spike.md describe the runtime workspace and harness home, not repo files. All four markdown links (README.md:178,194,195,223) resolve to existing protocol/*.md files.
- Layout, operator decisions (not violations): docs/specs/ absent (compliant); protocol/ (6 .md files: CLAUDE-RELIABILITY, CODEX-RELIABILITY, HARNESS-RELIABILITY, ISSUE-GUIDE, OPERATIONS, STATES) and prompts/ (validator.md, verify-review.md, worker.md) are product/runtime docs outside docs/ and manual/ — README links protocol/ directly, so this looks intentional; src/orchestra/ORCHESTRA.md is package data read by src/orchestra/cli.py:56, a runtime asset, not a project doc.
- Root-level docs are within the sanctioned set (README.md, AGENTS.md only).

## 3. Unverified claims

- docs/plans/2026-07-30-worker-auth-central-refresh.md:109-124 — the six `[x]` acceptance boxes individually. Implementation and test commits exist (8462f0d, 1290030, 6f3d81d, 8e2cc6a, f8c4faa) and the merge is on main (5c9fe36), but the test suite / ruff / mypy gates were not re-run in this sweep, and queue archive :305 states the acceptance items were never run through validator/worker/verifier.
- queue archive /home/potterzot/workspace/queue/archive/orchestra.md:188 "gates green" for #009 (pytest/ruff/mypy at merge time) — recorded by the worker report, not independently re-run here.

## 4. Not checked

- ROADMAP.md, HANDOFF.md, root TODO.md — absent from repo.
- docs/notes/*handoff*.md substitute — none exists.
- docs/review/ — directory absent.
- Cross-repo sibling-project claims — no doc claim cites a path under another /home/potterzot/workspace/projects/ repo.
- Open orchestra queue file /home/potterzot/workspace/queue/orchestra.md — empty (no open orchestra issues); `orchestra issue list` shows none for this project.
