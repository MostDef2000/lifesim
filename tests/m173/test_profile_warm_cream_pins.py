"""#59 PR #5 — Profile screen (#/profile) joins the provisional warm
cream/beige token family (VISUAL-BIBLE §48), the same family .desire-shell
(PR #1), .chat-shell (PR #2), .shop-shell (PR #3), and .tasks-shell/
.inv-shell (PR #4) carry.

This is the CHAT precedent: CSS-only (app.js byte-untouched), token
migration dark→cream in the EXISTING .profile-shell block (style.css,
between the "issue #67: profile screen" and "issue #69: people screen"
markers), NO --w-line definition (the file-wide count stays 4; the :root
fallback stays the OLD cool value, byte-pinned), and the line consumers
use the explicit warm literal rgba(120, 96, 64, 0.35).

NEW vs previous waves:
- the block header comment was REWRITTEN (old text documented the dark
  mirror; new text documents the cream migration) — pinned via the new
  marker phrase "#59 PR #5" which appears in the block;
- a .profile-shell button.danger exception rule exists (the global red
  #b3402e/#93301f/#fff is kept — the warm secondary rule would otherwise
  repaint the destructive button);
- box-shadow: none; (hero portrait reset) is a DELIBERATE pre-existing
  declaration — a naive "no box-shadow in block" pin would false-positive;
  instead we pin "no backdrop-filter declaration" and "the only
  box-shadow declaration in the block body is box-shadow: none;".

Pin groups (m170/m173 house style — exact literal counts, region-scoped):
- "warm cream pins" must FAIL on base (old dark mirror values) and pass
  after the implementation;
- "family invariants" re-state the pre-existing guards that must SURVIVE
  (--w-line count == 4, the .conn-banner.hidden last rule, the byte-
  untouched :root fallback) — these may pass on base.

Value pins are region-scoped (m170 technique): the cream tokens also live
in the other shells, so file-wide counts would over-count; the old dark
values are pinned ABSENT from the block body (they remain in the other
shells that get their own #59 PRs).
"""

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_CSS = os.path.join(ROOT, "backend/app/web/style.css")

BLOCK_OPEN = "/* ---------- issue #67: profile screen"
BLOCK_CLOSE = "/* ---------- issue #69: people screen"


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _profile67_block(css):
    """The #67 profile block: from its header comment to the #69 people
    marker. The block is EXISTING (present on base) — only its token
    values and a few rules migrate in this PR."""
    return css.split(BLOCK_OPEN, 1)[1].split(BLOCK_CLOSE, 1)[0]


def _profile67_body(css):
    """The profile block BODY: everything after the header comment's
    closing */. The header comment itself mentions backdrop-filter /
    box-shadow (documenting the #268 panel discipline), so the
    no-backdrop-filter / box-shadow-only-none pins scope to the body, not
    the whole block."""
    return _profile67_block(css).split("*/", 1)[1]


