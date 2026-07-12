from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from orchestra import git_ops, layout
from orchestra.archive import merge_and_archive
from orchestra.config import load_config
from orchestra.dashboard import summarize
from orchestra.dispatch import dispatch as _dispatch
from orchestra.issue import (
    AcceptanceItem,
    Issue,
    _parse_depends,
    block_issue,
    branch_name,
    exception_detail,
)
from orchestra.projects import find_project, read_projects
from orchestra.queue import find_issue, read_queue, write_queue
from orchestra.reconcile import reconcile as _reconcile
from orchestra.registry import issue_key, load_registry, save_registry
from orchestra.scaffold import new_project
from orchestra.workspace import WorkspaceError, resolve_workspace, save_workspace_setting


def _resolve_project(args):
    """Return the registered Project, or None (after printing an error) if unknown."""
    project = find_project(read_projects(Path(args.root) / "PROJECTS.md"), args.project)
    if project is None:
        print(f"project {args.project!r} not registered in PROJECTS.md", file=sys.stderr)
    return project


def cmd_guide(args: argparse.Namespace) -> int:
    text = resources.files("orchestra").joinpath("ORCHESTRA.md").read_text()
    print(text)
    return 0


def cmd_workspace_show(args: argparse.Namespace) -> int:
    try:
        print(resolve_workspace(args.root))
        return 0
    except WorkspaceError as exc:
        print(f"workspace error: {exc}", file=sys.stderr)
        return 2


def cmd_workspace_set(args: argparse.Namespace) -> int:
    try:
        print(save_workspace_setting(args.path))
        return 0
    except WorkspaceError as exc:
        print(f"workspace error: {exc}", file=sys.stderr)
        return 2


def _all_issues(root: Path):
    out = []
    pf = root / "PROJECTS.md"
    if pf.exists():
        for project in read_projects(pf):
            qf = layout.queue_file(root, project.name)
            if qf.exists():
                for issue in read_queue(qf):
                    out.append((project.name, issue))
    return out


def _issue_list_rows(all_issues, *, project=None, status=None):
    # Surface a dependent that is silently stuck behind a blocked dependency (deps clear
    # only when archived, so a blocked dep would otherwise leave the dependent waiting
    # forever with no signal).
    status_by = {(p, i.number): i.status for p, i in all_issues}
    rows = []
    for p, i in all_issues:
        if (project is not None and p != project) or (status is not None and i.status != status):
            continue
        blocked_deps = [d for d in i.depends_on if status_by.get((p, d)) == "blocked"]
        rows.append(
            {"project": p, "number": i.number, "status": i.status,
             "priority": i.priority, "title": i.title, "blocked_deps": blocked_deps}
        )
    rows.sort(key=lambda r: (r["project"], r["number"]))
    return rows


def _print_issue_row(row):
    note = ""
    if row["blocked_deps"]:
        deps = ", ".join(f"#{d}" for d in row["blocked_deps"])
        note = f"  ⚠ blocked dep {deps}"
    print(
        f"{row['project']}#{row['number']:03d}  {row['status']:<15} "
        f"P{row['priority']}  {row['title']}{note}"
    )


def cmd_issue_list(args: argparse.Namespace) -> int:
    root = Path(args.root)
    rows = _issue_list_rows(list(_all_issues(root)), project=args.project, status=args.status)
    if args.json:
        print(json.dumps(rows))
    else:
        for r in rows:
            _print_issue_row(r)
    return 0


