#!/usr/bin/env python3
"""ONE cycle of the dependency audit loop.

    python run_cycle.py [--max-updates N]

Each invocation:
  1. Reads progress.md (the spine) to see which (package, version)
     candidates were already attempted in a previous run - never repeats
     that work blindly.
  2. Finds currently outdated dependencies (live PyPI check).
  3. Filters out already-handled candidates (memory).
  4. Processes up to --max-updates NEW candidates (the budget guard) -
     each in its own isolated worktree: maker proposes + tests, checker
     independently verifies, PR is opened ONLY on PASS.
  5. Anything beyond budget is deferred, not silently dropped.
  6. Appends one dated section to progress.md recording everything, and
     prints exactly one final RESULT log line.

main is never modified with respect to the audited dependency/code - only
this script's own progress.md ledger is committed directly to main,
exactly like Assignments #3 and #7's memory files.
"""

import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

import checker
import maker

REPO_ROOT = Path(__file__).resolve().parent
WORKTREES_DIR = REPO_ROOT / ".worktrees"
PROGRESS_FILE = REPO_ROOT / "progress.md"
REQ_FILE = REPO_ROOT / "requirements.txt"

ATTEMPT_LINE_RE = re.compile(
    r"^- `(?P<package>[^`]+)`: (?P<current>\S+) -> (?P<latest>\S+) \((?P<verdict>PASS|FAIL|DEFERRED)\)"
)


def run(cmd, cwd=REPO_ROOT):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def load_previously_handled() -> set:
    """Parse progress.md and return {(package, latest_version)} for every
    candidate already attempted (PASS or FAIL) in any previous run. A
    DEFERRED entry does NOT count as handled - it's still outstanding."""
    if not PROGRESS_FILE.exists():
        return set()
    handled = set()
    for line in PROGRESS_FILE.read_text(encoding="utf-8").splitlines():
        m = ATTEMPT_LINE_RE.match(line.strip())
        if m and m.group("verdict") in ("PASS", "FAIL"):
            handled.add((m.group("package"), m.group("latest")))
    return handled


def reset_candidate_worktree(package: str):
    branch = f"dep-update/{package}"
    path = WORKTREES_DIR / package
    if path.exists():
        run(["git", "worktree", "remove", "--force", str(path)])
    run(["git", "branch", "-D", branch])  # no-op if it doesn't exist


def create_candidate_worktree(package: str) -> str:
    branch = f"dep-update/{package}"
    result = run(["git", "worktree", "add", str(WORKTREES_DIR / package), "-b", branch])
    if result.returncode != 0:
        raise RuntimeError(f"failed to create worktree for {package}:\n{result.stderr}")
    return branch


def build_pr_body(attempt: dict, review_report: str) -> str:
    return (
        f"## Dependency update: `{attempt['package']}`\n\n"
        f"| | |\n|---|---|\n"
        f"| Old version | `{attempt['current']}` |\n"
        f"| New version | `{attempt['latest']}` |\n"
        f"| Risk classification | {attempt['risk']} |\n\n"
        f"### Why\n{attempt['reason']}\n\n"
        f"### Test results (isolated venv, full suite)\n```\n{attempt['test_output_tail']}\n```\n\n"
        f"### Reviewer verdict\n```\n{review_report}\n```\n\n"
        f"This PR was opened automatically because the checker returned "
        f"PASS. It has NOT been merged - merging requires human approval.\n"
    )


