"""Issue #306 — game-shell token presence & scoping pins.

The §27 warm palette must live on the .game-shell scope (mirroring how
.world-shell scopes its --w-* family): the 7 palette tokens with exact hex
values, no --game-* declaration outside the .game-shell block, and the two
global :root blocks (dark legacy + landing) unchanged vs base — no --game-*
leak, no token drift.

RED on base 99f38fa (no .game-shell block); green with the #306
implementation in the tree.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSS_PATH = ROOT / "backend" / "app" / "web" / "style.css"

# The 7 §27 palette tokens (exact hex, case as shipped).
PALETTE_TOKENS = {
    "--game-bg": "#F3ECDF",
    "--game-panel": "#FFF9EF",
    "--game-ink": "#30271F",
    "--game-dim": "#69594C",
    "--game-accent": "#86551E",
    "--game-critical": "#A13D32",
    "--game-success": "#42613E",
}

# The full token list of the .game-shell block (7 palette + 4 support
# tokens) — pinned so a rename/drift is visible.
GAME_SHELL_TOKENS = {
    **PALETTE_TOKENS,
    "--game-accent-hover": "#6E4519",
    "--game-border": "rgba(48, 39, 31, 0.14)",
    "--game-focus": "rgba(134, 85, 30, 0.45)",
    "--game-warm": "rgba(134, 85, 30, 0.12)",
}

# Global :root invariants (base 99f38fa): the dark legacy block is pinned
# token-for-token (m269 pins it byte-exact; this re-states the token list),
# the landing block is pinned by its name set.
DARK_ROOT_TOKENS = {
    "--bg": "#101418",
    "--panel": "#1a2027",
    "--line": "#2a323c",
    "--text": "#d8dee6",
    "--dim": "#8a94a3",
    "--accent": "#4da3ff",
    "--ok": "#6fd08c",
    "--warn": "#e0b050",
    "--w-line": "rgba(148, 178, 205, 0.16)",
}
LANDING_ROOT_TOKEN_NAMES = {
    "--landing-bg", "--landing-surface", "--landing-ink", "--landing-ink-2",
    "--landing-ink-3", "--landing-muted", "--landing-gold", "--landing-gold-hover",
    "--landing-on-gold", "--landing-line", "--landing-line-2", "--landing-cream",
    "--landing-cream-rgb", "--landing-field", "--landing-danger", "--landing-olive",
    "--landing-shadow",
}


def _css():
    return CSS_PATH.read_text(encoding="utf-8")


def _block_span(css, selector):
    """(start, end) of the first `selector { ... }` rule, brace-matched."""
    i = css.index(selector + " {")
    depth, j = 1, i + len(selector) + 2
    while j < len(css) and depth:
        if css[j] == "{":
            depth += 1
        elif css[j] == "}":
            depth -= 1
        j += 1
    assert depth == 0, f"unbalanced braces in rule for {selector!r}"
    return i, j


def _block(css, selector):
    start, end = _block_span(css, selector)
    return css[start:end]


def _custom_props(block):
    clean = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", clean))


def _root_blocks(css):
    """All `:root { ... }` blocks, brace-matched (comments carry no braces)."""
    out, idx = [], 0
    while True:
        i = css.find(":root {", idx)
        if i == -1:
            return out
        depth, j = 1, i + len(":root {")
        while j < len(css) and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        out.append(css[i:j])
        idx = j


def test_seven_palette_tokens_exact_hex_in_game_shell():
    props = _custom_props(_block(_css(), ".game-shell"))
    for name, value in PALETTE_TOKENS.items():
        assert props.get(name) == value, (
            f"{name} must be exactly {value} in .game-shell, got {props.get(name)!r}"
        )


def test_game_shell_token_list_pinned():
    props = _custom_props(_block(_css(), ".game-shell"))
    assert props == GAME_SHELL_TOKENS, (
        f".game-shell token list drifted: {sorted(props)} vs {sorted(GAME_SHELL_TOKENS)}"
    )


def test_no_game_token_declaration_outside_game_shell():
    css = _css()
    start, end = _block_span(css, ".game-shell")
    decls = [m.start() for m in re.finditer(r"--game-[\w-]+\s*:", css)]
    assert decls, "no --game-* token declarations found at all"
    for i in decls:
        assert start <= i < end, (
            f"a --game-* declaration at offset {i} escapes the .game-shell scope"
        )


def test_root_blocks_unchanged_and_game_free():
    css = _css()
    roots = _root_blocks(css)
    assert len(roots) == 2, "exactly two :root blocks (dark legacy + landing)"
    dark, landing = _custom_props(roots[0]), _custom_props(roots[1])
    # Dark legacy :root — token-for-token unchanged vs base.
    assert dark == DARK_ROOT_TOKENS, f"dark :root token list drifted: {dark}"
    # Landing :root — name set unchanged.
    assert set(landing) == LANDING_ROOT_TOKEN_NAMES, (
        f"landing :root token names drifted: {sorted(landing)}"
    )
    # No --game-* leak into either global block.
    assert not any(k.startswith("--game-") for k in dark)
    assert not any(k.startswith("--game-") for k in landing)
