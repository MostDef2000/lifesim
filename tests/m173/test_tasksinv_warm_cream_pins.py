"""#59 PR #4 — Tasks (#/tasks) + Inventory (#/inventory) join the
provisional warm cream/beige token family (VISUAL-BIBLE §48), the same
family .desire-shell (PR #1), .chat-shell (PR #2), and .shop-shell (PR #3)
carry.

Structurally different from the chat/shop waves: TWO screens share ONE CSS
section (style.css, header comment starting
"/* ---------- #59 PR #4: Дела (#/tasks) + Инвентарь (#/inventory)"), and
the shells define NO --w-line of their own (the file-wide --w-line count
stays 4; the :root fallback stays the OLD cool value, byte-pinned). The
base .task-row/.opp/.inv-item divider rules stay byte-untouched and are
recolored with scoped border-color overrides only. Two app.js class-string
changes ride the shells: "grid tasks-shell" (renderTasks) and
"panel inv-shell" (renderInventory).

Pin groups (m170/m173 house style — exact literal counts, region-scoped):
- "warm cream pins" must FAIL on base (the section is NEW) and pass after
  the implementation;
- "family invariants" re-state the pre-existing guards that must SURVIVE
  (--w-line count == 4, the byte-untouched base divider rules, the
  .conn-banner.hidden last rule) — these may pass on base.

Value pins are region-scoped (m170 technique): the cream tokens also live
in .desire-shell/.chat-shell/.shop-shell, so file-wide counts would
over-count; the old cool values are pinned ABSENT from the new section.
"""

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
APP_CSS = os.path.join(ROOT, "backend/app/web/style.css")

SEC_OPEN = "/* ---------- #59 PR #4: Дела (#/tasks) + Инвентарь (#/inventory)"
SEC_CLOSE = "/* ---------- issue #63: chat screen warm-family rework"


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _tasksinv59_block(css):
    """The #59 PR #4 section: from its header comment to the #63 chat
    header (which must stay after it). The section is NEW in this PR — on
    base the markers are absent and the slice is undefined."""
    return css.split(SEC_OPEN, 1)[1].split(SEC_CLOSE, 1)[0]