def open_pr(package: str, branch: str, attempt: dict, review_report: str) -> str:
    """Push the branch and open the PR. Prefers the `gh` CLI (used when
    running locally, where it's authenticated). If `gh` isn't installed
    on this machine at all (true in the Claude Code cloud sandbox, which
    routes GitHub access through MCP tools instead of a `gh` binary),
    falls back to writing a pr_to_create_<package>.json request file with
    everything needed - the calling agent (which DOES have GitHub MCP
    tool access there) is instructed to open the real PR from it. Either
    way the checker has already made the PASS decision; this step is only
    the mechanical act of opening the PR."""
    worktree = WORKTREES_DIR / package
    run(["git", "push", "-u", "origin", branch], cwd=worktree)
    title = f"Update {package} {attempt['current']} -> {attempt['latest']}"
    body = build_pr_body(attempt, review_report)

    # This step must NEVER raise - it's the last thing that happens in an
    # unattended run, and a crash here would leave the run without its
    # final progress.md entry at all. Any failure mode (binary missing,
    # not executable, gh present but not authenticated, network error,
    # anything else) falls through to the same JSON-fallback path rather
    # than only handling the one failure mode tested locally.
    try:
        result = subprocess.run(
            ["gh", "pr", "create", "--repo", "abubakarzohaib141/dependency-audit-loop",
             "--base", "main", "--head", branch, "--title", title, "--body", body],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
    except OSError as e:
        print(f"  NOTE: could not invoke gh CLI ({e}).")
        return _write_pr_request_fallback(package, branch, title, body)

    if result.returncode != 0:
        print(f"  NOTE: gh pr create failed (exit {result.returncode}): {result.stderr.strip()[:300]}")
        return _write_pr_request_fallback(package, branch, title, body)

    return result.stdout.strip().splitlines()[-1]


def _write_pr_request_fallback(package: str, branch: str, title: str, body: str) -> str:
    request_path = REPO_ROOT / f"pr_to_create_{package}.json"
    request_path.write_text(
        json.dumps({"repo": "abubakarzohaib141/dependency-audit-loop", "base": "main",
                    "head": branch, "title": title, "body": body}, indent=2),
        encoding="utf-8",
    )
    marker = f"PENDING (gh CLI unavailable here - see {request_path.name}, open via GitHub MCP tools)"
    print(f"  NOTE: gh CLI not found in this environment. Wrote {request_path.name} "
          f"with the PR details - open it via the GitHub MCP tools (e.g. "
          f"mcp__github__create_pull_request) using that file's fields, then delete it.")
    return marker


def append_progress(entries: dict) -> None:
    ts = entries["timestamp"]
    lines = [f"## {ts}", "", "### Inspected"]
    for c in entries["all_candidates_and_unchanged"]:
        lines.append(f"- `{c}`")
    lines += ["", "### Candidates found"]
    if entries["candidates"]:
        for c in entries["candidates"]:
            lines.append(f"- `{c['package']}`: {c['current']} -> {c['latest']} ({c['risk']})")
    else:
        lines.append("- (none - all dependencies up to date)")
    lines += ["", "### Attempts"]
    if entries["attempts"]:
        for a in entries["attempts"]:
            lines.append(f"- `{a['package']}`: {a['current']} -> {a['latest']} ({a['verdict']})")
            if a["verdict"] == "PASS":
                lines.append(f"  - PR: {a['pr_url']}")
            elif a["verdict"] == "FAIL":
                lines.append(f"  - Reasons: {'; '.join(a['reasons'])}")
    else:
        lines.append("- (none)")
    if entries["deferred"]:
        lines += ["", "### Deferred (budget reached)"]
        for c in entries["deferred"]:
            lines.append(f"- `{c['package']}`: {c['current']} -> {c['latest']} ({c['risk']}) - DEFERRED, budget exhausted this run")
    lines += ["", f"### Budget", f"{entries['budget_used']}/{entries['budget_limit']} update attempts used this run"]
    lines += ["", "### Result", entries["result_status"]]
    if entries["result_status"] == "NEEDS_HUMAN":
        lines += ["", "### Needs human", entries["needs_human_reason"]]

    section = "\n".join(lines) + "\n"
    if PROGRESS_FILE.exists():
        existing = PROGRESS_FILE.read_text(encoding="utf-8").rstrip("\n")
        updated = existing + "\n\n" + section
    else:
        updated = "# Dependency Audit Log\n\n" + section
    PROGRESS_FILE.write_text(updated, encoding="utf-8")

    run(["git", "add", "progress.md"])
    run(["git", "commit", "-m", f"Audit run {ts}: {entries['result_status']}"])
    run(["git", "push"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-updates", type=int, default=1,
                         help="Maximum number of NEW dependency updates to attempt this run (budget guard)")
    args = parser.parse_args()
    budget_limit = args.max_updates

    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"=== Dependency audit cycle: {ts} (budget: {budget_limit}) ===")

    run(["git", "checkout", "main"])
    run(["git", "pull"])
    run(["git", "worktree", "prune"])

    all_packages = list(maker.parse_requirements(REQ_FILE).keys())
    candidates = maker.find_candidates(REQ_FILE)
    handled = load_previously_handled()

    new_candidates = [c for c in candidates if c.get("latest") and (c["package"], c["latest"]) not in handled]
    already_handled_skipped = [c for c in candidates if c.get("latest") and (c["package"], c["latest"]) in handled]

    print(f"Inspected {len(all_packages)} pinned dependencies.")
    print(f"Found {len(candidates)} outdated ({len(already_handled_skipped)} already handled in a previous run, skipping).")

    to_process = new_candidates[:budget_limit]
    deferred = new_candidates[budget_limit:]

    if already_handled_skipped:
        for c in already_handled_skipped:
            print(f"  SKIP (memory): {c['package']} {c['current']} -> {c['latest']} already handled previously")

    attempts_recorded = []
    budget_used = 0

    for candidate in to_process:
        package = candidate["package"]
        print(f"\n--- Processing {package}: {candidate['current']} -> {candidate['latest']} ({candidate['risk']}) ---")
        budget_used += 1

        # One candidate's unexpected failure must never crash the whole
        # cycle and skip writing progress.md - it becomes a recorded FAIL
        # with the real error, which surfaces as NEEDS_HUMAN below,
        # exactly like a genuine checker rejection would.
        try:
            reset_candidate_worktree(package)
            branch = create_candidate_worktree(package)
            worktree = WORKTREES_DIR / package

            attempt = maker.make_attempt(worktree, candidate)

            if not attempt.get("attempted"):
                print(f"  maker could not attempt: {attempt.get('reason')}")
                attempts_recorded.append({**attempt, "verdict": "FAIL", "reasons": [attempt.get("reason", "not attempted")]})
                continue

            run(["git", "add", "-A"], cwd=worktree)
            run(["git", "commit", "-m", f"Update {package} {candidate['current']} -> {candidate['latest']}"], cwd=worktree)

            verdict, report, reasons = checker.review(worktree, branch, attempt)
            print(report)

            if verdict:
                pr_url = open_pr(package, branch, attempt, report)
                print(f"  PASS -> PR opened: {pr_url}")
                attempts_recorded.append({**attempt, "verdict": "PASS", "pr_url": pr_url})
            else:
                print(f"  FAIL -> no PR created")
                attempts_recorded.append({**attempt, "verdict": "FAIL", "reasons": reasons})
        except Exception as e:
            print(f"  UNEXPECTED ERROR processing {package}: {e}")
            attempts_recorded.append({**candidate, "verdict": "FAIL", "reasons": [f"unexpected error: {e}"]})

    result_status = "OK"
    needs_human_reason = ""
    failed = [a for a in attempts_recorded if a["verdict"] == "FAIL"]
    if failed:
        result_status = "NEEDS_HUMAN"
        needs_human_reason = "; ".join(f"{a['package']}: {'; '.join(a.get('reasons', []))}" for a in failed)
    if deferred:
        result_status = "NEEDS_HUMAN"
        deferred_note = "budget reached with candidates still outstanding: " + ", ".join(
            f"{c['package']} ({c['current']} -> {c['latest']})" for c in deferred
        )
        needs_human_reason = (needs_human_reason + "; " if needs_human_reason else "") + deferred_note

    append_progress({
        "timestamp": ts,
        "all_candidates_and_unchanged": all_packages,
        "candidates": candidates,
        "attempts": attempts_recorded,
        "deferred": deferred,
        "budget_used": budget_used,
        "budget_limit": budget_limit,
        "result_status": result_status,
        "needs_human_reason": needs_human_reason,
    })

    passed = sum(1 for a in attempts_recorded if a["verdict"] == "PASS")
    failed_n = sum(1 for a in attempts_recorded if a["verdict"] == "FAIL")
    summary = (
        f"{passed} PR(s) opened, {failed_n} rejected, {len(deferred)} deferred (budget), "
        f"{len(already_handled_skipped)} already known"
    )
    print(f"\nRESULT: {result_status} - {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
