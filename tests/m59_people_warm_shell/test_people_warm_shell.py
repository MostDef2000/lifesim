"""m59 wave 023 — People screen (#/people) joins the provisional warm
cream/beige token family (VISUAL-BIBLE §48; the final palette is issue #55,
NOT this change): the same family .profile-shell (PR #5) carries.

CI truth = SOURCE-STRUCTURE pins only (style.css read as text) — NOT
rendered geometry. Actual computed style / contrast / meter geometry is the
manual browser truth (browser_acceptance.js, parent-run via native MCP,
NOT collected by pytest, NOT run by CI).

Scope: the EXISTING #69 people block (style.css, from the
"/* ---------- issue #69: people screen" marker to the "/* #107:" marker).
Only its token values + container paint + panel/hero/link inks migrate
dark→cream (profile-equivalent values). app.js is byte-untouched.

Pin groups (m173 house style, region-scoped):
- "cream pins" must FAIL on base 323d9c00 (old dark mirror values) and pass
  after the CSS change;
- "family invariants" re-state pre-existing guards that must SURVIVE (the
  3 m69 meter override selectors count==1, the --w-rel-hostile mirror, the
  --w-line count == 4, the byte-untouched :root fallback, the conn-banner
  tail) — these pass on base and must stay green.

Value pins are region-scoped: the cream tokens also live in the other
shells (file-wide counts would over-count), and the warm trio
(--w-warm/--w-warm-line/--w-warm-ink) is IDENTICAL in dark and cream, so
only the 6 changed tokens are value-pinned; the old dark values are pinned
ABSENT from the block body (they remain in the other shells).
"""

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_CSS = os.path.join(ROOT, "backend/app/web/style.css")

BLOCK_OPEN = "/* ---------- issue #69: people screen"
BLOCK_CLOSE = "/* #107:"

# The 6 profile-equivalent cream token values (.profile-shell precedent).
CREAM = {
    "--w-bg": "#f3ecdf",
    "--w-panel": "rgba(255, 253, 248, 0.92)",
    "--w-panel-deep": "rgba(236, 224, 204, 0.7)",
    "--w-text": "#2a2118",
    "--w-dim": "#6e5f4e",
    "--w-accent": "#c98a2e",
}
# The old dark mirror values that must LEAVE the block body (the hero's
# dark linear stop rgba(23, 33, 42, 0.55) included).
DARK = ("#0a1016", "rgba(23, 33, 42, 0.72)", "rgba(23, 33, 42, 0.55)",
        "rgba(12, 19, 25, 0.6)", "#dde5ec", "#8fa1b1", "#e2b45a")
# The warm trio is byte-UNCHANGED by the migration (identical in both).
WARM_TRIO = ("--w-warm: rgba(226, 180, 90, 0.15);",
             "--w-warm-line: rgba(226, 180, 90, 0.4);",
             "--w-warm-ink: #1d1506;")
HOSTILE = "--w-rel-hostile: #e07070;"
WARM_BORDER = "rgba(120, 96, 64, 0.35)"
LINK_INK = "#8a5a16"
HERO_LINEAR = "linear-gradient(180deg, rgba(255, 253, 248, 0.9), var(--w-panel-deep))"


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _people_block(css):
    """The #69 block: header comment through the #107 marker."""
    return css.split(BLOCK_OPEN, 1)[1].split(BLOCK_CLOSE, 1)[0]


def _people_body(css):
    """Block BODY: after the header comment's closing */ — the stale header
    prose must not feed the pins."""
    return _people_block(css).split("*/", 1)[1]


def _rule(css, selector):
    """First rule body for an exact selector (brace-balanced)."""
    i = css.index(selector)
    j = css.index("{", i)
    depth, k = 0, j
    while True:
        if css[k] == "{":
            depth += 1
        elif css[k] == "}":
            depth -= 1
            if depth == 0:
                return css[j + 1:k]
        k += 1


def test_people_warm_cream_pins():
    """023 W2/W3: the people shell carries the profile-equivalent cream
    tokens + container paint + flat warm panels + warm hero + link ink.
    RED on base 323d9c00 (the old dark mirror values)."""
    css = _read(APP_CSS)
    assert css.count(BLOCK_OPEN) == 1
    body = _people_body(css)
    shell = _rule(css, ".people-shell {")
    # (1) The 6 profile-equivalent cream token values, each exactly once in
    #     the .people-shell rule (the warm trio is unchanged — pinned in the
    #     invariants test, not here).
    for var, val in CREAM.items():
        assert shell.count(f"{var}: {val};") == 1, f"{var} must be {val}"
    # (2) The old dark mirror values are GONE from the block body.
    for dark in DARK:
        assert dark not in body, f"old dark value {dark!r} must leave the block"
    # (3) Container paint: --w-bg/--w-text live + the global --dim remap
    #     (profile precedent, the desire lesson).
    for decl in ("background: var(--w-bg);", "color: var(--w-text);",
                 "--dim: var(--w-dim);"):
        assert decl in shell, f".people-shell must paint {decl!r}"
    # (4) Flat panels: warm border, NO blur/shadow (the dark block had
    #     backdrop-filter: blur(10px) + box-shadow).
    panel = _rule(css, ".people-shell .panel {")
    assert f"border-color: {WARM_BORDER};" in panel
    assert "backdrop-filter" not in panel and "box-shadow" not in panel
    # (5) Hero: the warm light linear (profile precedent bytes).
    assert HERO_LINEAR in _rule(css, ".people-hero {")
    # (6) Links: readable warm ink, not accent-as-text.
    assert f"color: {LINK_INK};" in _rule(css, ".people-shell a {")


def test_people_family_invariants():
    """Guards that pass on base and must SURVIVE the cream migration:
    the signed-meter overrides (m69), the hostile mirror, the --w-line
    token count, the byte-untouched :root fallback, the conn-banner tail."""
    css = _read(APP_CSS)
    body = _people_body(css)
    # (1) The 3 signed-meter override selectors, verbatim, exactly once
    #     (m69 test_people_meter_overrides_beat_global_need_fill).
    assert css.count(".people-shell .need .bar > .people-mid") == 1
    assert css.count(".people-shell .need .bar > .people-fill {") == 1
    assert css.count(".people-shell .need .bar > .people-fill.neg") == 1
    # (2) The hostile red mirror stays in the block (the chat-shell-scoped
    #     token would resolve to transparent without it) + the warm trio is
    #     byte-unchanged.
    assert HOSTILE in body
    for decl in WARM_TRIO:
        assert decl in body
    # (3) NO --w-line definition in the block; the file-wide count stays 4
    #     (:root + .world-shell + .desire-shell + .shop-shell).
    assert body.count("--w-line:") == 0
    assert css.count("--w-line:") == 4
    # (4) The :root fallback stays the OLD cool value, byte-untouched.
    root = css.split(":root {", 1)[1].split("\n}", 1)[0]
    assert "--w-line: rgba(148, 178, 205, 0.16);" in root
    # (5) The connection-banner tail rule stays the file's last rule.
    assert css.rstrip().endswith(".conn-banner.hidden { display: none; }")
