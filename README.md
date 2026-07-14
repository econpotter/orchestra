# Orchestra

Orchestra runs development work across many repositories unattended. You plan and promote
issues; Orchestra dispatches a validator, worker, and verifier on a schedule, then asks for
human approval or intervention when policy requires it.

This repository contains the public Orchestra engine, CLI, protocol, prompts, and scheduler
units. It is deliberately separate from an Orchestra workspace, which contains private
project registrations, queues, configuration, and runtime state.

Claude and Codex are the supported agent harnesses. A `PiJsonAdapter` is specified at the
protocol level so the architecture remains harness-neutral, but the Pi harness implementation
is deferred to a later feature.

## Install from source

Clone with either SSH or HTTPS:

```sh
git clone git@github.com:econpotter/orchestra.git
# or: git clone https://github.com/econpotter/orchestra.git
cd orchestra
```

For development, install the locked environment and run the CLI from the checkout:

```sh
uv sync
uv run pytest
uv run orchestra --help
```

To put `orchestra` on your `PATH`, install an editable tool from this exact checkout:

```sh
uv tool install --force --editable "$PWD"
```

The editable installation keeps the executable tied to this checkout. Confirm the executable
before operating a workspace:

```sh
command -v orchestra
orchestra --help
```

## Create a separate workspace

Do not put operational data in this repository. Create a separate directory for it; the path
can be anywhere:

```sh
mkdir -p ~/workspace/{projects,queue,prompts,.orchestra}
cp config.example.yaml ~/workspace/config.yaml
cp prompts/*.md ~/workspace/prompts/
printf '# Projects\n' > ~/workspace/PROJECTS.md
orchestra workspace set ~/workspace
orchestra workspace show
```

The workspace owns:

- `config.yaml`: harness, role, workflow, review, and concurrency policy;
- `PROJECTS.md`: registered repositories and their queue paths;
- `queue/`: Orchestra-managed issue state;
- `projects/`: project checkouts, when you choose to keep them under the workspace;
- `prompts/`: optional workspace overrides of the packaged prompts; and
- `.orchestra/`: runtime handles, logs, results, locks, and worktrees.

Register an existing repository, then verify the workspace can be read:

```sh
orchestra project add example --path projects/example --branch main \
  --purpose "Describe what this project owns"
orchestra status
orchestra guide
```

To scaffold instead, set `template_path` in the workspace's `config.yaml` to a compatible
project template and run:

```sh
orchestra new-project example --lang python --stage development
```

`new-project` creates the checkout beneath the workspace, initializes Git, adds the
`PROJECTS.md` entry, and creates its queue. The template is workspace data and is not bundled
into the Orchestra engine repository.

Paths in `PROJECTS.md` are resolved from the workspace. The engine checkout may live inside
the workspace (for example, `projects/orchestra`) without becoming the workspace root.
Workspace resolution order is explicit `--root`, `ORCHESTRA_ROOT`,
`~/.config/orchestra/settings.yaml`, then upward discovery from the current directory. Use
`orchestra workspace show` after moving either directory.

## Configure a harness

Start from `config.example.yaml`. Its provider argument vectors show a Codex setup; Claude
can be configured with its own executable, model, and prompt transport. Each role names the
harness configuration and model it should use. Harness executables must already be installed,
authenticated, and available on the scheduler's `PATH`.

A current Claude process configuration is:

```yaml
providers:
  claude:
    argv: ["claude", "-p", "--model", "{model}", "--dangerously-skip-permissions"]
    prompt: stdin
```

Set `roles.<role>.provider: claude` and choose the corresponding Claude model for each role.
The autonomy flag is appropriate only inside an execution boundary you trust; use the
workspace's `sandbox` configuration when filesystem confinement is required.

The planned supervised adapters, evidence contract, and current rollout status are documented
in [`protocol/HARNESS-RELIABILITY.md`](protocol/HARNESS-RELIABILITY.md). Do not configure Pi
as if it were an implemented, verified harness.

## Run Orchestra

Create work in a registered project, then promote it:

```sh
orchestra issue add example --title "Implement the change" \
  --plan docs/plans/change.md \
  --accept "The requested behavior is covered by tests"
orchestra tick
orchestra status
```

The plan or spec must be committed to the project’s base branch before dispatch. See
[`protocol/ISSUE-GUIDE.md`](protocol/ISSUE-GUIDE.md) for issue-writing guidance and
[`protocol/STATES.md`](protocol/STATES.md) for lifecycle semantics.

Key commands include `issue add`, `issue list`, `issue show`, `status`, `logs`, `diff`,
`approve`, `reject`, `kill`, `project add`, `pause`, `resume`, `dispatch`, `reconcile`, and
`tick`. Run `orchestra guide` or `orchestra <command> --help` for the live interface.

## Install the scheduler

The shipped systemd user units call the installed CLI, which resolves the workspace saved by
`orchestra workspace set`:

```sh
mkdir -p ~/.config/systemd/user
cp systemd/orchestra.service systemd/orchestra.timer ~/.config/systemd/user/
loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable --now orchestra.timer
systemctl --user start orchestra.service
systemctl --user status orchestra.service orchestra.timer
journalctl --user -u orchestra.service -n 50
```

Before relying on unattended runs, confirm that the `PATH` in
`~/.config/systemd/user/orchestra.service` includes `orchestra`, the selected harness CLI,
and tools used by project checks. The one-shot service intentionally uses `KillMode=process`
so detached harness processes survive the tick that launched them. See
[`protocol/OPERATIONS.md`](protocol/OPERATIONS.md) for scheduler details and a cron fallback.

## Workflow and policy

- A tick runs `dispatch` and then `reconcile`; reconciliation is the sole automated
  lifecycle writer, while explicit operator commands perform intentional transitions.
- Structural validation is deterministic by default. Set `validate.semantic: true` to add an
  LLM validator stage.
- `review.autoapprove: false` stops verifier-approved work for human review; `true` merges it
  during reconciliation.
- `slots` limits in-flight Orchestra roles, not subagents created inside a harness run.
- `pause` and `resume` control new dispatch without stopping in-flight agents.
- `Worktree-Seed` in `PROJECTS.md` supports `path` (copy), `path:link` (writable symlink),
  and `path:ro-link` (Bubblewrap-enforced read-only sharing).

The public engine does not own any particular workspace's queue, project portfolio, private
notes, local prompts, or agent instructions. Keep those in the workspace and contribute only
reusable engine behavior and documentation here.