def test_tasksinv_warm_cream_pins():
    """#59 PR #4: the tasks + inventory shells carry the provisional warm
    cream/beige token family. Value pins are region-scoped to the shared
    token block (the cream tokens also live in the other shells, so
    file-wide counts would over-count); the old cool values are pinned
    ABSENT from the whole new section."""
    css = _read(APP_CSS)
    js = _read(APP_JS)
    # The section is NEW: one header, one shared token block.
    assert css.count(SEC_OPEN) == 1
    assert css.count(".tasks-shell, .inv-shell {") == 1
    # The shared token block region: the tokens + container paint are
    # defined here and nowhere else in the block. NO --w-line definition
    # (the file-wide count stays 4; the :root fallback stays cool).
    blk = css[css.index(".tasks-shell, .inv-shell {"):
              css.index("}", css.index(".tasks-shell, .inv-shell {")) + 1]
    # (1) The 6 provisional cream token values, each exactly once.
    for token in ("--w-bg: #f3ecdf;",
                  "--w-panel: rgba(255, 253, 248, 0.92);",
                  "--w-panel-deep: rgba(236, 224, 204, 0.7);",
                  "--w-text: #2a2118;",
                  "--w-dim: #6e5f4e;",
                  "--w-accent: #c98a2e;"):
        assert blk.count(token) == 1, token
    # (2) The --w-warm trio is carried into the shared block, unchanged.
    for token in ("--w-warm: rgba(226, 180, 90, 0.15);",
                  "--w-warm-line: rgba(226, 180, 90, 0.4);",
                  "--w-warm-ink: #1d1506;"):
        assert blk.count(token) == 1, token
    # (3) Container paint (the desire lesson): the global muted token is
    #     remapped for this scope and the text color is set so --w-text is
    #     live. (The ground/card split is pinned separately below — the
    #     shared block carries no background of its own.)
    for paint in ("--dim: var(--w-dim);",
                  "color: var(--w-text);"):
        assert blk.count(paint) == 1, paint
    # NO --w-line definition in the shared block (count stays 4 file-wide).
    assert blk.count("--w-line:") == 0
    # (4) Ground vs card: .tasks-shell is the grid ground; .inv-shell IS
    #     the panel (no grid wrapper), so it paints the card fill.
    assert css.count(".tasks-shell { background: var(--w-bg); }") == 1
    assert css.count(".inv-shell { background: var(--w-panel); "
                     "border-color: rgba(120, 96, 64, 0.35); }") == 1
    # (5) The tasks panel card: warm fill + warm literal border.
    assert css.count(".tasks-shell .panel { background: var(--w-panel); "
                     "border-color: rgba(120, 96, 64, 0.35); }") == 1
    # (6) Warm dark-gold link ink (desire precedent), exactly once.
    assert css.count(".tasks-shell a, .inv-shell a { color: #8a5a16; }") == 1
    # (7) Dividers: scoped recolor — the base .task-row/.opp/.inv-item
    #     rules (with their var(--w-line) shorthand) stay byte-untouched.
    assert css.count(".tasks-shell .task-row, .tasks-shell .opp, "
                     ".inv-shell .inv-item { border-color: rgba(120, 96, 64, 0.35); }") == 1
    # (8) Warm secondary buttons (both screens have only secondary
    #     buttons — none .primary): warm fields instead of the global
    #     dark-cool box.
    assert css.count(".tasks-shell button, .inv-shell button {") == 1
    btn = css[css.index(".tasks-shell button, .inv-shell button {"):
              css.index("}", css.index(".tasks-shell button, .inv-shell button {")) + 1]
    assert btn.count("background: var(--w-panel-deep);") == 1
    assert css.count(".tasks-shell button:hover, .inv-shell button:hover "
                     "{ border-color: var(--w-accent); }") == 1
    # (9) Inventory specifics: group headings, row hover tint, sell-row
    #     field.
    assert css.count(".inv-shell .inv-group h3 { color: var(--w-text); }") == 1
    assert css.count(".inv-shell .inv-item:hover { background: "
                     "rgba(120, 96, 64, 0.06); }") == 1
    assert css.count(".inv-shell .inv-sell-row input {") == 1
    inv = css[css.index(".inv-shell .inv-sell-row input {"):
              css.index("}", css.index(".inv-shell .inv-sell-row input {")) + 1]
    assert inv.count("background: var(--w-panel-deep);") == 1
    # (10) app.js: the two class-string changes ride the shells.
    assert js.count('class: "grid tasks-shell"') == 1
    assert js.count('"panel inv-shell"') == 1
    # (11) The old cool values are ABSENT from the new section (they remain
    #      in :root and the other cool screens — pinned == 0 here so the
    #      cool palette cannot leak into the warm shells).
    sec = _tasksinv59_block(css)
    for old in ("#4da3ff", "#1a2027", "#2a323c", "#8a94a3",
                "rgba(148, 178, 205"):
        assert sec.count(old) == 0, old
    # (12) The section header carries the PROVISIONAL §48 marker exactly
    #      once (block-scoped — series parity with the m63/m173 pins).
    assert sec.count("PROVISIONAL per VISUAL-BIBLE §48") == 1


def test_tasksinv_family_invariants():
    """Pre-existing guards that must SURVIVE the warm rework (regression
    guards — these may pass on base)."""
    css = _read(APP_CSS)
    # The --w-line token count stays exactly 4 (:root + .world-shell +
    # .desire-shell + .shop-shell) — the tasks/inv shells add NO --w-line
    # definition.
    assert css.count("--w-line:") == 4
    # The base divider rules stay byte-untouched (the scoped overrides
    # recolor them without editing the base rules).
    assert css.count(".task-row { display: flex; flex-wrap: wrap; gap: 4px; "
                     "align-items: center; padding: 4px 0; "
                     "border-bottom: 1px solid var(--w-line); }") == 1
    assert css.count(".opp { border-top: 1px solid var(--w-line); "
                     "padding: 6px 0; display: flex; flex-direction: column; "
                     "gap: 2px; }") == 1
    assert css.count(".inv-item { display: flex; gap: 8px; align-items: center; "
                     "padding: 4px 0; border-bottom: 1px solid var(--w-line); "
                     "cursor: pointer; }") == 1
    # The appended-only tail convention (m175) is respected.
    assert css.rstrip().endswith(".conn-banner.hidden { display: none; }")
