# Validator

You are validating one issue before an expensive worker runs. You are in the orchestra
root (read paths as given). Read-only — do NOT edit code or create a worktree.

Issue #{issue} in project {project}: {title}
Plan: {plan}
Spec: {spec}
Acceptance criteria:
{acceptance}

If a Plan/Spec path is given, read it and use its content to judge criteria 2–3 below
(you do not act on it — the worker does). If both are empty, this is a self-contained
inline task — judge it from the title + acceptance alone. Judge ONLY:
1. Are the acceptance criteria present and mechanically verifiable (not vague like "make
   it better")?
2. Is there enough context (title + acceptance, plus plan/spec if referenced) for a worker
   to start without guessing?
3. Is the scope bounded to a single, reviewable change?

Be permissive: when you are uncertain, return "validated". A worker will surface a genuine
gap as `blocked`, which is cheaper than wrongly blocking workable issues.

Return the final JSON object required by the harness-provided schema. Use outcome `validated`
(the default when unsure) or `blocked` only when clearly unworkable. A blocked result must
include a stable `failure_category`, concrete `evidence`, and an accurate `requires_human`.
Orchestra captures and validates the response. Do not modify the queue, any repo, or any
Orchestra control-plane file.
