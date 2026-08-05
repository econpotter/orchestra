# TODO — orchestra

## A rework loses what the previous attempt discovered

**Observed 2026-08-04**, twice, in the statewater run (13 issues, 4 waves).

When a branch conflicts with `main`, the verifier's remedy is:

> rebase: your branch conflicts with 'main' (another issue merged since you
> branched). Re-implement your plan on top of current 'main' and regenerate any
> derived artifacts so it merges clean.

The worker starts fresh from the plan. It re-derives everything the plan
specifies and the acceptance criteria enforce. It does **not** re-derive what
the previous attempt found out along the way, because nothing asks it to.

Two losses in one run:

- `statewater#008` wrote a 43-line answer into
  `docs/plans/20260804-co-c2-timeseries.md` — that `structures/divrec/waterclasses`
  is required to interpret `diversion_series`, because divrec rows are keyed by
  a water class the response identifies and never describes. The reworked diff
  was three files; the plan doc was not among them.
- `statewater#007` wrote two corrections into `docs/design/api-contract.md`
  during its first rework. The final diff was three files; the contract was not
  among them.

`#007` is the clean illustration of the mechanism: the *code* implementing the
correction came back on the rework, because tests drive it. Only the sentence
describing it was lost. So this is not about documentation as a category — it
is **anything with no test or criterion behind it**. Documentation is just the
usual victim, since docs are not executable.

What made it recoverable this time: the findings were also in each issue's
`### Decisions` field, which survives a rework, and a human-supervised reviewer
was reading those fields and re-entering the content by hand. Unattended, both
findings would have vanished from the repository with no trace — the only
symptom being `diversion_series` rows referencing a key nothing explains.

Worth considering:

- Carry the previous attempt's non-code changes into the rework prompt, or at
  least the `### Decisions` text, so the worker knows what it found last time.
- Or have the rework instruction name the files the prior attempt touched
  outside its owned set, and ask the worker to reapply them deliberately.
- Or surface a warning at merge when a reworked diff drops a path the prior
  attempt had modified.

## Related, from the same run

- **Rebase churn serializes a wave.** Four issues each registered a module in
  one shared `__init__.py`, so every merge invalidated the others' branches and
  they ping-ponged through rework. It took holding one issue to break the loop.
  Worth thinking about whether the merge could resolve a non-overlapping
  rebase, or whether the queue should serialize issues known to share a file.
- **`orchestra hold` will not touch an `in_progress` or `committed` issue**, so
  the only way to stop a loop is to catch an issue while it is `needs_rework`.
- **`orchestra harness doctor` cannot report auth health while a worker is
  active** — it returns `login: not_checked_workers_active`, `ready: False`.
  During a long run that is almost always, so the one moment you want to know
  whether the token is dead is the moment you cannot ask.