def test_profile_warm_cream_pins():
    """#59 PR #5: the profile shell carries the provisional warm cream/
    beige token family. Value pins are region-scoped to the .profile-shell
    rule (the cream tokens also live in the other shells, so file-wide
    counts would over-count); the old dark mirror values are pinned ABSENT
    from the block body."""
    css = _read(APP_CSS)
    block = _profile67_block(css)
    body = _profile67_body(css)
    # The block is EXISTING: one header (the .profile-shell { opener also
    # appears in the ≤1100px media query, so it is not count-pinned — the
    # token block is the FIRST occurrence, extracted below).
    assert css.count(BLOCK_OPEN) == 1
    # (0) The header comment was REWRITTEN for the cream migration — the
    #     new marker phrase "#59 PR #5" appears in the block (5 sites:
    #     header + token block + container paint + inks + danger).
    assert block.count("#59 PR #5") == 5
    # The .profile-shell rule region: the tokens + container paint are
    # defined here and nowhere else in the rule. NO --w-line definition
    # (the file-wide count stays 4; the :root fallback stays cool).
    shell = css[css.index(".profile-shell {"):
                css.index("}", css.index(".profile-shell {")) + 1]
    # (1) The 6 provisional cream token values, each exactly once.
    for token in ("--w-bg: #f3ecdf;",
                  "--w-panel: rgba(255, 253, 248, 0.92);",
                  "--w-panel-deep: rgba(236, 224, 204, 0.7);",
                  "--w-text: #2a2118;",
                  "--w-dim: #6e5f4e;",
                  "--w-accent: #c98a2e;"):
        assert shell.count(token) == 1, token
    # (2) The --w-warm trio is carried in the shell, unchanged.
    for token in ("--w-warm: rgba(226, 180, 90, 0.15);",
                  "--w-warm-line: rgba(226, 180, 90, 0.4);",
                  "--w-warm-ink: #1d1506;"):
        assert shell.count(token) == 1, token
    # (3) Container paint (the desire lesson): the global muted token is
    #     remapped for this scope and the container is painted flat so
    #     --w-bg/--w-text are live.
    for paint in ("background: var(--w-bg);",
                  "color: var(--w-text);",
                  "--dim: var(--w-dim);"):
        assert shell.count(paint) == 1, paint
    # NO --w-line definition in the shell (count stays 4 file-wide).
    assert shell.count("--w-line:") == 0
    # (4) Panels: warm fill + warm literal border; flat (no backdrop-
    #     filter, no box-shadow — #268 panel discipline).
    panel = css[css.index(".profile-shell .panel {"):
                css.index("}", css.index(".profile-shell .panel {")) + 1]
    assert panel.count("border-color: rgba(120, 96, 64, 0.35);") == 1
    assert body.count("backdrop-filter") == 0
    # box-shadow: the ONLY box-shadow declaration in the block body is the
    # deliberate hero-portrait reset "box-shadow: none;" (a naive
    # "no box-shadow" pin would false-positive on that reset).
    assert body.count("box-shadow") == 1
    assert body.count("box-shadow: none;") == 1
    # (5) Headings + link ink: h2 reads the warm text token; the inherited
    #     global link ink is replaced by the warm dark-gold (desire/shop
    #     precedent).
    assert css.count(".profile-shell h2 { color: var(--w-text); }") == 1
    assert css.count(".profile-shell a { color: #8a5a16; }") == 1
    # (6) Secondary buttons go warm instead of the global dark-cool box
    #     (the accent/primary button keeps its own rule).
    assert css.count(".profile-shell button:not(.primary) {") == 1
    btn = css[css.index(".profile-shell button:not(.primary) {"):
              css.index("}", css.index(".profile-shell button:not(.primary) {")) + 1]
    assert btn.count("background: var(--w-panel-deep);") == 1
    assert btn.count("color: var(--w-text);") == 1
    assert btn.count("border-color: rgba(120, 96, 64, 0.35);") == 1
    # (7) The destructive affordance KEEPS its global red inside the shell
    #     (the warm secondary rule above would otherwise repaint it — same
    #     specificity, later source wins). Values are the global
    #     button.danger bytes, not new §48 tokens.
    assert css.count(".profile-shell button.danger {") == 1
    danger = css[css.index(".profile-shell button.danger {"):
                 css.index("}", css.index(".profile-shell button.danger {")) + 1]
    assert danger.count("background: #b3402e;") == 1
    assert danger.count("color: #fff;") == 1
    assert css.count(".profile-shell button.danger:hover { background: #93301f; }") == 1
    # (8) input/select: warm fields with the warm literal border.
    inp = css[css.index(".profile-shell input,"):
              css.index("}", css.index(".profile-shell input,")) + 1]
    assert inp.count("border: 1px solid rgba(120, 96, 64, 0.35);") == 1
    # (9) Hero band: the linear start goes warm (the radial warm glow is
    #     kept).
    hero = css[css.index(".profile-shell .profile-hero {"):
               css.index("}", css.index(".profile-shell .profile-hero {")) + 1]
    assert hero.count("linear-gradient(180deg, rgba(255, 253, 248, 0.9), "
                      "var(--w-panel-deep));") == 1
    assert hero.count("radial-gradient(640px 240px at 18% 0%, "
                      "rgba(226, 180, 90, 0.10), transparent 62%),") == 1
    # (10) Chips / trait / rows / roles: warm literal borders + the trait
    #      value ink.
    chip = css[css.index(".profile-shell .profile-chip {"):
               css.index("}", css.index(".profile-shell .profile-chip {")) + 1]
    assert chip.count("border: 1px solid rgba(120, 96, 64, 0.35);") == 1
    assert css.count(".profile-shell .profile-trait .profile-chip-v { color: #8a5a16; }") == 1
    row = css[css.index(".profile-shell .profile-row {"):
              css.index("}", css.index(".profile-shell .profile-row {")) + 1]
    assert row.count("border-bottom: 1px dashed rgba(120, 96, 64, 0.35);") == 1
    role = css[css.index(".profile-shell .profile-role {"):
               css.index("}", css.index(".profile-shell .profile-role {")) + 1]
    assert role.count("border: 1px solid rgba(120, 96, 64, 0.35);") == 1
    # (11) The old dark mirror values are ABSENT from the block body (they
    #      remain in the other shells — pinned == 0 here so the dark mirror
    #      cannot silently return to profile).
    for old in ("#0a1016",
                "rgba(23, 33, 42",
                "rgba(12, 19, 25",
                "#dde5ec",
                "#8fa1b1",
                "#e2b45a"):
        assert body.count(old) == 0, old
    # (12) The block carries the PROVISIONAL §48 marker exactly once
    #      (block-scoped — series parity with the m63/m173 pins).
    assert block.count("PROVISIONAL per VISUAL-BIBLE §48") == 1


def test_profile_family_invariants():
    """Pre-existing guards that must SURVIVE the warm rework (regression
    guards — these may pass on base)."""
    css = _read(APP_CSS)
    # The --w-line token count stays exactly 4 (:root + .world-shell +
    # .desire-shell + .shop-shell) — the profile shell adds NO --w-line
    # definition.
    assert css.count("--w-line:") == 4
    # The appended-only tail convention (m175) is respected.
    assert css.rstrip().endswith(".conn-banner.hidden { display: none; }")
    # The :root fallback stays the OLD cool value, byte-untouched (the
    # profile line consumers use the explicit warm literal instead).
    root = css.split(":root {", 1)[1].split("\n}", 1)[0]
    assert "--w-line: rgba(148, 178, 205, 0.16);" in root
