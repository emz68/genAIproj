"""A5 — merge-readiness checker: the §8 step-2 gate.

Verifies that a branch's diff against main touches ONLY the paths its owner
is assigned in the §4 ownership map (plus the branch's own docs/proposals
file). Run by the human Integration Manager; agents may run it read-only on
their own branch but never act on other branches.

Usage:
    python -m src.platform.merge_readiness --all
    python -m src.platform.merge_readiness --branch emlyn [--branch sanja] [--base main]
"""

from __future__ import annotations

import argparse
import subprocess
import sys

# §4 ownership map. Trailing "/" = directory prefix; otherwise exact file path.
OWNERSHIP: dict[str, tuple[str, ...]] = {
    "emlyn": ("src/ingestion/", "data/README.md"),
    "sanja": ("src/validation/",),
    "yash": ("src/reconciliation/",),
    "emily": ("src/reporting/",),
    "anastasia": ("src/platform/", "tests/integration/"),
}

ALL_BRANCHES = tuple(OWNERSHIP)


def violations(branch: str, changed_paths: list[str]) -> list[str]:
    """Paths in the diff that the branch owner is not allowed to touch."""
    if branch not in OWNERSHIP:
        raise ValueError(f"unknown branch {branch!r}; expected one of {sorted(OWNERSHIP)}")
    allowed = OWNERSHIP[branch]
    proposal = f"docs/proposals/{branch}.md"
    bad = []
    for path in changed_paths:
        if path == proposal:
            continue
        if any(path.startswith(rule) if rule.endswith("/") else path == rule for rule in allowed):
            continue
        bad.append(path)
    return bad


def changed_paths(branch: str, base: str = "main") -> list[str]:
    """`git diff --name-only base...branch`, falling back to origin/ refs."""
    last_error = ""
    for base_ref in (base, f"origin/{base}"):
        for branch_ref in (branch, f"origin/{branch}"):
            proc = subprocess.run(
                ["git", "diff", "--name-only", f"{base_ref}...{branch_ref}"],
                capture_output=True, text=True,
            )
            if proc.returncode == 0:
                return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
            last_error = proc.stderr.strip()
    raise RuntimeError(f"git diff failed for {base}...{branch}: {last_error}")


def check_branch(branch: str, base: str) -> tuple[bool, list[str], list[str]]:
    paths = changed_paths(branch, base)
    bad = violations(branch, paths)
    return not bad, paths, bad


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.platform.merge_readiness")
    parser.add_argument("--branch", action="append", default=None, choices=ALL_BRANCHES)
    parser.add_argument("--all", action="store_true", help="check all five branches")
    parser.add_argument("--base", default="main")
    args = parser.parse_args(argv)

    branches = list(ALL_BRANCHES) if args.all else (args.branch or [])
    if not branches:
        parser.error("pass --all or at least one --branch")

    any_fail = False
    for branch in branches:
        try:
            ok, paths, bad = check_branch(branch, args.base)
        except (RuntimeError, ValueError) as e:
            print(f"{branch}: ERROR — {e}")
            any_fail = True
            continue
        if ok:
            print(f"{branch}: PASS ({len(paths)} changed path(s), all owned)")
        else:
            any_fail = True
            print(f"{branch}: FAIL — touches {len(bad)} foreign path(s):")
            for p in bad:
                print(f"    {p}")
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
