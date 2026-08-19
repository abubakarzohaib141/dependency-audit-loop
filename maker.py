"""Maker: inspects requirements.txt, finds outdated pinned dependencies,
classifies them safe/risky, and - within budget - proposes an update
inside an isolated worktree: edits requirements.txt, installs it into a
fresh venv, runs the tests, and records what happened.

Never merges anything. Never touches main. See skills/dependency-audit.md
for the full policy this follows.
"""

import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PYPI_URL = "https://pypi.org/pypi/{name}/json"
REQ_LINE_RE = re.compile(r"^([A-Za-z0-9_.\-]+)==([A-Za-z0-9_.\-]+)\s*$")

MAX_MAKER_ATTEMPTS = 3  # retries for transient install/network failures only


def parse_requirements(path: Path) -> dict:
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = REQ_LINE_RE.match(line)
        if m:
            pins[m.group(1)] = m.group(2)
    return pins


def latest_version(package: str) -> str:
    with urllib.request.urlopen(PYPI_URL.format(name=package), timeout=15) as r:
        data = json.load(r)
    return data["info"]["version"]


def major(version: str) -> str:
    return version.split(".")[0]


def find_candidates(req_path: Path) -> list:
    """Return [{package, current, latest, risk}] for every outdated pinned
    dependency. risk is 'safe' (same major version) or 'risky' (major
    version bump)."""
    pins = parse_requirements(req_path)
    candidates = []
    for package, current in sorted(pins.items()):
        try:
            latest = latest_version(package)
        except Exception as e:
            candidates.append({
                "package": package, "current": current, "latest": None,
                "risk": "unknown", "error": str(e),
            })
            continue
        if latest == current:
            continue
        risk = "safe" if major(latest) == major(current) else "risky"
        candidates.append({
            "package": package, "current": current, "latest": latest,
            "risk": risk,
        })
    return candidates


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def apply_update(worktree: Path, package: str, new_version: str) -> bool:
    """Edit requirements.txt in the worktree to pin package==new_version.
    Returns True if a line was actually changed."""
    req_path = worktree / "requirements.txt"
    lines = req_path.read_text(encoding="utf-8").splitlines()
    changed = False
    for i, line in enumerate(lines):
        m = REQ_LINE_RE.match(line.strip())
        if m and m.group(1) == package:
            lines[i] = f"{package}=={new_version}"
            changed = True
            break
    if changed:
        req_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changed


def install_and_test(worktree: Path):
    """Create an isolated venv inside the worktree, install requirements,
    run pytest. Returns (tests_passed, output). Retries transient failures
    (e.g. a flaky pip download) up to MAX_MAKER_ATTEMPTS times - this does
    NOT retry a genuine test failure, only the install step's exit code
    being a network/tooling error is retried."""
    venv_dir = worktree / ".venv-audit"
    py = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

    last_output = ""
    for attempt in range(1, MAX_MAKER_ATTEMPTS + 1):
        run([sys.executable, "-m", "venv", str(venv_dir)], worktree)
        install = run(
            [str(py), "-m", "pip", "install", "-q", "-r", "requirements.txt", "-r", "requirements-dev.txt"],
            worktree,
        )
        if install.returncode == 0:
            break
        last_output = f"pip install failed (attempt {attempt}/{MAX_MAKER_ATTEMPTS}):\n{install.stdout}\n{install.stderr}"
        if attempt < MAX_MAKER_ATTEMPTS:
            time.sleep(2)
    else:
        return False, last_output

    test = run([str(py), "-m", "pytest", "-v"], worktree)
    return test.returncode == 0, test.stdout + test.stderr


def make_attempt(worktree: Path, candidate: dict) -> dict:
    """Apply the candidate's update in the worktree, install + test it,
    and return an attempt record for the checker/orchestrator to use."""
    package = candidate["package"]
    changed = apply_update(worktree, package, candidate["latest"])
    if not changed:
        return {**candidate, "attempted": False, "reason": "package not found in requirements.txt"}

    tests_passed, test_output = install_and_test(worktree)

    reason = (
        f"{package} is pinned at {candidate['current']}; {candidate['latest']} is the "
        f"current release on PyPI. "
        + (
            "Same major version, so this is treated as a routine compatible update."
            if candidate["risk"] == "safe"
            else "This is a MAJOR version bump, which can include breaking API changes."
        )
    )

    return {
        **candidate,
        "attempted": True,
        "tests_passed": tests_passed,
        "test_output_tail": "\n".join(test_output.strip().splitlines()[-15:]),
        "reason": reason,
    }
