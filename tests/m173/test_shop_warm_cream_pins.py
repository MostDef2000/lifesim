"""#59 PR #3 — Shop / Trade screen (#/shop) joins the provisional warm
cream/beige token family (VISUAL-BIBLE §48), the same family .desire-shell
carried since PR #1 and .chat-shell since PR #2.

Mechanism differs from the chat wave (PR #2): chat replaced its
var(--w-line) consumers with EXPLICIT warm literals and defined NO --w-line
of its own. Shop KEEPS its own --w-line token (m170/m173/m175 pin the
file-wide --w-line count at exactly 4 — a fifth definition is forbidden) and
migrates its VALUE in place to the warm card-line strength
rgba(120, 96, 64, 0.35), because every shop line is a card line
(panel / offer-row / form-field).

Pin groups (m170/m173 house style — exact literal counts, region-scoped):
- "warm cream pins" must FAIL on base (old dark mirror values) and pass
  after the implementation;
- "family invariants" re-state the pre-existing guards that must SURVIVE
  the rework (--w-line count == 4, card-line borders, the primary-button
  rule, the unchanged --w-warm trio) — these may pass on base;
- "stale comment fix": the app.js renderShop comment names #59 PR #3
  (the old "mirrors the world-shell token set" wording is gone).

Value pins are region-scoped (m170 technique): the cream tokens also live
in .desire-shell and .chat-shell, so file-wide counts would over-count; the
old dark values are pinned ABSENT from the shop section (they remain in the
other shells that get their own #59 PRs).
"""

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
APP_CSS = os.path.join(ROOT, "backend/app/web/style.css")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _shop173_block(css):
    """The #173 shop section: from the Рынок header comment to the
    offer-rows marker. The offer rows keep the warm tokens via
    var(--w-line) and are not part of the shell token block, so the
    old-value-ABSENCE pins scope to this tighter slice (they must not see
    the later shells, which deliberately keep the old dark values until
    their own #59 PRs)."""
    return css.split("/* #173: Рынок (#/shop)", 1)[1].split(
        "/* #173: offer rows", 1)[0]


def test_shop_warm_cream_pins():
    """#59 PR #3: the shop shell carries the provisional warm cream/beige
    token family. Value pins are region-scoped to the .shop-shell rule
    (the cream tokens also live in .desire-shell/.chat-shell, so file-wide
    counts would over-count); the old dark mirror values are pinned ABSENT
    from the whole shop section."""
    css = _read(APP_CSS)
    # One shell definition, one section header.
    assert css.count(".shop-shell {") == 1
    assert css.count("/* #173: Рынок (#/shop)") == 1
    # The .shop-shell rule region: the tokens + container paint are defined
    # here and nowhere else in the rule.
    shell = css[css.index(".shop-shell {"):
                css.index("}", css.index(".shop-shell {")) + 1]
    # (1) The 7 provisional cream token values, each exactly once. Shop
    #     KEEPS its own --w-line token (unlike chat's explicit literals) —
    #     its value migrates in place to the warm card-line strength.
    for token in ("--w-bg: #f3ecdf;",
                  "--w-panel: rgba(255, 253, 248, 0.92);",
                  "--w-panel-deep: rgba(236, 224, 204, 0.7);",
                  "--w-line: rgba(120, 96, 64, 0.35);",
                  "--w-text: #2a2118;",
                  "--w-dim: #6e5f4e;",
                  "--w-accent: #c98a2e;"):
        assert shell.count(token) == 1, token
    # (2) Container paint (the desire lesson): the global muted token is
    #     remapped for this scope and the container is painted flat so
    #     --w-bg/--w-text are live.
    for paint in ("--dim: var(--w-dim);",
                  "background: var(--w-bg);",
                  "color: var(--w-text);"):
        assert shell.count(paint) == 1, paint
    # (3) Accent-as-text fails on cream — the warm dark-gold ink #8a5a16
    #     replaces the inherited global inks for links and money (desire
    #     precedent), each exactly once.
    assert css.count(".shop-shell a { color: #8a5a16; }") == 1
    assert css.count(".shop-shell .world-money { color: #8a5a16; }") == 1
    # (4) Secondary buttons go warm instead of the global dark-cool box
    #     (the accent/primary button keeps its own rule, pinned in the
    #     family invariants below).
    assert css.count(".shop-shell button:not(.primary) {") == 1
    sec_btn = css[css.index(".shop-shell button:not(.primary) {"):
                  css.index("}", css.index(".shop-shell button:not(.primary) {")) + 1]
    assert sec_btn.count("background: var(--w-panel-deep);") == 1
    hover_rule = ".shop-shell button:not(.primary):hover { border-color: var(--w-accent); }"
    assert css.count(hover_rule) == 1
    # (5) The old dark mirror values are ABSENT from the shop section (they
    #     remain in the other shells — pinned == 0 here so the dark mirror
    #     cannot silently return to shop).
    sec = _shop173_block(css)
    for old in ("#0a1016",
                "rgba(23, 33, 42, 0.72)",
                "rgba(12, 19, 25, 0.6)",
                "rgba(148, 178, 205, 0.16)",
                "#dde5ec",
                "#8fa1b1",
                "#e2b45a"):
        assert sec.count(old) == 0, old
    # (6) The section header comment carries the PROVISIONAL §48 marker
    #     (series parity with chat's m63 pin — each #59 warm-pass screen
    #     carries its own marker, so the count is block-scoped).
    assert sec.count("PROVISIONAL per VISUAL-BIBLE §48") == 1


def test_shop_family_invariants():
    """Pre-existing guards that must SURVIVE the warm rework (regression
    guards — these may pass on base)."""
    css = _read(APP_CSS)
    shell = css[css.index(".shop-shell {"):
                css.index("}", css.index(".shop-shell {")) + 1]
    # The --w-line token count stays exactly 4 (:root + .world-shell +
    # .desire-shell + .shop-shell) — shop migrates its value in place, it
    # does NOT add a fifth definition.
    assert css.count("--w-line:") == 4
    # Card-line borders survive (offer-row + form-field + ...).
    assert css.count("border: 1px solid var(--w-line);") >= 2
    # The primary (accent) button rule is untouched by the rework.
    assert css.count(".shop-shell button.primary {") == 1
    # The --w-warm trio values are unchanged (the rework only migrates the
    # 7 cream tokens + adds the container paint / inks / secondary buttons).
    assert shell.count("--w-warm: rgba(226, 180, 90, 0.15);") == 1
    assert shell.count("--w-warm-line: rgba(226, 180, 90, 0.4);") == 1
    assert shell.count("--w-warm-ink: #1d1506;") == 1


def test_app_js_stale_comment_fix():
    """The renderShop comment names #59 PR #3 (the stale "mirrors the
    world-shell token set" wording is replaced by the provisional warm
    cream family note)."""
    js = _read(APP_JS)
    assert js.count("#59 PR #3") == 1
    # The old stale wording is gone.
    assert js.count("shop grid mirrors the world-shell token set") == 0
