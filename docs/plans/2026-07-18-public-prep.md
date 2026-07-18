# Public-Release Prep Implementation Plan (orchestra)

> **For agentic workers:** Execute task-by-task. This plan PREPARES the repo
> for public release. Do NOT change repository visibility — the flip is a
> separate operator action after operator review.

**Goal:** Make the orchestra repo public-ready: a public-facing README pass and
a final health check. Recon (2026-07-18) found no secrets, no personal paths,
clean 19-commit history, MIT LICENSE present.

**Tech Stack:** Markdown, uv, pytest.

## Global Constraints

- Do NOT run `gh repo edit`, change visibility, or push. Local commits only.
- README copy follows `~/.config/agents/references/writing-style.md`: plain,
  concrete, no hype, no "excited to", no marketing language.
- Never present the Pi harness as implemented (see existing README caveat —
  preserve it).

---

### Task 1: Public-facing README pass

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Review README as a stranger.** Read `README.md` end to end as
someone who found the repo from a personal-site Projects link: no workspace
context, no prior knowledge. Note every place that assumes private context,
references paths outside the repo, or skips a step a clean-clone user needs.

- [ ] **Step 2: Edit.** Keep the existing structure and technical content
(recon rated it comprehensive). Required touches:
  - Open with 2-3 plain sentences of what orchestra IS (unattended
    multi-repo development scheduling: validator/worker/verifier roles,
    sandboxed harness runs, human approval gates) before any install text.
  - Add one status line near the top: "Status: alpha, in active development
    and daily use by its author."
  - Verify every command in the README works from a clean clone perspective
    (read-only check: do the referenced files/paths exist in the repo?).
  - Keep the Pi-harness "not implemented" caveat intact.

- [ ] **Step 3: Verify no private context leaked into the new copy.**

```bash
grep -nE "/home/potterzot|econpotter|moonshot|ai-due-diligence|agio" README.md && echo "FIX BEFORE COMMIT" || echo "clean"
```

Expected: `clean`.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: public-facing README pass"
```

---

### Task 2: Release health check

- [ ] **Step 1: Full suite, lint, types**

```bash
uv run pytest -q
uv run ruff check src tests
uv run mypy src
```

Expected: all green (suite was 334 passed + 1 skipped at plan time).

- [ ] **Step 2: Confirm hygiene** — no tracked `.env` or credential files:

```bash
git ls-files | grep -iE "\.env$|credential|secret" && echo "INVESTIGATE" || echo "clean"
test -f LICENSE && head -1 LICENSE
```

Expected: `clean`; LICENSE header prints (MIT).

- [ ] **Step 3: Report** — README diff summary for operator review, health
check results, and an explicit "ready for operator flip" line. No visibility
change, no push.
