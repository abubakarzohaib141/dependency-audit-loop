# Skill: Dependency Audit

Use this checklist whenever inspecting or proposing dependency updates in
this repository.

## How to inspect dependencies

1. Read `requirements.txt` - every pinned `package==version` line is a
   dependency to check.
2. For each package, query PyPI's JSON API
   (`https://pypi.org/pypi/<package>/json`, field `info.version`) for the
   currently published latest release. Never guess a version number.
3. A package is a candidate only if the pinned version differs from the
   latest published version.

## How to determine whether an update is appropriate

4. Compare the **major** version number (the first dotted component) of
   the current pin against the latest release.
   - Same major version (e.g. `2.25.0 -> 2.34.2`) → **safe**: a routine
     compatible update, eligible for automatic proposal.
   - Different major version (e.g. `7.0 -> 8.4.2`) → **risky**: treat as
     a breaking-change candidate. Semantic versioning reserves a major
     bump specifically for changes that may break existing usage.

## How to modify dependency files

5. Change only the single pinned version line for the package being
   updated in `requirements.txt`. Do not touch any other file, any other
   dependency's pin, or unrelated code, even if it looks like an
   improvement.
6. Never delete a dependency, never add a new one, never relax a pin to
   a range (`>=`) unless explicitly asked to.

## How to run tests

7. Install the updated `requirements.txt` into a **fresh, isolated
   virtual environment** (never the shared/system Python) and run the
   full test suite (`pytest -v`) against it.
8. A dependency update is only a candidate for approval if the full
   suite passes in that isolated environment - not a subset, not "looks
   fine on inspection."

## When NOT to update a dependency

- The version bump is a **major** version change. Always defer to a
  human, regardless of whether tests happen to still pass - a passing
  test suite does not prove the absence of behavior changes it doesn't
  exercise.
- The tests fail in the isolated environment.
- The change would touch any file other than `requirements.txt`.
- The "latest" version cannot be independently re-verified (e.g. PyPI is
  unreachable) - do not proceed on an unverified claim.

## When to stop and ask for a human

- A required check cannot be completed after a small, bounded number of
  attempts (see budget guards in `run_cycle.py`) - do not retry
  indefinitely.
- A major-version candidate exists - always surface it as `NEEDS_HUMAN`
  with the reasoning, never silently skip it or silently approve it.
- The per-run budget of update attempts is reached with candidates still
  outstanding - record what's deferred and why, don't quietly drop it.

## What this skill does NOT decide

This skill guides the maker's judgment about *what* to propose. It does
not decide whether a proposal is accepted - that is the checker's job,
independently, based on objective evidence (tests, diff scope, an
independently re-verified version, and this same major-version policy).
The maker cannot approve its own work.
