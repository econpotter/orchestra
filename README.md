# orchestra

Run development work across many projects unattended. You plan + promote issues; orchestra
dispatches per-issue agents (validator → worker → verifier) on a schedule, and pulls you in
async to approve or unblock. The engine is provider-agnostic (Claude / Codex / pi).

## Install
```
uv sync                       # dev
uv tool install ~/orchestra   # puts `orchestra` on your PATH
orchestra workspace set ~/orchestra-workspace
```

When iterating on the CLI locally, prefer `uv run orchestra ...` from this checkout. In
this environment, `uv tool install .` can reuse a stale artifact when the package version
is unchanged, so the installed `orchestra` executable may not reflect your latest edits.
If you need to refresh the global tool from local changes, build a wheel and install that:
`uv build --wheel -o /tmp/orchestra-dist && uv tool install --force /tmp/orchestra-dist/orchestra-0.1.1-py3-none-any.whl`

**Code and workspace are separate.** The workspace contains `config.yaml`, `prompts/`,
`PROJECTS.md`, `queue/`, `projects/`, and `.orchestra/`. Resolution order is explicit
`--root`, `ORCHESTRA_ROOT`, `~/.config/orchestra/settings.yaml`, then upward discovery from
the current directory. Run `orchestra workspace show` to inspect it or `orchestra workspace
set PATH` to change the durable default after moving the directory.

## Workflow
0. Create a project: `orchestra new-project <name> --lang python|r [--stage alpha|beta|production]`
   — scaffolds from the template (`config.template_path`), `git init`s, and registers it
   (PROJECTS.md + Workflow key + lifecycle stage + empty queue). Keep the template clone
   current with `git -C projects/project-template pull`.
1. Plan in a project (`docs/plans/…`), then promote:
   `orchestra issue add <project> --title "…" --plan docs/plans/x.md --accept "…"`
   (or `--from-plan docs/plans/x.md --apply` to split a whole plan).
2. Let it run: `orchestra tick` by hand, or the systemd timer (`systemd/`).
3. Check in: `orchestra status`, `orchestra issue list --status awaiting_review`.
4. Review + act: `orchestra issue show <project> <n>`, `orchestra diff …`,
   then `orchestra approve <project> <n>` (or `reject --note …`, `kill`).

If you want verifier-approved work to merge without a human `approve`, set
`review.autoapprove: true` in `config.yaml`. That policy is applied by `reconcile`, so it
takes effect on the next tick.

## Commands
`orchestra guide` lists them; `orchestra <cmd> --help` for each. Key ones: `issue add`,
`issue list`, `issue show`, `status`, `logs`, `approve`, `reject`, `kill`, `project add`,
`pause`/`resume`, `dispatch`/`reconcile`/`tick`.

## Tuning
- **Concurrency** — `config.yaml` `slots:` (default 5): the global cap on in-flight agents
  across all projects and roles (a running validator/worker/verifier each holds one slot).
  Edit it; takes effect **next tick** (no restart — `dispatch` re-reads `config.yaml`). Note:
  a worker's internal subagents aren't counted, so real process count can exceed `slots`,
  and each worker slot is an opus-class run — size accordingly (target 4–6 *issues*).
- **Review policy** — `config.yaml` `review.autoapprove:` (default `false`) controls whether
  `awaiting_review` issues stop for a human `orchestra approve` or are auto-merged by
  reconcile. Like the other engine config, it takes effect on the next tick; no systemd
  restart is needed.
- **Cadence** — the systemd timer's `OnCalendar` in `~/.config/systemd/user/orchestra.timer`
  (default `*:0/15`). Change → `systemctl --user daemon-reload && systemctl --user restart
  orchestra.timer`. End-to-end latency ≈ stages × interval; the tick is cheap, so tightening
  is fine. (crontab fallback: the `*/N` field.) Concurrency lives in engine config; cadence
  lives in the scheduler — by design.
- **Pause** — `orchestra pause` / `resume` stops/resumes launching new work without touching
  the timer (in-flight agents finish).

## Concepts
- **Queue** (`queue/<project>.md`) — orchestra-owned, human-editable; the boundary between
  planning (left) and agent execution (right).
- **States** — open → validated → in_progress → committed → awaiting_review → merged →
  archived, plus blocked / needs_rework. See `protocol/STATES.md`.
- **Per project** — add one line to its `AGENTS.md`: *"If working with orchestra, run
  `orchestra guide`."* Remove it to opt the project out.
- **Worktree data** — declare comma-separated `Worktree-Seed` entries in the workspace's
  `PROJECTS.md`. Use `path` to copy, `path:link` for a writable symlink, or
  `path:ro-link` to share data while enforcing read-only access inside agents (`bwrap`
  required). Issues inherit the project declaration automatically.

See `ORCHESTRA.md` (operating manual) and `docs/superpowers/specs/` (design) for depth.
Harness supervision and structured attempt evidence are specified in
[`protocol/HARNESS-RELIABILITY.md`](protocol/HARNESS-RELIABILITY.md).
