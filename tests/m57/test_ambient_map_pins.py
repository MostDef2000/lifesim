"""Issue #57: ambient map effects (tree sway, surf, smoke, birds).

Byte-pin style (m146 lineage): python reads app.js/style.css as text and
pins behavior-adjacent static facts — config values, gate/LOD logic
strings, DOM insertion order, pointer-events, CSS keyframes/reduced-motion
neutralization/rest states. All assertions active; nothing mocked.

Scope of the pins:
- MAP_AMBIENT config: 3 tree zones (T1-T3), 4 surf segments (S1-S4),
  2 smoke sources (kitchen/workshop), 2 bird corridors (B1-B2) with the
  exact coordinates under review;
- gating: quality low-mobile -> null, reduced motion -> null, smoke
  gated by MAP_AMBIENT.flags.smoke;
- LOD: surf at every zoom; trees+smoke excluded on island; birds
  excluded on local;
- z-order: ambient layer slot right after .map-base img and below
  tint/lights/markers/weather (z-index 0 vs 1/2/4/5);
- .map-ambient is pointer-events: none (never intercepts input);
- CSS: 4 keyframe groups; .map-ambient neutralized in BOTH
  reduced-motion blocks; surf/smoke/bird rest state is opacity 0;
  OS-level reduce additionally hides frozen tree overlays (media block
  only — the class path relies on JS nulling the whole layer);
- provenance comment (tentative zones + smoke flag note) stays;
- syntax gate: node --check app.js.
"""
import os
import subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
APP_CSS = os.path.join(ROOT, "backend/app/web/style.css")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _split_once(text, sep, note):
    assert sep in text, f"expected {note}: missing marker {sep!r}"
    return text.split(sep, 1)[1]


def _js_branch_body(src, gate):
    """Static brace-match: body of the first `if (<gate>) {` block.

    Proves actual branch membership — a restructure that empties the
    branch while leaving the emission text between the two gate lines
    yields an empty body here and fails the caller's containment pins.
    """
    assert gate in src, f"missing gate line {gate!r}"
    start = src.index(gate) + len(gate)  # just past the opening '{'
    depth, i = 1, start
    while depth:
        assert i < len(src), f"unbalanced braces after gate {gate!r}"
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
        i += 1
    return src[start:i - 1]


def test_map_ambient_config_zones():
    """#57: MAP_AMBIENT config — 3 trees + 4 surf + 2 smoke + 2 birds, pinned coords."""
    js = _read(APP_JS)

    assert "const MAP_AMBIENT = {" in js, "#57: MAP_AMBIENT config missing from app.js"
    block = _split_once(js, "const MAP_AMBIENT = {", "MAP_AMBIENT config").split("\n};", 1)[0]

    # Smoke sources are flag-gated (weak visual confirmation — flip off if wrong).
    assert "flags: { smoke: true }," in block, "#57: MAP_AMBIENT.flags.smoke gate flag missing"

    trees = _split_once(block, "trees: [", "trees array").split("],", 1)[0]
    assert trees.count("// T") == 3, "#57: expected exactly 3 tree zones (T1-T3)"
    assert (
        "{ x: 35, y: 18, w: 30, h: 22 }, // T1 north interior" in trees
    ), "#57: tree zone T1 (north interior) coords changed"

    surf = _split_once(block, "surf: [", "surf array").split("],", 1)[0]
    assert surf.count("x1:") == 4, "#57: expected exactly 4 surf segments (S1-S4)"
    assert (
        "{ x1: 30, y1: 14, x2: 69, y2: 13 }, // S1 north" in surf
    ), "#57: north surf segment S1 (30,14)->(69,13) changed"
    assert (
        "{ x1: 31, y1: 73, x2: 67, y2: 72 }, // S4 south" in surf
    ), "#57: south surf segment S4 (31,73)->(67,72) changed"

    smoke = _split_once(block, "smoke: [", "smoke array").split("],", 1)[0]
    assert smoke.count("{ x:") == 2, "#57: expected exactly 2 smoke sources (kitchen+workshop)"
    assert "{ x: 46.79, y: 51.4 }, // kitchen" in smoke, "#57: kitchen smoke source coords changed"
    assert (
        "{ x: 42.89, y: 56.0 }, // workshop" in smoke
    ), "#57: workshop smoke source coords changed"

    birds = _split_once(block, "birds: [", "birds array").split("],", 1)[0]
    assert birds.count("x1:") == 2, "#57: expected exactly 2 bird corridors (B1-B2)"
    assert (
        "{ x1: 15, y1: 86, x2: 83, y2: 85 }, // B2 south, band y 84-88%" in birds
    ), "#57: south bird corridor B2 (band y 84-88%) changed"
    assert (
        "{ x1: 12, y1: 9, x2: 86, y2: 8 }, // B1 north, band y 4-12%" in birds
    ), "#57: north bird corridor B1 (band y 4-12%) changed"

    # Provenance: tentative zones + smoke flag note stay attached to the config.
    assert "first-queue ambient effects" in js, "#57: provenance comment (issue scope) missing"
    assert "tree zones are tentative" in js, "#57: provenance note (tentative tree zones) missing"
    assert "gated by flags.smoke" in js, "#57: provenance note (smoke flag) missing"


