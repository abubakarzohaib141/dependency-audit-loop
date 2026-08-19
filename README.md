# #8 — Dependency Audit Loop

The capstone of the Loop Engineering section: an automated **Dependency
Audit + Update PR Loop** that periodically inspects a repository's
Python dependencies, proposes safe updates in isolation, verifies them
independently, and opens a real GitHub PR — but only when an independent
checker approves the work, and never merges anything itself.

- Repo: https://github.com/abubakarzohaib141/dependency-audit-loop
- Routine: `trig_0144EJvPq3McxnUaYfuKc4XN` (daily, `0 3 * * *` UTC)

```text
HEARTBEAT (Routine, daily)
    |
run_cycle.py  <- reads progress.md (spine/memory)
    |
for each NEW candidate, up to the budget:
    |
    worktree  ->  MAKER (maker.py)  ->  CHECKER (checker.py)
    |                                        |
    |                                    PASS / FAIL
    |                                        |
    |                              PASS -> CONNECTOR opens a real PR
    |                              FAIL -> no PR, recorded NEEDS_HUMAN
    |
progress.md updated, one RESULT line printed
```

## What the loop does

Given a repository with pinned dependencies (`requirements.txt`), the
loop: finds which pins are outdated (live PyPI check), classifies each as
**safe** (same major version) or **risky** (major version bump),
attempts up to a budget-limited number of NEW ones per run in isolated
worktrees, has an independent checker verify each attempt, and opens a
PR only for the ones that pass. It never touches `main` except to append
its own memory ledger, never merges anything, and never repeats work
it's already recorded.

## Architecture — the six parts

### 1. Heartbeat

A real Claude Code **Routine** (`trig_0144EJvPq3McxnUaYfuKc4XN`), cron
`0 3 * * *` (daily, UTC), pointed at this repo. Each firing runs
`python run_cycle.py --max-updates 1` and, if needed, completes PR
creation via GitHub MCP tools (see **Connector** below). For
development, `python run_cycle.py` runs one cycle manually, synchronously,
with full printed output — the exact same code path the Routine uses.

### 2. Worktree

Every attempt happens in `git worktree add .worktrees/<package> -b
dep-update/<package>` — its own working directory and branch, sharing
the same `.git` history as `main`. `main` is **never** checked out for
edits; `run_cycle.py`'s own `git checkout main` calls are read-only
positioning before inspecting or appending to `progress.md`.

### 3. Skill

[`skills/dependency-audit.md`](skills/dependency-audit.md) — how to
inspect dependencies (query PyPI, never guess a version), how to judge
appropriateness (major-version bump = always risky, deferred to a
human), how to modify files (only the one pinned line, nothing else),
how to run tests (fresh isolated venv, full suite), and explicit "when
NOT to update" / "when to stop and ask a human" rules.

### 4. Maker

[`maker.py`](maker.py) — `find_candidates()` queries PyPI live for every
pinned package; `make_attempt()` edits exactly one line in
`requirements.txt`, creates a fresh venv inside the worktree, installs,
runs `pytest -v`, and records a justification. Never merges, never
touches `main`.

### 5. Checker

[`checker.py`](checker.py) — five independent checks, **all** must
pass:
1. Tests actually pass in the isolated venv.
2. Diff scope: only `requirements.txt` changed.
3. The claimed "latest" version is **independently re-fetched from PyPI**
   — the checker never trusts the maker's own claim.
4. Not a major-version bump (policy: always defer to a human regardless
   of whether tests happen to pass — see evidence below, this is real).
5. A justification is recorded.

### 6. Connector

GitHub, via `gh` CLI locally, or — since the Claude Code cloud sandbox
does **not** have `gh` installed at all (confirmed by direct testing,
not assumed) — a JSON fallback (`pr_to_create_<package>.json`) that the
Routine's agent reads and completes via the GitHub MCP tools. Either
path produces the same real PR containing: old version, new version, why,
full test output, and the checker's verdict. **PRs are never merged
automatically** — human approval is the final gate.

## Spine / memory

