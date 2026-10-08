"""#125 pins: test_ae6_serve_missing_db must be hang-proof.

Issue #125: the old test replaced `db_path: data/lifesim.db`, but the real
config key (config/default.yaml) is `db_path: "lifesim.db"` — the replace
silently no-oped, so the config stayed CWD-relative and any stray lifesim.db
in the repo root made `main(["serve", ...])` fall through to uvicorn.run(),
hanging the whole suite. These byte-pins freeze the fix in place:

- the serve invocation must be isolated with a `cwd=` tmp dir and guarded by
  a hard `timeout=` (kill + assert-fail, never an infinite hang);
- the config patch target must literally match the actual db_path line of
  config/default.yaml (so a future key drift fails loudly, not lazily).
"""

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SERVE_TEST = _REPO_ROOT / "tests" / "m5" / "test_chunk_d.py"
_CONFIG = _REPO_ROOT / "config" / "default.yaml"


def _serve_test_source() -> str:
    src = _SERVE_TEST.read_text(encoding="utf-8")
    match = re.search(
        r"def test_ae6_serve_missing_db\(.*?(?=\ndef |\Z)", src, re.DOTALL
    )
    assert match, "test_ae6_serve_missing_db not found in tests/m5/test_chunk_d.py"
    return match.group(0)


def test_pin_serve_refusal_runs_isolated_with_hard_timeout():
    """#125: the serve invocation must set cwd= (tmp isolation) and timeout=."""
    source = _serve_test_source()
    assert "cwd=" in source, "#125: serve invocation must run with cwd= isolation"
    assert "timeout=" in source, "#125: serve invocation must have a hard timeout= guard"


def test_pin_serve_refusal_patches_real_db_path_key():
    """#125: the replace target must match config/default.yaml's actual db_path line."""
    source = _serve_test_source()
    db_lines = [
        line.strip()
        for line in _CONFIG.read_text(encoding="utf-8").splitlines()
        if re.match(r"^\s*db_path:", line)
    ]
    assert db_lines, "#125: config/default.yaml lost its db_path key"
    for actual in db_lines:
        assert actual in source, (
            f"#125: patch target must match the real config key {actual!r} "
            "(a stale target no-ops and leaves db_path CWD-relative)"
        )
