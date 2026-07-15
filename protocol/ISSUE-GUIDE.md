# Issue Guide

Issues live in `queue/<project>.md`, one `## #NNN <project>: <title>` block each.
They are **thin pointers** — reference a Plan/Spec, never duplicate it.

## Required format
```
## #042 weather-api: add API retry
Status: open
Priority: 3
Plan: projects/weather-api/docs/plans/api-resilience.md#retry
Spec: projects/weather-api/docs/specs/2026-06-api-resilience.md
Depends On: null
Network: true
Network-Approved: false
Retries: 0
Worker: null
Acceptance:
- [ ] client retries 5xx with exponential backoff, max 3
- [ ] covered by tests; existing suite green
### Decisions
### Blocked Reason
```

## Field rules
- **Status** — one of the values in STATES.md. Humans create issues as `open` or explicitly
  `held`; held issues remain parked until `orchestra release` returns them to `open`.
- **Priority** — integer; lower dispatches first within a project.
- **Plan / Spec** — repo-relative path (Plan may carry a `#anchor`); `null` if absent,
  but at least one of the two is required. The file must exist.
- **Depends On** — comma list of issue numbers, or `null`.
- **Network** — whether the task uses external data or services. `Network-Approved` is
  Orchestra-managed gate state and defaults to `false`.
- **Acceptance** — ≥1 checkbox; each criterion must be mechanically verifiable.
- **Decisions / Blocked Reason** — free text derived by reconcile from validated structured
  role results and durable attempt evidence.

## Promotion is a human act
Nothing enters a queue without a human blessing it. That promotion is the boundary
between thinking (ROADMAP/specs/plans in the project repo) and doing (the queue).
