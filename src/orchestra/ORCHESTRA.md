# Working with orchestra

orchestra runs work across projects unattended. This is how to hand it work and act on
results from a project session. (Requires the `orchestra` CLI on PATH —
`uv tool install orchestra` — then `orchestra workspace set PATH`, set `ORCHESTRA_ROOT`,
or pass `--root`.)

## Starting a project
Create and register a new project:

    orchestra new-project <name> --lang python|r

## Promote work to the queue
After a plan/spec exists in this project, queue an issue:

    orchestra issue add <project> --title "..." --plan docs/plans/foo.md \
      --priority 2 --accept "criterion one" --accept "criterion two"

Or a self-contained task with no plan: drop `--plan`, rely on `--title` + `--accept`.
Split a whole plan into proposed issues (review before writing):

    orchestra issue add --from-plan docs/plans/foo.md <project>        # dry-run
    orchestra issue add --from-plan docs/plans/foo.md <project> --apply

**Commit the plan and spec to the project's base branch first.** A worker branches off
base and cannot see uncommitted planner files; `issue add` refuses a Plan/Spec missing
from the base branch (pass `--force` to add anyway). Mark heavy/irreversible network work
with `Network: true` in the issue — it validates to `held` and waits for
`orchestra release <project> <n>` before it can dispatch. The `Network` flag is a *dispatch
gate*; at run time it is advisory, not a network jail. `sandbox.enabled` confines the
filesystem (agents may write only their worktree) but shares the network, since an agent
must reach its own model API to run — so blocking a mass-fetch relies on the `held`/`release`
gate plus the agent prompt, not a run-time egress block.

## Check status / act
    orchestra status                       # what's running, slots, counts
    orchestra issue list --status awaiting_review
    orchestra issue show <project> <n>     # full issue + decisions + diff pointer
    orchestra approve <project> <n>        # merge + archive an awaiting_review issue
    orchestra reject  <project> <n> --note "why"   # bounce back for rework
    orchestra release <project> <n>        # release a held Network issue (held->validated)
    orchestra logs <project> <n> -f        # watch a worker

Run `orchestra <command> --help` for details.

## Worktree data
Each issue inherits its project's `Worktree-Seed` entries from the workspace's
`PROJECTS.md`; issues do not duplicate them. Entries are comma-separated: `path` copies a
seed, `path:link` creates a writable symlink, and `path:ro-link` shares the project source
read-only inside workers and verifiers. An untracked destination is a symlink; an existing
tracked path is used as the bind mount point. `ro-link` requires Bubblewrap (`bwrap`), and
a missing source fails dispatch. `orchestra issue show <project> <n>` prints the effective
seed list.

## Wiring a project (one-time)
Add ONE line to the project's `AGENTS.md`:

    If working with orchestra, run `orchestra guide`.

That's the only per-project change. Remove it to opt the project out of orchestra.
