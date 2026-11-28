"""#369 — appearance view inside the shared game shell (Node DOM harness).

The harness drives the production appearance view (viewAppearance,
renderAppearance, runPreview) through loading, ready, empty, error,
unavailable, preview and stale transitions against a minimal DOM and
asserts the observable behavior: shell states, navigation, typed draft
survival, preview isolation, stale guard and retry.
"""

import subprocess
from pathlib import Path


def test_appearance_shell_behavior():
    harness = Path(__file__).with_name("appearance_shell.node.js")
    result = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert (
        "PASS: shell states, navigation, draft survival, preview isolation, "
        "stale guard, retry"
    ) in result.stdout
