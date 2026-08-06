# Manual archive: `orchestra archive` and `orchestra drop`

Status: implemented 2026-08-06 on branch `archive-command` (e726f37, bec5a57); not merged
Date: 2026-08-06

## Goal

Give the operator a supported way to retire a live issue that the loop will never
finish, and record why. Two cases, two verbs:

- `orchestra archive` — the work landed outside the loop (done by hand on the
  project branch, or merged manually).
- `orchestra drop` — the issue is moot: superseded, obsolete, or not worth doing.
  Nothing landed.

Today the only path into `queue/archive/<project>.md` is `merge_and_archive`
(`src/orchestra/archive.py:81`), which requires `awaiting_review` plus a clean
merge of the issue branch. Everything else gets hand-edited into the archive file,
which the workspace convention forbids and which silently skips the guards
(active-attempt check, worktree teardown, worktree-DB drop).

## Design

### New terminal status `dropped`

`archived` keeps its meaning: the work exists on the base branch. `dropped` means
the issue was retired without work landing. Both statuses live in
`queue/archive/<project>.md`; both keep their numbers reserved, since
`next_number` (`queue.py:46`) reads the archive file.

The distinction is load-bearing for dependencies: an `archived` number satisfies a
`Depends On`; a `dropped` number does not.

### New field `Archive-Reason`

A one-line issue field, rendered by `render_issue` and parsed by `parse_issue`
alongside the other `Key: value` lines. Not a `### ` section: the value is one
line, and a field needs no new section-state handling in the parser.

Rendered only when non-empty, after `Worker:`. Emitting it unconditionally would
add an empty `Archive-Reason: ` line to every live issue, so the next queue write
would churn every issue in every project.

`### Decisions` stays the agent's log. Operator rulings do not go in it.

`--reason` is required on both commands. An archive entry with no reason is the
artifact this work exists to stop producing.

### Command surface

```
orchestra archive <project> <number>... --reason "landed by hand in 8c25da7"
orchestra drop    <project> <number>... --reason "superseded by #112"
```

Several numbers per invocation, one shared reason. Each number is processed
independently: a refusal reports and moves on to the next; exit status is 1 if any
number failed, 0 otherwise.

### Guards, per number

1. The number exists in the live queue. Otherwise: report, skip.
2. No live handle for `issue_key(project, number)` in `.orchestra/workers.json`.
   Otherwise: report "kill and reconcile it first", skip. Mirrors `cmd_hold`
   (`cli.py:827`).
3. Status is not already `archived` or `dropped`. Otherwise: report, skip.

Every other live status is accepted, including `awaiting_review` — "I merged it
myself" is a real case.

### Side effects

- Write the archive file before removing the row from the live queue, so a crash
  between the two writes leaves the issue recoverable. Same ordering as
  `archive.py:87`.
- Remove the worktree, best-effort. It is derived; a failure warns and does not
  abort.
- Drop the worktree DB when `project.worktree_db` is set, same as `archive.py:99`.
- **Leave the branch.** Print its name and its unmerged commit count against
  `project.branch`. Deleting it could destroy work the operator has not read;
  cleanup is a one-line command they can run later.

### Dependency handling

`done_numbers` (`dispatch.py:170`) currently counts every archive-file row as
satisfied. It must exclude `dropped` rows. A sibling `dropped_numbers` exposes
them so `validate_structural` can tell "dropped" from "unknown".

`validate_structural` (`validate.py:40`) gains a `dropped_ids` parameter:

- A dep in `dropped_ids` resolves as *known* — no "references unknown issue #N".
- It adds its own reason: `Depends On references dropped issue #N`.

That covers issues validated after the drop. It does not cover an issue already at
`validated` when the drop happens: `reconcile` only re-validates issues at `open`
(`reconcile.py:288`), so such an issue would never be re-checked, would never get
a role from `role_for_issue` (`selection.py:102`), and would sit at `validated`
forever with nothing recorded — the exact silent stall `validate.py:81-86` was
written to prevent for archived deps.

So `drop` cascades: after archiving the issue, scan the same project's live queue
for issues listing that number in `Depends On` and `block_issue` each with
`depends on dropped #N: <reason>`. They surface in `orchestra status`; the operator
edits the dep or drops them too.

`archive` does not cascade. An archived dep is satisfied.

### Not in scope

- Branch deletion.
- Un-archiving. If a row is archived wrongly, edit the two queue files.
- Cross-project batching. One project per invocation.

## Acceptance criteria

Each box below names the test that covers it.

- [x] `orchestra archive <proj> <n>... --reason R` moves each live issue to
      `queue/archive/<proj>.md` with `Status: archived` and `Archive-Reason: R`,
      and removes it from the live queue.
      (`test_manual_archive.py::test_archive_writes_status_and_reason_and_removes_from_live`,
      `test_cli.py::test_archive_moves_issue_and_records_reason`)
- [x] `orchestra drop` does the same with `Status: dropped`.
      (`test_manual_archive.py::test_drop_writes_status_and_reason`)
- [x] `--reason` is required; omitting it is an argparse error on both commands.
      (`test_cli.py::test_archive_refuses_when_reason_is_missing`,
      `::test_drop_refuses_when_reason_is_missing`)