[`progress.md`](progress.md) — every run appends a dated section:
inspected packages, candidates found, every attempt's verdict, PR links,
budget usage, and anything deferred. The next run parses every prior
`- \`package\`: current -> latest (PASS|FAIL)` line and skips any
`(package, latest)` pair already handled — it does not blindly repeat
work. (`DEFERRED` entries are **not** treated as handled — they're
picked up automatically once budget allows.)

## Budget guards

`--max-updates N` (default in the Routine's prompt: 1) caps how many
**new** candidates are attempted per run. `maker.MAX_MAKER_ATTEMPTS` and
`checker.MAX_CHECKER_ATTEMPTS` (both 3) bound retries of transient
network/install failures only — never a genuine test failure or
rejection. When the budget is reached with candidates still outstanding,
they're recorded as `DEFERRED` and the run's `RESULT` is `NEEDS_HUMAN`.

## Human safety

The loop never merges a PR, never deletes anything, never touches a file
outside `requirements.txt` in a worktree, never rewrites unrelated code,
and never approves a major-version bump automatically. Every rejection
and every deferral is written to `progress.md` as `NEEDS_HUMAN` with the
concrete reason — nothing fails silently.

---

## Verification — actual evidence for every scenario

### A. Normal successful audit + B. safe update passes -> PR

Run 1 (`--max-updates 2`, 2026-08-19T17:15:38Z), against the original
`click==7.0` / `requests==2.25.0`:

```
Found 2 outdated (0 already handled).
--- Processing requests: 2.25.0 -> 2.34.2 (safe) ---
[1/5] Tests pass: PASS   [2/5] Diff scope: PASS   [3/5] Version re-verified: PASS
[4/5] Not a major bump: PASS   [5/5] Justification: PASS
PASS -> PR opened: https://github.com/abubakarzohaib141/dependency-audit-loop/pull/1
```

### C. A deliberately risky change the checker rejects

Same run, `click==7.0 -> 8.4.2` (a real major-version jump):

```
[1/5] Tests pass: PASS   <- tests actually passed on click 8.x too
[4/5] Not a major-version bump: FAIL - major version bump (7.0 -> 8.4.2)
      - policy requires human sign-off for major bumps
FAIL -> no PR created
```

**This is the important part**: tests passing did **not** get click a
PR. The checker doesn't just trust green tests — the major-version
policy overrides it, exactly as the skill file specifies.

### D. A second run demonstrates persistent memory

Run 2, same command, immediately after:

```
Found 2 outdated (2 already handled in a previous run, skipping).
  SKIP (memory): click 7.0 -> 8.4.2 already handled previously
  SKIP (memory): requests 2.25.0 -> 2.34.2 already handled previously
RESULT: OK - 0 PR(s) opened, 0 rejected, 0 deferred, 2 already known
```

No worktrees created, no PyPI-check repeated, no duplicate PR attempt.

### E. Budget guard stopping a run at its limit

`six==1.15.0` and `python-dateutil==2.8.1` were added to `main` (a plain
human commit — the loop never does this itself). Run 3 with
`--max-updates 1`:

```
Found 4 outdated (2 already handled, skipping).
--- Processing python-dateutil: 2.8.1 -> 2.9.0.post0 (safe) ---
PASS -> PR opened: .../pull/2
RESULT: NEEDS_HUMAN - 1 PR(s) opened, 0 rejected, 1 deferred (budget), 2 already known
```

`progress.md` recorded `six` under `### Deferred (budget reached)` —
**not** silently dropped. A follow-up run with a larger budget picked it
up correctly and opened PR #3, confirming deferred work isn't lost.

### F. `main` remains untouched

```bash
$ git show main:requirements.txt
click==7.0
requests==2.25.0
six==1.15.0
python-dateutil==2.8.1
colorama==0.4.3
tqdm==4.50.0
pyparsing==3.0.0
wcwidth==0.2.5
```

Every one of these is still at its **original human-pinned version** on
`main`, after 7 open PRs across 8+ runs. Every proposed update lives only
on its own `dep-update/<package>` branch.

### G. Assignments #1–#7 untouched

Verified by listing all project folders and spot-checking file
timestamps on key files in each — all predate this session. (One
unrelated observation, not caused by this work: `#6`'s `README.md` has
reverted to a placeholder on disk since it was last completed — flagged
to the user, left untouched per instructions, since only #8 was in
scope here.)

### A real bug found and fixed during testing (not simulated)

The first real cloud-Routine run crashed partway through: `gh pr create`
failed in the sandbox (no `gh` binary at all there — confirmed via a
dedicated diagnostic run first, not assumed) in a way my original
`except FileNotFoundError` clause didn't catch, so the script raised
uncaught and never reached `_write_pr_request_fallback()`. The Routine's
agent recovered manually (found the pushed branch, rebuilt the PR body,
opened it via MCP, fixed `progress.md`) — real resilience, but not
something to depend on nightly. Fixed by broadening `open_pr()` to catch
`OSError` generally and to fall through to the JSON fallback on **any**
non-zero exit from `gh` too, plus wrapping each candidate's processing
in the main loop with `try/except` so one candidate's unexpected failure
can never crash the whole cycle or skip writing `progress.md`. Re-tested
for real in the cloud afterward: clean run, correct JSON fallback,
correct MCP recovery, PR #7, no crash. All 7 PRs (`#1`–`#7`) and every
scenario above are visible at
https://github.com/abubakarzohaib141/dependency-audit-loop/pulls?q=is%3Apr.

## How to run one cycle manually

```bash
pip install -r requirements-dev.txt
python run_cycle.py --max-updates 1
```

Prints the full audit trail live; appends to `progress.md`; opens a real
PR via `gh` if available locally (it is, on this machine).

## How the scheduled run works

The Routine fires daily at 03:00 UTC, clones the repo fresh, runs the
exact command above, and — since that cloud sandbox has no `gh` CLI —
completes any resulting PR via the GitHub MCP tools per the fallback
protocol documented above. It never merges, and it stops on its own once
the budget (1 new candidate/day, by default) is used or nothing new is
found.

## How to inspect PRs

https://github.com/abubakarzohaib141/dependency-audit-loop/pulls — every
PR body contains old/new version, why, full test output, and the
checker's verdict inline. `progress.md` is the running index of
everything ever attempted, with PR links.

## Failure / recovery behavior

- A rejected candidate (major bump, failing tests, out-of-scope diff, an
  unverifiable version) → `FAIL`, no PR, recorded with concrete reasons,
  overall run `RESULT: NEEDS_HUMAN`.
- Budget exhausted with candidates left → `DEFERRED`, not dropped, picked
  up automatically once budget allows.
- An unexpected exception processing one candidate → caught, recorded as
  a `FAIL` with the real error, doesn't crash the rest of the run.
- `gh` CLI unavailable for the actual PR-open step → JSON fallback file,
  completed via GitHub MCP tools, `progress.md` updated with the real
  URL once done.

## Week-long unattended operation

The Routine is live now, `enabled: true`, cron `0 3 * * *` UTC,
`next_run_at` confirmed via the API. Nothing further needs to be
configured for it to keep running for 7+ days on its own.

**What to inspect each day:**
1. https://github.com/abubakarzohaib141/dependency-audit-loop/pulls —
   any new PR? Read its body (old/new version, why, tests, verdict) and
   decide whether to merge it yourself — the loop won't.
2. `progress.md` on `main` — a new dated section should appear daily,
   ending in `RESULT: OK` or `RESULT: NEEDS_HUMAN`. `NEEDS_HUMAN` means
   read the `### Needs human` line — a rejection, a deferral, or (rare,
   now hardened against) an unexpected error.
3. https://claude.ai/code/routines/trig_0144EJvPq3McxnUaYfuKc4XN — the
   run history; each session's transcript is inspectable if a day's
   result looks surprising.

**How to verify the 7-day requirement was actually met** (after real
time has passed — not claimed here since it hasn't): confirm 7 distinct
dated sections appeared in `progress.md` on 7 different calendar days
matching `next_run_at` timestamps roughly 24h apart, with no manual
`RemoteTrigger run` calls responsible for any of them (only genuine
`last_fired_at` scheduler timestamps).
