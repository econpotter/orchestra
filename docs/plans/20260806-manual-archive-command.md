# Manual archive: `orchestra archive` and `orchestra drop`

Status: approved, not started
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

- [ ] `orchestra archive <proj> <n>... --reason R` moves each live issue to
      `queue/archive/<proj>.md` with `Status: archived` and `Archive-Reason: R`,
      and removes it from the live queue.
- [ ] `orchestra drop` does the same with `Status: dropped`.
- [ ] `--reason` is required; omitting it is an argparse error on both commands.
- [ ] Both refuse a number with a live handle in `.orchestra/workers.json`, a
      number absent from the live queue, and a number already terminal; each
      refusal names the number and reason, other numbers in the same invocation
      still process, exit code is 1.
- [ ] The worktree is removed and the worktree DB dropped (when configured); a
      worktree-removal failure warns and the archive still completes.
- [ ] The branch survives, and its name plus unmerged commit count against the
      project base is printed.
- [ ] `Archive-Reason` round-trips through `parse_issue`/`render_issue`; issue
      blocks written before this change (no such field) still parse.
- [ ] `done_numbers` excludes `dropped` rows; an issue depending on a dropped
      number does not become dispatchable.
- [ ] `validate_structural` reports `Depends On references dropped issue #N` for a
      dropped dep, and still reports "unknown issue" for a number that exists
      nowhere.
- [ ] `drop` blocks every live issue in the project depending on the dropped
      number, with `depends on dropped #N: <reason>` as the blocked reason.
- [ ] `archive` leaves dependents untouched and they remain dispatchable.
- [ ] `protocol/STATES.md` documents `dropped`, both commands, and the cascade;
      `src/orchestra/ORCHESTRA.md` lists both in its command block.
- [ ] `uv run pytest` green, `uv run ruff check` clean.

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