- [x] Both refuse a number with a live handle in `.orchestra/workers.json`, a
      number absent from the live queue, and a number already terminal; each
      refusal names the number and reason, other numbers in the same invocation
      still process, exit code is 1.
      (`test_manual_archive.py::test_refuses_live_handle`, `::test_refuses_unknown_number`,
      `::test_refuses_already_terminal`,
      `test_cli.py::test_archive_multiple_numbers_mixed_success_and_failure`)
- [x] The worktree DB is dropped when configured; a worktree-removal failure warns
      and the archive still completes.
      (`test_manual_archive.py::test_worktree_db_dropped_when_configured`,
      `::test_worktree_removal_failure_warns_and_still_completes`)
      **Gap:** no test exercises a *successful* worktree removal — only the failure
      path. The call is one line reusing `git_ops.remove_worktree`, already covered
      by `merge_and_archive`'s tests, so this is untested-by-inspection, not unknown.
- [x] The branch survives, and its name plus unmerged commit count against the
      project base is printed.
      (`test_manual_archive.py::test_branch_survives_and_reports_unmerged_count`,
      `::test_branch_never_cut_reports_zero_unmerged`)
- [x] `Archive-Reason` round-trips through `parse_issue`/`render_issue`; issue
      blocks written before this change (no such field) still parse; a live issue
      renders no such line at all.
      (`test_issue.py::test_archive_reason_round_trip`,
      `::test_legacy_block_without_archive_reason_defaults_empty`,
      `::test_live_issue_renders_no_archive_reason_line`)
- [x] `done_numbers` excludes `dropped` rows; an issue depending on a dropped
      number does not become dispatchable.
      (`test_dispatch.py::test_done_numbers_excludes_dropped_includes_archived`,
      `::test_dispatch_skips_issue_depending_on_dropped_number`)
- [x] `validate_structural` reports `Depends On references dropped issue #N` for a
      dropped dep, and still reports "unknown issue" for a number that exists
      nowhere.
      (`test_validate.py::test_dependency_on_dropped_issue_reports_dropped_not_unknown`,
      `::test_unknown_dependency_still_blocks_with_dropped`)
- [x] `drop` blocks every live issue in the project depending on the dropped
      number, with `depends on dropped #N: <reason>` as the blocked reason.
      (`test_manual_archive.py::test_cascade_dropped_blocks_dependents`,
      `::test_cascade_dropped_ignores_non_dependents`,
      `test_cli.py::test_drop_moves_issue_and_cascades_to_dependents`)
- [x] `archive` leaves dependents untouched and they remain dispatchable.
      (`test_cli.py::test_archive_leaves_dependents_untouched_and_dispatchable`)
- [x] `protocol/STATES.md` documents `dropped`, both commands, and the cascade;
      `src/orchestra/ORCHESTRA.md` lists both in its command block.
      (`test_docs_present.py` status list; text verified by inspection)
- [x] `uv run pytest` green, `uv run ruff check` clean.
      (516 passed, 1 skipped — the skip is pre-existing; ruff clean)

## Outcome

Implemented as specified; no design deviations. Two implementation choices the plan
left open: guard refusals raise `ValueError` from `manual_archive` and are caught in
the CLI's per-number loop (mirrors `merge_and_archive`/`cmd_approve`), and
`cascade_dropped` is a separate function called from `cmd_drop` rather than folded
into `manual_archive`, which keeps `manual_archive` symmetric between the two verbs.

Out of scope and still out: branch deletion, un-archiving, cross-project batching.

## Steps

1. **Status and field.** Add `dropped` to `KNOWN_STATUSES` and
   `archive_reason: str = ""` to `Issue`; parse and render `Archive-Reason`.
   Check: `test_issue.py` round-trip with and without the field.

2. **`manual_archive.py`.** One function taking root, project, number, target
   status, and reason; performs the guards, the archive-then-remove write pair,
   the worktree and DB teardown, and returns what the caller must print about the
   surviving branch. Check: unit tests against a fixture workspace covering each
   guard and the crash-safe write order.

3. **Dependency plumbing.** `dispatch.done_numbers` excludes `dropped`; add
   `dispatch.dropped_numbers`; `validate_structural` takes `dropped_ids` and emits
   the dropped-dep reason; thread it through the two call sites
   (`dispatch.py:441`, `reconcile.py:292`). Check: `test_validate.py` for both dep
   cases, `test_dispatch.py` for non-dispatchability.

4. **Cascade.** `drop` blocks live dependents through `block_issue`.
   Check: a fixture queue where #2 depends on #1; drop #1, assert #2 is `blocked`
   with the reason naming #1.

5. **CLI.** `cmd_archive` and `cmd_drop`, both `nargs='+'` on number, both
   `--reason` required, per-number error accumulation, exit 1 on any failure.
   Check: `test_cli.py` for success, mixed success/failure exit code, and the
   argparse error when `--reason` is missing.

6. **Docs.** `protocol/STATES.md` status table, lifecycle, and a paragraph on the
   two verbs and the cascade; `ORCHESTRA.md` command block; add `dropped` to the
   `STATUSES` list in `test_docs_present.py`.
   Check: `uv run pytest tests/test_docs_present.py`.

7. **Full gate.** `uv run pytest && uv run ruff check`.
