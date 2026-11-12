"""Issue #306 — WCAG contrast for the game-shell palette.

Ratios are COMPUTED from the actual token values parsed out of the
.game-shell block (WCAG 2.x relative-luminance formula, implemented here —
no external deps), then pinned so a token drift is visible even when the
ratio still passes the threshold.

Pins (base implementation):
- --game-ink on --game-panel: 13.9647:1 (>= 7:1, AAA)
- --game-ink on --game-bg:    12.4504:1 (>= 7:1, AAA)
- --game-dim on --game-panel:  6.3972:1 (>= 4.5:1, AA)
- primary CTA text (--game-panel) on --game-accent: 6.0173:1 (>= 4.5:1, AA)
- danger text (--game-critical) on --game-panel:     6.2090:1 (>= 4.5:1, AA)

The two CTA/danger pairs are read from the ACTUAL rules (.game-action-primary
and .game-action-danger + the .game-action base background), not assumed —
a rule that swaps tokens is caught both by the resolved color and the pin.

RED on base 99f38fa (no .game-shell tokens to measure); green with the
#306 implementation in the tree.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSS_PATH = ROOT / "backend" / "app" / "web" / "style.css"

# (pinned ratio, WCAG minimum) per (fg, bg) token pair.
PINNED = {
    ("--game-ink", "--game-panel"): (13.9647, 7.0),
    ("--game-ink", "--game-bg"): (12.4504, 7.0),
    ("--game-dim", "--game-panel"): (6.3972, 4.5),
    # CTA / danger pairs (read from the actual rules, see tests below).
    ("--game-panel", "--game-accent"): (6.0173, 4.5),
    ("--game-critical", "--game-panel"): (6.2090, 4.5),
}


def _lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rel_luminance(rgb):
    r, g, b = rgb
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast_ratio(c1, c2):
    l1, l2 = rel_luminance(c1), rel_luminance(c2)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


def parse_hex(s):
    m = re.match(r"^#([0-9a-fA-F]{6})$", s.strip())
    assert m, f"expected #rrggbb, got {s!r}"
    h = m.group(1)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _game_shell_block(css):
    i = css.index(".game-shell {")
    depth, j = 1, i + len(".game-shell {")
    while j < len(css) and depth:
        if css[j] == "{":
            depth += 1
        elif css[j] == "}":
            depth -= 1
        j += 1
    return css[i:j]


def _token_value(css, name):
    """Resolve a .game-shell custom property (var() refs stay in-scope)."""
    block = _game_shell_block(css)
    m = re.search(re.escape(name) + r"\s*:\s*([^;]+);", block)
    assert m, f"{name} missing from the .game-shell block"
    v = m.group(1).strip()
    for _ in range(3):
        vm = re.match(r"^var\((--[\w-]+)\)$", v)
        if not vm:
            return v
        m2 = re.search(re.escape(vm.group(1)) + r"\s*:\s*([^;]+);", block)
        assert m2, f"unresolvable var({vm.group(1)}) inside .game-shell"
        v = m2.group(1).strip()
    return v


def _rule_body(css, selector):
    """Body of the first `selector { ... }` rule, brace-matched."""
    i = css.index(selector + " {")
    depth, j = 1, i + len(selector) + 2
    while j < len(css) and depth:
        if css[j] == "{":
            depth += 1
        elif css[j] == "}":
            depth -= 1
        j += 1
    return css[i + len(selector) + 2 : j - 1]


def _prop(body, name):
    """A declaration's value, anchored so `color` never matches `border-color`."""
    m = re.search(r"(?:^|;)\s*" + re.escape(name) + r"\s*:\s*([^;]+);", body)
    assert m, f"property {name!r} not found in rule body"
    return m.group(1).strip()


def _resolve_color(css, value):
    """A literal #hex or var(--token) color value -> (r, g, b)."""
    v = value.strip()
    m = re.match(r"^var\((--[\w-]+)\)$", v)
    if m:
        v = _token_value(css, m.group(1))
    return parse_hex(v)


def _ratio(fg, bg):
    css = CSS_PATH.read_text(encoding="utf-8")
    return contrast_ratio(parse_hex(_token_value(css, fg)),
                           parse_hex(_token_value(css, bg)))


def _check(fg, bg):
    ratio = _ratio(fg, bg)
    pinned, minimum = PINNED[(fg, bg)]
    assert ratio >= minimum, f"{fg} on {bg} is {ratio:.2f}:1, below {minimum}:1"
    assert abs(ratio - pinned) < 0.01, (
        f"{fg}/{bg} ratio drifted: {ratio:.4f} != pinned {pinned}"
    )
    return ratio


def test_ink_on_panel():
    _check("--game-ink", "--game-panel")


def test_ink_on_bg():
    _check("--game-ink", "--game-bg")


def test_dim_on_panel():
    _check("--game-dim", "--game-panel")


def test_primary_cta_text_on_accent():
    # .game-action-primary: panel/white text on the accent background.
    css = CSS_PATH.read_text(encoding="utf-8")
    body = _rule_body(css, ".game-action-primary")
    fg = _resolve_color(css, _prop(body, "color"))
    bg = _resolve_color(css, _prop(body, "background"))
    ratio = contrast_ratio(fg, bg)
    pinned, minimum = PINNED[("--game-panel", "--game-accent")]
    assert ratio >= minimum, (
        f"primary CTA text on accent is {ratio:.2f}:1, below {minimum}:1"
    )
    assert abs(ratio - pinned) < 0.01, (
        f"primary CTA ratio drifted: {ratio:.4f} != pinned {pinned}"
    )


def test_danger_text_on_panel():
    # .game-action-danger: critical text; the background is inherited from
    # the .game-action base rule (danger sets no background of its own).
    css = CSS_PATH.read_text(encoding="utf-8")
    fg = _resolve_color(css, _prop(_rule_body(css, ".game-action-danger"), "color"))
    bg = _resolve_color(css, _prop(_rule_body(css, ".game-action"), "background"))
    ratio = contrast_ratio(fg, bg)
    pinned, minimum = PINNED[("--game-critical", "--game-panel")]
    assert ratio >= minimum, (
        f"danger text on panel is {ratio:.2f}:1, below {minimum}:1"
    )
    assert abs(ratio - pinned) < 0.01, (
        f"danger text ratio drifted: {ratio:.4f} != pinned {pinned}"
    )
