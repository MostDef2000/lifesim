"""#367 — shared navigation and inventory shell behavior (Node DOM harness)."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_inventory_shell_behavior():
    harness = Path(__file__).with_name("inventory_shell.node.js")
    result = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "PASS: navigation" in result.stdout


def test_world_and_inventory_reuse_dock():
    js = (ROOT / "backend/app/web/app.js").read_text(encoding="utf-8")
    assert 'const worldPill = gameSectionNav("#/world");' in js
    assert 'gameSectionNav("#/inventory")' in js
    assert js.count('class: "world-pill"') == 1
    assert js.count('class: "panel inv-shell"') == 1
    css = (ROOT / "backend/app/web/style.css").read_text(encoding="utf-8")
    assert ".inventory-game-shell .world-pill {" in css
    assert "grid-template-rows: minmax(0, 1fr) auto;" in css
