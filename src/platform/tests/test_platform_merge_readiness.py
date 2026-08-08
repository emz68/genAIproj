import shutil
import subprocess

import pytest

from src.platform.merge_readiness import OWNERSHIP, violations


def test_own_paths_pass():
    paths = ["src/platform/run.py", "src/platform/stubs/ingestion_stub.py",
             "tests/integration/test_integration_orchestrator.py",
             "docs/proposals/anastasia.md"]
    assert violations("anastasia", paths) == []


def test_foreign_module_paths_flagged():
    assert violations("anastasia", ["src/ingestion/run.py"]) == ["src/ingestion/run.py"]
    assert violations("emlyn", ["src/validation/rules.yaml"]) == ["src/validation/rules.yaml"]


def test_frozen_root_and_seed_files_flagged():
    for path in ["requirements.txt", ".gitignore", "pyproject.toml",
                 "PRODUCT_DEVELOPMENT_MANAGER.md", "data/seed/README.md",
                 "src/__init__.py"]:
        assert violations("anastasia", [path]) == [path], path


def test_frozen_seeded_init_flagged_even_inside_owned_directory():
    # §2.1 rule 3: seeded __init__.py files are main-owned even though they
    # sit under a branch owner's directory prefix.
    assert violations("emlyn", ["src/ingestion/__init__.py"]) == ["src/ingestion/__init__.py"]
    assert violations("anastasia", ["src/platform/__init__.py"]) == ["src/platform/__init__.py"]
    # …but new files beside them are fine:
    assert violations("emlyn", ["src/ingestion/readers/__init__.py"]) == []


def test_proposal_file_only_for_own_branch():
    assert violations("sanja", ["docs/proposals/sanja.md"]) == []
    assert violations("sanja", ["docs/proposals/yash.md"]) == ["docs/proposals/yash.md"]


def test_emlyn_owns_data_readme_but_not_seed():
    assert violations("emlyn", ["data/README.md", "src/ingestion/readers.py"]) == []
    assert violations("emlyn", ["data/seed/ev_charging_sessions_cary.csv.gz"]) != []


def test_prefix_matching_is_path_safe():
    # src/platform-evil/ must not match the src/platform/ prefix rule
    assert violations("anastasia", ["src/platform-evil/x.py"]) == ["src/platform-evil/x.py"]


def test_unknown_branch_raises():
    with pytest.raises(ValueError):
        violations("mallory", ["README.md"])


@pytest.mark.skipif(shutil.which("git") is None, reason="git unavailable")
def test_live_check_on_this_repo():
    """The anastasia branch itself must pass its own gate (A5 used as §8 step 2)."""
    from src.platform.merge_readiness import check_branch

    try:
        ok, paths, bad = check_branch("anastasia", "main")
    except RuntimeError as e:
        pytest.skip(f"git refs unavailable: {e}")
    assert ok, f"anastasia branch touches foreign paths: {bad}"


@pytest.mark.skipif(shutil.which("git") is None, reason="git unavailable")
def test_cli_smoke():
    import sys

    proc = subprocess.run([sys.executable, "-m", "src.platform.merge_readiness", "--branch", "anastasia"],
                          capture_output=True, text=True)
    if "ERROR" in proc.stdout:
        pytest.skip(f"git refs unavailable for the CLI check: {proc.stdout.strip()}")
    assert "anastasia: PASS" in proc.stdout, proc.stdout
    assert proc.returncode == 0


def test_ownership_map_matches_the_five_people():
    assert set(OWNERSHIP) == {"emlyn", "sanja", "yash", "emily", "anastasia"}
