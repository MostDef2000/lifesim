"""#368 — interactive inventory cards (Node DOM harness).

The harness executes the production inventory region (view state, filters,
card selection, sell draft, wear/sell guards) against a minimal DOM and
asserts the observable behavior: local interactions issue zero API calls,
stale action responses mutate nothing, and the pinned shell facts hold.
"""

import subprocess
from pathlib import Path


def test_inventory_cards_behavior():
    harness = Path(__file__).with_name("inventory_cards.node.js")
    result = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert (
        "PASS: selection, filters, unknown type, sell draft, double-submit, "
        "stale guards, pinned shell"
    ) in result.stdout