def test_map_ambient_gating_and_lod():
    """#57: low-mobile/reduced-motion -> null; smoke flag gate; per-effect LOD."""
    js = _read(APP_JS)

    fn = _split_once(js, "function buildMapAmbient(ctx) {", "buildMapAmbient()").split(
        "\nfunction ", 1
    )[0]
    # Hard gate: nothing at quality low-mobile or when reduced motion is on.
    assert (
        'if (ctx.quality === "low-mobile" || ctx.reduced) return null;' in fn
    ), "#57: buildMapAmbient must return null for low-mobile quality OR reduced motion"

    # LOD gates: surf at every zoom; trees+smoke not on island; birds not on local.
    # Branch membership is proven by brace-matching the gate bodies (a text-
    # order pin alone vacuous-passes an emptied island branch whose emission
    # text merely sits between the two gate lines), plus exactly-once counts
    # so no duplicate un-gated emission can hide outside the branches.
    island_body = _js_branch_body(fn, 'if (ctx.lod !== "island") {')
    local_body = _js_branch_body(fn, 'if (ctx.lod !== "local") {')
    assert (
        "MAP_AMBIENT.trees.forEach" in island_body
    ), "#57: trees forEach must be INSIDE the lod!==island branch body"
    assert (
        "MAP_AMBIENT.smoke.forEach" in island_body
    ), "#57: smoke forEach must be INSIDE the lod!==island branch body"
    assert (
        "if (MAP_AMBIENT.flags.smoke)" in island_body
    ), "#57: flags.smoke gate must be INSIDE the lod!==island branch body"
    assert (
        "MAP_AMBIENT.birds.forEach" in local_body
    ), "#57: birds forEach must be INSIDE the lod!==local branch body"
    assert (
        js.count("MAP_AMBIENT.trees.forEach") == 1
    ), "#57: trees.forEach must appear exactly once in app.js (no un-gated duplicate)"
    assert (
        js.count("MAP_AMBIENT.smoke.forEach") == 1
    ), "#57: smoke.forEach must appear exactly once in app.js (no un-gated duplicate)"
    assert (
        js.count("MAP_AMBIENT.birds.forEach") == 1
    ), "#57: birds.forEach must appear exactly once in app.js (no un-gated duplicate)"
    # Surf is emitted before the LOD branches: visible at EVERY zoom.
    assert (
        fn.index("MAP_AMBIENT.surf") < fn.index('if (ctx.lod !== "island")')
    ), "#57: surf must render at EVERY zoom (emitted before LOD branches)"
    assert js.count("MAP_AMBIENT.surf") == 1, "#57: surf emission must appear exactly once"

    # Smoke element is centered on its source by offsetting half its size.
    assert (
        "style: `left:${src.x - 1}%; top:${src.y - 1.4}%;`" in fn
    ), "#57: smoke placement must offset half-size (src.x-1, src.y-1.4) to center on source"


def test_map_ambient_layer_slot_and_input_safety():
    """#57: layer slot after base img, below tint/lights/markers/weather, never interactive."""
    js = _read(APP_JS)
    css = _read(APP_CSS)

    living = _split_once(js, "function buildLivingMap(", "buildLivingMap()")
    base_at = living.index('class: "map-base"')
    ambient_at = living.index("buildMapAmbient(")
    tint_at = living.index('class: "map-time-tint"')
    assert (
        base_at < ambient_at < tint_at
    ), "#57: ambient layer must be inserted after .map-base and before .map-time-tint"
    assert (
        "buildMapAmbient({ lod: S.mapLod, quality: S.mapQuality, reduced: !!S.mapReducedMotion })"
        in living
    ), "#57: buildMapAmbient call site must pass live lod/quality/reduced-motion state"

    ambient_rule = _split_once(css, ".map-ambient {", ".map-ambient rule").split("}", 1)[0]
    assert (
        "pointer-events: none" in ambient_rule
    ), "#57: .map-ambient must be pointer-events: none (never intercept taps)"
    assert (
        "z-index: 0" in ambient_rule
    ), "#57: .map-ambient must be z-index 0 (below tint/lights/markers/weather)"
    # The stacking context it must stay under: tint 1, lights 2, markers 4, weather 5.
    assert ".map-time-tint { z-index: 1; }" in css, "#57: tint layer z-index 1 pin broken"
    assert (
        ".map-night-lights { z-index: 2; opacity: 0; }" in css
    ), "#57: lights layer z-index 2 pin broken"
    marker_rule = _split_once(css, ".map-dynamic-marker {", ".map-dynamic-marker rule").split(
        "}", 1
    )[0]
    assert "z-index: 4" in marker_rule, "#57: markers must stay above ambient (z-index 4)"
    assert (
        ".map-rain, .map-fog { z-index: 5; }" in css
    ), "#57: weather must stay above ambient (z-index 5)"


