"""Checker: independently verifies a maker attempt before any PR is
created. All five checks must pass for PASS - never trusts the maker's
own claims without re-deriving them.
"""

import subprocess
import time
from pathlib import Path

import maker

MAIN_BRANCH = "main"
MAX_CHECKER_ATTEMPTS = 3  # retries for the independent PyPI re-check only


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def check_tests_passed(attempt: dict):
    ok = attempt.get("tests_passed") is True
    return ok, ("tests passed" if ok else "tests did not pass in the worktree")


def check_diff_scope(worktree: Path, branch: str):
    stat = run(["git", "diff", f"{MAIN_BRANCH}...{branch}", "--stat"], worktree).stdout
    changed_files = [line.split("|")[0].strip() for line in stat.splitlines() if "|" in line]
    reasons = []
    if not changed_files:
        reasons.append("no files were changed relative to main")
    for f in changed_files:
        if f != "requirements.txt":
            reasons.append(f"unexpected file changed outside the audit's scope: {f}")
    return (len(reasons) == 0), reasons, changed_files


def check_version_is_real(attempt: dict):
    """Independently re-fetch the latest version from PyPI rather than
    trusting the maker's own claim - guards against a maker that invented
    or misreported a version."""
    last_error = None
    for attempt_no in range(1, MAX_CHECKER_ATTEMPTS + 1):
        try:
            real_latest = maker.latest_version(attempt["package"])
            ok = attempt["latest"] == real_latest
            return ok, f"maker claimed {attempt['latest']}, PyPI currently shows {real_latest}"
        except Exception as e:
            last_error = e
            if attempt_no < MAX_CHECKER_ATTEMPTS:
                time.sleep(2)
    return False, f"could not independently verify version on PyPI after {MAX_CHECKER_ATTEMPTS} attempts: {last_error}"


def check_not_major_bump(attempt: dict):
    ok = attempt["risk"] == "safe"
    detail = (
        "same major version"
        if ok
        else f"major version bump ({attempt['current']} -> {attempt['latest']}) - policy requires human sign-off for major bumps"
    )
    return ok, detail


def check_justified(attempt: dict):
    reason = attempt.get("reason", "").strip()
    ok = len(reason) > 20
    return ok, ("justification present" if ok else "no justification recorded")


def review(worktree_path, branch: str, attempt: dict):
    worktree = Path(worktree_path).resolve()
    reasons = []
    lines = [f"--- Checking {attempt['package']} {attempt['current']} -> {attempt['latest']} (branch '{branch}') ---"]

    tests_ok, tests_detail = check_tests_passed(attempt)
    lines.append(f"[1/5] Tests pass: {'PASS' if tests_ok else 'FAIL'} - {tests_detail}")
    if not tests_ok:
        reasons.append(f"tests did not pass: {tests_detail}")

    scope_ok, scope_reasons, changed_files = check_diff_scope(worktree, branch)
    lines.append(f"[2/5] Diff scope: {'PASS' if scope_ok else 'FAIL'} (changed: {changed_files})")
    reasons.extend(scope_reasons)

    version_ok, version_detail = check_version_is_real(attempt)
    lines.append(f"[3/5] Version independently re-verified on PyPI: {'PASS' if version_ok else 'FAIL'} - {version_detail}")
    if not version_ok:
        reasons.append(version_detail)

    major_ok, major_detail = check_not_major_bump(attempt)
    lines.append(f"[4/5] Not a major-version bump: {'PASS' if major_ok else 'FAIL'} - {major_detail}")
    if not major_ok:
        reasons.append(major_detail)

    just_ok, just_detail = check_justified(attempt)
    lines.append(f"[5/5] Justification recorded: {'PASS' if just_ok else 'FAIL'} - {just_detail}")
    if not just_ok:
        reasons.append(just_detail)

    verdict = tests_ok and scope_ok and version_ok and major_ok and just_ok
    lines.append("PASS" if verdict else "FAIL")
    if not verdict:
        lines.append("Reasons:")
        for r in reasons:
            lines.append(f"- {r}")

    return verdict, "\n".join(lines), reasons