def cmd_issue_show(args: argparse.Namespace) -> int:
    root = Path(args.root)
    project = _resolve_project(args)
    if project is None:
        return 2
    qf = layout.queue_file(root, args.project)
    issue = find_issue(read_queue(qf), args.number) if qf.exists() else None
    if issue is None:
        print(f"issue #{args.number} not found in {args.project}", file=sys.stderr)
        return 2
    info = {
        "project": args.project, "number": issue.number, "title": issue.title,
        "status": issue.status, "priority": issue.priority, "plan": issue.plan,
        "spec": issue.spec, "depends_on": issue.depends_on, "retries": issue.retries,
        "acceptance": [{"checked": a.checked, "text": a.text} for a in issue.acceptance],
        "decisions": issue.decisions, "blocked_reason": issue.blocked_reason,
        "verifier_feedback": issue.verifier_feedback,
        "branch": branch_name(issue),
        "worktree": str(layout.worktree_dir(root, args.project, issue.number)),
    }
    if args.json:
        print(json.dumps(info))
    else:
        from orchestra.issue import render_issue
        print(render_issue(issue))
        print(f"\nbranch:   {info['branch']}")
        print(f"worktree: {info['worktree']}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    root = Path(args.root)
    s = summarize(Path(args.root))
    if args.json:
        print(json.dumps(s))
    else:
        print(f"slots used: {s['slots_used']}")
        print("counts: " + ", ".join(f"{k}={v}" for k, v in sorted(s["counts"].items())))
        for row in _issue_list_rows(list(_all_issues(root))):
            _print_issue_row(row)
    return 0


def _planspec_on_base(root: Path, project, refs, *, force: bool) -> bool:
    """A worker branches off `project.branch`, so a referenced Plan/Spec must already be
    committed to that branch's tree — an uncommitted planner file is invisible to it and
    only surfaces (as `invalid`) hours later at reconcile. Refuse at queue time instead.
    `--force` downgrades the refusal to a warning for the deliberate commit-the-plan-next
    workflow. Returns True when the add may proceed."""
    repo = root / project.path
    missing = [
        ref.split("#", 1)[0]
        for _, ref in refs
        if ref and not git_ops.file_in_branch(repo, project.branch, ref.split("#", 1)[0])
    ]
    if not missing:
        return True
    joined = ", ".join(missing)
    if force:
        print(
            f"warning: not on base branch '{project.branch}': {joined} "
            f"(--force: adding anyway)",
            file=sys.stderr,
        )
        return True
    print(
        f"refusing: plan/spec not committed to base branch '{project.branch}': {joined}\n"
        f"commit the plan/spec to base first, or pass --force to add anyway.",
        file=sys.stderr,
    )
    return False


def cmd_issue_add(args: argparse.Namespace) -> int:
    root = Path(args.root)
    project = _resolve_project(args)
    if project is None:
        return 2
    from orchestra.planparse import propose_issues_from_plan
    from orchestra.queue import next_number
    qf = layout.queue_file(root, args.project)
    active = read_queue(qf) if qf.exists() else []
    af = layout.archive_file(root, args.project)
    archived = read_queue(af) if af.exists() else []

    if args.from_plan:
        if args.title:
            print("note: --title is ignored when --from-plan is provided", file=sys.stderr)
        proposals = propose_issues_from_plan(args.from_plan, args.project)
        if args.apply and not _planspec_on_base(
            root, project, [("Plan", str(args.from_plan))], force=args.force
        ):
            return 2
        if not args.apply:
            print(f"# {len(proposals)} proposed issues (dry-run; pass --apply to write):")
            for p in proposals:
                print(f"- {p['title']}  [{p['plan']}]")
            return 0
        new = []
        n = next_number(active, archived)
        for p in proposals:
            new.append(Issue(
                number=n, project=args.project, title=p["title"], status="open",
                priority=args.priority, plan=p["plan"], spec=None, depends_on=[],
                retries=0, worker=None, acceptance=[], decisions="",
                blocked_reason="", verifier_feedback="",
            ))
            n += 1
        qf.parent.mkdir(parents=True, exist_ok=True)
        write_queue(qf, active + new)
        print(f"added {len(new)} issues to {args.project}")
        return 0

    if not args.title:
        print("issue add requires --title (or --from-plan)", file=sys.stderr)
        return 2
    if not _planspec_on_base(
        root, project, [("Plan", args.plan), ("Spec", args.spec)], force=args.force
    ):
        return 2
    try:
        depends_on = _parse_depends(args.depends_on or "")
    except ValueError as exc:
        print(f"error: --{exc}", file=sys.stderr)
        return 2
    number = next_number(active, archived)
    issue = Issue(
        number=number, project=args.project, title=args.title, status="open",
        priority=args.priority, plan=args.plan, spec=args.spec,
        depends_on=depends_on,
        retries=0, worker=None,
        acceptance=[AcceptanceItem(checked=False, text=t) for t in (args.accept or [])],
        decisions="", blocked_reason="", verifier_feedback="",
    )
    qf.parent.mkdir(parents=True, exist_ok=True)
    write_queue(qf, active + [issue])
    print(f"added {args.project}#{number:03d}")
    return 0


def cmd_project_add(args: argparse.Namespace) -> int:
    root = Path(args.root)
    pf = root / "PROJECTS.md"
    block = (
        f"\n## {args.name}\n- Path: {args.path}\n- Branch: {args.branch}\n"
        f"- Purpose: {args.purpose}\n- Queue: queue/{args.name}.md\n- Focus: none\n"
    )
    existing = pf.read_text() if pf.exists() else "# Projects\n"
    pf.write_text(existing.rstrip() + "\n" + block)
    print(f"registered {args.name}")
    return 0


def _merge_settings(root: Path) -> tuple[int, Path | None]:
    """(rebase_cap, merge tmpdir) from config.yaml — mirrors what reconcile's autoapprove
    passes, so a manual approve/retry-merge uses the same disk-backed merge tmpdir."""
    cfg_path = root / "config.yaml"
    if not cfg_path.exists():
        return 2, None
    cfg = load_config(cfg_path)
    tmpdir = (root / cfg.merge_tmpdir) if cfg.merge_tmpdir else None
    return cfg.retries_cap, tmpdir


def cmd_approve(args: argparse.Namespace) -> int:
    root = Path(args.root)
    project = _resolve_project(args)
    if project is None:
        return 2
    try:
        cap, tmpdir = _merge_settings(root)
        result = merge_and_archive(root, project, args.number, rebase_cap=cap, tmpdir=tmpdir)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except subprocess.CalledProcessError as exc:
        print(f"git merge error: {exc.stderr.strip()}", file=sys.stderr)
        return 1
    if result == "blocked":
        print(
            f"{args.project}#{args.number:03d} still conflicts after repeated reworks "
            f"— blocked for a manual merge."
        )
        return 0
    if result == "reworked":
        print(
            f"{args.project}#{args.number:03d} conflicts with current {project.branch} "
            f"(another issue merged) — sent back to rework; it will re-run off the updated base."
        )
        return 0
    print(f"approved {args.project}#{args.number:03d}")
    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    root = Path(args.root)
    project = _resolve_project(args)
    if project is None:
        return 2
    qf = layout.queue_file(root, args.project)
    issues = read_queue(qf) if qf.exists() else []
    issue = find_issue(issues, args.number)
    if issue is None:
        print(f"issue #{args.number} not found", file=sys.stderr)
        return 2
    if issue.status == "awaiting_review":
        issue.status = "needs_rework"
    elif issue.status == "blocked":
        issue.status = "open"
    else:
        print(f"issue #{args.number} is {issue.status!r}; reject expects awaiting_review or blocked",
              file=sys.stderr)
        return 3
    if args.note:
        issue.verifier_feedback = (issue.verifier_feedback + "\n" + args.note).strip()
    write_queue(qf, issues)
    print(f"rejected {args.project}#{args.number:03d} -> {issue.status}")
    return 0


def cmd_retry_merge(args: argparse.Namespace) -> int:
    """Recovery path for issue #006: a blocked issue whose worker already committed and
    verifier already passed is re-driven straight to merge — WITHOUT re-running the worker
    (unlike `reject` blocked->open, which restarts the whole pipeline). Refuses when the
    branch has no committed work, since there is then nothing to merge."""
    root = Path(args.root)
    project = _resolve_project(args)
    if project is None:
        return 2
    qf = layout.queue_file(root, args.project)
    issues = read_queue(qf) if qf.exists() else []
    issue = find_issue(issues, args.number)
    if issue is None:
        print(f"issue #{args.number} not found", file=sys.stderr)
        return 2
    if issue.status != "blocked":
        print(f"issue #{args.number} is {issue.status!r}; retry-merge expects blocked",
              file=sys.stderr)
        return 3
    repo = root / project.path
    branch = branch_name(issue)
    if not git_ops.branch_exists(repo, branch) or not git_ops.commit_exists_on_branch(
        repo, branch, project.branch
    ):
        print(
            f"issue #{args.number} has no committed work on {branch} to re-merge — use "
            f"`orchestra reject {args.project} {args.number}` to reopen it instead",
            file=sys.stderr,
        )
        return 3
    # Flip back to awaiting_review (the only state merge_and_archive will act on) and drop
    # the stale block, then run the identical merge path autoapprove uses.
    issue.status = "awaiting_review"
    issue.blocked_reason = ""
    write_queue(qf, issues)
    try:
        cap, tmpdir = _merge_settings(root)
        result = merge_and_archive(root, project, args.number, rebase_cap=cap, tmpdir=tmpdir)
    except Exception as exc:  # noqa: BLE001 — mirror reconcile autoapprove: ANY failure after
        # the status flip must re-block loudly (never leave it awaiting_review, never crash).
        reason = f"retry-merge: merge failed: {exception_detail(exc)}"
        refreshed = read_queue(qf)
        current = find_issue(refreshed, args.number)
        if current is not None:
            block_issue(current, reason)
            write_queue(qf, refreshed)
        print(reason, file=sys.stderr)
        return 1
    if result == "blocked":
        print(f"{args.project}#{args.number:03d} still conflicts — blocked for a manual merge.")
        return 0
    if result == "reworked":
        print(f"{args.project}#{args.number:03d} conflicts with current {project.branch} "
              f"— sent back to rework.")
        return 0
    print(f"merged {args.project}#{args.number:03d}")
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    root = Path(args.root)
    project = _resolve_project(args)
    if project is None:
        return 2
    qf = layout.queue_file(root, args.project)
    issues = read_queue(qf) if qf.exists() else []
    issue = find_issue(issues, args.number)
    if issue is None:
        print(f"issue #{args.number} not found", file=sys.stderr)
        return 2
    if issue.status != "held":
        print(f"issue #{args.number} is {issue.status!r}; release expects held", file=sys.stderr)
        return 3
    issue.status = "validated"
    write_queue(qf, issues)
    print(f"released {args.project}#{args.number:03d} -> validated")
    return 0


def cmd_kill(args: argparse.Namespace) -> int:
    root = Path(args.root)
    project = _resolve_project(args)
    if project is None:
        return 2
    reg_path = root / ".orchestra" / "workers.json"
    reg = load_registry(reg_path)
    key = issue_key(args.project, args.number)
    handle = reg.get(key)
    if handle is not None:
        try:
            os.kill(handle.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        del reg[key]
        save_registry(reg_path, reg)
    qf = layout.queue_file(root, args.project)
    issues = read_queue(qf) if qf.exists() else []
    issue = find_issue(issues, args.number)
    if issue is not None:
        block_issue(issue, "killed by operator")
        write_queue(qf, issues)
    print(f"killed {args.project}#{args.number:03d}")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    root = Path(args.root)
    project = _resolve_project(args)
    if project is None:
        return 2
    qf = layout.queue_file(root, args.project)
    issue = find_issue(read_queue(qf), args.number) if qf.exists() else None
    if issue is None:
        print(f"issue #{args.number} not found", file=sys.stderr)
        return 2
    repo = root / project.path
    r = subprocess.run(
        ["git", "-C", str(repo), "diff", f"{project.branch}..{branch_name(issue)}"],
        capture_output=True, text=True,
    )
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    return r.returncode


def cmd_logs(args: argparse.Namespace) -> int:
    root = Path(args.root)
    project = _resolve_project(args)
    if project is None:
        return 2
    log = root / ".orchestra" / "logs" / f"{issue_key(args.project, args.number)}.log"
    if not log.exists():
        print(f"no log at {log}", file=sys.stderr)
        return 2
    if args.follow:
        return subprocess.run(["tail", "-f", str(log)]).returncode
    sys.stdout.write(log.read_text())
    return 0


def cmd_pause(args: argparse.Namespace) -> int:
    p = Path(args.root) / ".orchestra" / "paused"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("")
    print("paused (dispatch will launch nothing until resume)")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    p = Path(args.root) / ".orchestra" / "paused"
    if p.exists():
        p.unlink()
    print("resumed")
    return 0


def cmd_dispatch(args: argparse.Namespace) -> int:
    root = Path(args.root)
    cfg = load_config(root / "config.yaml")
    started = datetime.now(timezone.utc).isoformat()
    for key in _dispatch(root, cfg, started=started):
        print(f"launched {key}")
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    root = Path(args.root)
    cfg = load_config(root / "config.yaml")
    for key, status in _reconcile(root, cfg):
        print(f"{key} -> {status}")
    return 0


def cmd_tick(args: argparse.Namespace) -> int:
    # Always reconcile, even if dispatch reported a failure — reconcile must still reap
    # finished agents and advance the queue regardless of a dispatch-side problem.
    rc_dispatch = cmd_dispatch(args)
    rc_reconcile = cmd_reconcile(args)
    return rc_dispatch or rc_reconcile


def cmd_new_project(args: argparse.Namespace) -> int:
    root = Path(args.root)
    cfg = load_config(root / "config.yaml")
    try:
        dest = new_project(
            root, args.name, lang=args.lang, stage=args.stage,
            template_path=cfg.template_path,
        )
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"template not found: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        msg = (exc.stderr or b"").decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        print(f"scaffold failed: {msg.strip()}", file=sys.stderr)
        return 1
    print(f"created {dest} (registered in PROJECTS.md, queue created)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orchestra", description="orchestra control surface")
    parser.add_argument(
        "--root",
        default=None,
        help="workspace path (default: env, user settings, then upward discovery)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_guide = sub.add_parser("guide", help="print the project-integration guide")
    p_guide.set_defaults(func=cmd_guide)

    p_workspace = sub.add_parser("workspace", help="show or configure workspace path")
    workspace_sub = p_workspace.add_subparsers(dest="workspace_command", required=True)
    p_workspace_show = workspace_sub.add_parser("show", help="print resolved workspace")
    p_workspace_show.set_defaults(func=cmd_workspace_show)
    p_workspace_set = workspace_sub.add_parser("set", help="set default workspace path")
    p_workspace_set.add_argument("path")
    p_workspace_set.set_defaults(func=cmd_workspace_set)

    p_status = sub.add_parser("status", help="engine dashboard")
    p_status.add_argument("--json", action="store_true")
    p_status.set_defaults(func=cmd_status)

    p_issue = sub.add_parser("issue", help="issue commands")
    issue_sub = p_issue.add_subparsers(dest="issue_command", required=True)

    p_list = issue_sub.add_parser("list", help="list issues")
    p_list.add_argument("--project", default=None)
    p_list.add_argument("--status", default=None)
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_issue_list)

    p_show = issue_sub.add_parser("show", help="show one issue")
    p_show.add_argument("project")
    p_show.add_argument("number", type=int)
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_issue_show)

    p_add = issue_sub.add_parser("add", help="add an issue")
    p_add.add_argument("project")
    p_add.add_argument("--title", default=None)
    p_add.add_argument("--from-plan", dest="from_plan", default=None)
    p_add.add_argument("--apply", action="store_true")
    p_add.add_argument("--plan", default=None)
    p_add.add_argument("--spec", default=None)
    p_add.add_argument("--priority", type=int, default=5)
    p_add.add_argument("--accept", action="append", help="acceptance criterion (repeatable)")
    p_add.add_argument(
        "--depends-on", dest="depends_on", default=None,
        help="comma-separated issue numbers this issue depends on",
    )
    p_add.add_argument(
        "--force", action="store_true",
        help="add even if the plan/spec is not yet committed to the base branch (warns)",
    )
    p_add.set_defaults(func=cmd_issue_add)

    p_project = sub.add_parser("project", help="project commands")
    project_sub = p_project.add_subparsers(dest="project_command", required=True)
    p_padd = project_sub.add_parser("add", help="register a project")
    p_padd.add_argument("name")
    p_padd.add_argument("--path", required=True)
    p_padd.add_argument("--branch", default="main")
    p_padd.add_argument("--purpose", default="")
    p_padd.set_defaults(func=cmd_project_add)

    for name, fn in (("approve", cmd_approve), ("kill", cmd_kill)):
        pp = sub.add_parser(name, help=f"{name} an issue")
        pp.add_argument("project")
        pp.add_argument("number", type=int)
        pp.set_defaults(func=fn)

    p_reject = sub.add_parser("reject", help="bounce an issue back (awaiting_review->needs_rework, or blocked->open)")
    p_reject.add_argument("project")
    p_reject.add_argument("number", type=int)
    p_reject.add_argument("--note", default="")
    p_reject.set_defaults(func=cmd_reject)

    p_retry = sub.add_parser(
        "retry-merge",
        help="re-drive a blocked issue with committed work straight to merge (no worker rerun)",
    )
    p_retry.add_argument("project")
    p_retry.add_argument("number", type=int)
    p_retry.set_defaults(func=cmd_retry_merge)

    p_release = sub.add_parser("release", help="release a held Network issue (held->validated)")
    p_release.add_argument("project")
    p_release.add_argument("number", type=int)
    p_release.set_defaults(func=cmd_release)

    p_diff = sub.add_parser("diff", help="show an issue's branch diff")
    p_diff.add_argument("project")
    p_diff.add_argument("number", type=int)
    p_diff.set_defaults(func=cmd_diff)

    p_logs = sub.add_parser("logs", help="print/tail a worker log")
    p_logs.add_argument("project")
    p_logs.add_argument("number", type=int)
    p_logs.add_argument("-f", "--follow", action="store_true")
    p_logs.set_defaults(func=cmd_logs)

    for name, fn in (("pause", cmd_pause), ("resume", cmd_resume)):
        pp = sub.add_parser(name, help=f"{name} dispatch")
        pp.set_defaults(func=fn)

    for name, fn in (("dispatch", cmd_dispatch), ("reconcile", cmd_reconcile), ("tick", cmd_tick)):
        pp = sub.add_parser(name, help=f"engine: {name}")
        pp.set_defaults(func=fn)

    p_new = sub.add_parser("new-project", help="scaffold + register a new project")
    p_new.add_argument("name")
    p_new.add_argument("--lang", choices=["python", "r"], default="python")
    p_new.add_argument("--stage", choices=["development", "production"], default="development")
    p_new.set_defaults(func=cmd_new_project)

    return parser


def _hoist_root(argv: list[str]) -> list[str]:
    """Allow `--root` anywhere on the command line. It is a global option, so argparse
    requires it before the subcommand; users (and the systemd unit) naturally write
    `orchestra tick --root X`. Pull any `--root X` / `--root=X` out and prepend it."""
    out: list[str] = []
    root: str | None = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--root" and i + 1 < len(argv):
            root = argv[i + 1]
            i += 2
            continue
        if a.startswith("--root="):
            root = a.split("=", 1)[1]
            i += 1
            continue
        out.append(a)
        i += 1
    return (["--root", root] + out) if root is not None else out


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    args = build_parser().parse_args(_hoist_root(argv))
    if args.command not in {"guide", "workspace"}:
        try:
            args.root = str(resolve_workspace(args.root))
        except WorkspaceError as exc:
            print(f"workspace error: {exc}", file=sys.stderr)
            return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
