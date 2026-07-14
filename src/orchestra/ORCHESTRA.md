# Working with orchestra

orchestra runs work across projects unattended. This is how to hand it work and act on
results from a project session. Install the public engine checkout with
`uv tool install --force --editable /path/to/orchestra`, then select the separate workspace
with `orchestra workspace set PATH`, set `ORCHESTRA_ROOT`, or pass `--root`.

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
from the base branch (pass `--force` to add anyway). Mark work that uses external data or
services with `Network: true`; this is visible advisory metadata and is dispatchable by
default. Installations that require explicit approval for every network issue can set
`hold_network_issues: true` in `config.yaml`; those issues validate to `held` and wait for
`orchestra release <project> <n>`. At run time the flag is not a network jail.
`sandbox.enabled` confines the filesystem but shares the network because an agent must
reach its own model API.

## Check status / act
    orchestra status                       # what's running, slots, counts
    orchestra issue list --status awaiting_review
    orchestra issue show <project> <n>     # full issue + decisions + diff pointer
    orchestra approve <project> <n>        # merge + archive an awaiting_review issue
    orchestra reject  <project> <n> --note "why"   # awaiting_review -> needs_rework; blocked -> open
    orchestra release <project> <n>        # release an opt-in/legacy held issue
    orchestra logs <project> <n> -f        # watch a worker

`reject` is state-sensitive: it sends reviewed work back for revision, while a blocked
issue is reopened at `open` for normal validation on the next host-level tick. It does not
dispatch a worker itself.

## Execution environment
The host scheduler owns execution. From Herdr or another confined agent session, use
read-only commands (`guide`, `status`, `issue list`, `issue show`, `logs`, and `diff`) and
queue-only controls (`issue add`, `reject`, and `release`). These commands do not launch a
worker.

**Do not run `orchestra tick`, `orchestra dispatch`, or `orchestra reconcile` from
Herdr.** Run execution commands from an external host shell, or leave them to the host
scheduler. Supervised workers use a transient user systemd service as their verified outer
filesystem boundary; a confined session may not have the host user-manager authority or
filesystem view needed to create it. Run engine Git/worktree
commands (`approve` and `retry-merge`) from the external host shell as well.

Run `orchestra <command> --help` for details.

## Worktree data
Each issue inherits its project's `Worktree-Seed` entries from the workspace's
`PROJECTS.md`; issues do not duplicate them. Entries are comma-separated: `path` copies a
seed, `path:link` creates a writable symlink, and `path:ro-link` shares the project source
read-only inside workers and verifiers. An untracked destination is a symlink; an existing
tracked path is protected read-only by the outer systemd boundary. A missing source fails
dispatch. `orchestra issue show <project> <n>` prints the effective seed list.

## Wiring a project (one-time)
Add ONE line to the project's `AGENTS.md`:

    If working with orchestra, run `orchestra guide`.

That's the only per-project change. Remove it to opt the project out of orchestra.