def test_map_ambient_css_pins():
    """#57: 4 keyframes; BOTH reduced-motion blocks neutralize; rest states invisible."""
    css = _read(APP_CSS)

    for name in ("map-ambient-sway", "map-ambient-surf", "map-ambient-smoke", "map-ambient-bird"):
        marker = f"@keyframes {name} {{"
        assert css.count(marker) == 1, f"#57: expected exactly one {marker!r} keyframe group"

    # .map-ambient must be neutralized in BOTH reduced-motion blocks.
    rm_class = _split_once(
        css, ".map-card.reduced-motion .map-stage,", "class-based reduced-motion block"
    ).split("{", 1)[0]
    assert (
        ".map-card.reduced-motion .map-ambient," in rm_class
    ), "#57: .map-ambient missing from .reduced-motion class neutralization block"
    assert (
        ".map-card.reduced-motion .map-ambient *" in rm_class
    ), "#57: .map-ambient * children missing from .reduced-motion class block"
    rm_media = _split_once(
        css,
        "@media (prefers-reduced-motion: reduce) {\n  .map-stage,",
        "map prefers-reduced-motion media block",
    ).split("animation", 1)[0]
    assert (
        ".map-ambient," in rm_media
    ), "#57: .map-ambient missing from @media prefers-reduced-motion block"
    assert (
        ".map-ambient *" in rm_media
    ), "#57: .map-ambient * children missing from @media prefers-reduced-motion block"
    rm_class_rules = css.split(".map-card.reduced-motion .map-stage,", 1)[1].split("}", 1)[0]
    assert (
        "animation: none !important" in rm_class_rules
    ), "#57: reduced-motion class block must disable animations"
    rm_media_rules = css.split(
        "@media (prefers-reduced-motion: reduce) {\n  .map-stage,", 1
    )[1].split("\n}", 1)[0]
    assert (
        "animation: none !important" in rm_media_rules
    ), "#57: reduced-motion media block must disable animations"

    # Animated effects rest invisible: animation:none leaves nothing odd on screen.
    for cls in (".map-ambient-surf {", ".map-ambient-smoke {", ".map-ambient-bird {"):
        rule = _split_once(css, cls, cls).split("}", 1)[0]
        assert "opacity: 0" in rule, f"#57: {cls[:-2]} rest state must be opacity: 0"
    tree_rule = _split_once(css, ".map-ambient-tree {", ".map-ambient-tree rule").split("}", 1)[0]
    assert "opacity: .58" in tree_rule, "#57: tree sway rest state must stay the subtle .58 overlay"


def test_map_ambient_reduced_motion_tree_hide():
    """#57: OS-level reduce hides frozen trees (media block); class path relies on JS nulling."""
    css = _read(APP_CSS)

    # The map @media prefers-reduced-motion block (identified by its
    # .map-stage selector, since two other prefers-reduced-motion blocks
    # exist for landing/typing) must hide tree zones: with OS reduce on
    # but the in-app toggle off, animations freeze and the .58-opacity
    # tree overlays would otherwise linger as frozen smudges.
    media_body = _split_once(
        css,
        "@media (prefers-reduced-motion: reduce) {\n  .map-stage,",
        "map prefers-reduced-motion media block",
    ).split("\n}", 1)[0]
    tree_hide = _split_once(
        media_body, ".map-ambient-tree {", "media-block tree-hide rule"
    ).split("}", 1)[0]
    assert (
        "opacity: 0 !important" in tree_hide
    ), "#57: media reduced-motion block must force .map-ambient-tree opacity 0 !important"

    # Negative pin: the .reduced-motion CLASS block has no tree-hide rule —
    # that path nulls the whole ambient layer in JS (buildMapAmbient returns
    # null when ctx.reduced), so no DOM node exists to freeze. If this
    # deliberately changes (belt-and-suspenders), update this pin consciously.
    class_block = _split_once(
        css, ".map-card.reduced-motion .map-stage,", "class-based reduced-motion block"
    ).split("\n}", 1)[0]
    assert (
        ".map-ambient-tree" not in class_block
    ), "#57: class reduced-motion block must NOT tree-hide (JS nulls the layer instead)"


def test_map_ambient_syntax_gate():
    """#57: app.js stays parseable after the ambient layer addition (m146 node --check pattern)."""
    result = subprocess.run(
        ["node", "--check", "backend/app/web/app.js"],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
