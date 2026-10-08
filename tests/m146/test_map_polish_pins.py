"""Issue #106 Lane A: touch-reachable labels + 24px hit targets.

House strategy (region/local) is a separate owner decision (#106).

Lane A scope only:
- item 4 — tap reveals a map label: pure-CSS ``:active`` reveal plus a JS
  ``touched`` class (one add / one remove call) so the label stays visible
  after a tap; houses keep their visible label suppressed (m21 aria path);
- item 1a — WCAG 2.5.8: anchor hit targets enlarged to >=24px (12px dot +
  8px ::before inset on every side) with zero visual change;
- item 6 — tab-stop audit is browser verification only (no code change).

Byte-pin style (m21/m121/m124/m128 lineage): previously pinned blocks stay
byte-exact; this lane only appends.
"""
import os
import subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
APP_CSS = os.path.join(ROOT, "backend/app/web/style.css")
WS_PY = os.path.join(ROOT, "backend/app/api/ws.py")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_map_polish_lane_a_pins():
    """Issue #106 Lane A: touch-reachable labels + 24px hit targets. House strategy (region/local) is a separate owner decision (#106)."""  # noqa: E501
    js = _read(APP_JS)
    css = _read(APP_CSS)
    ws = _read(WS_PY)

    # ---------- Change 1: touch label reveal (item 4) ----------
    # Exactly the strings this lane adds: one classList.remove("touched"),
    # one classList.add("touched"), wired through the el() on* ->
    # addEventListener path (onpointerdown prop). el() itself stays pinned.
    assert js.count('classList.remove("touched")') == 1
    assert js.count('classList.add("touched")') == 1
    assert js.count("onpointerdown: markTouchedAnchor") == 1
    # el() byte-pins untouched (m22:190 / m121:89).
    assert "for (const item of child) appendKids(node, item);" in js
    assert 'el("div", { class: "tabs" },' in js
    # houses keep their visible label suppressed (m21 aria-label path)
    assert '"aria-label": l.name' in js

    # ---------- Change 2: 24px hit targets (item 1a) ----------
    assert css.count(".anchor::before") == 1
    before_rule = css.split(".anchor::before {", 1)[1].split("}", 1)[0]
    assert "inset: -8px" in before_rule
    assert "position: absolute" in before_rule

    # ---------- reveal rules are append-only ----------
    assert css.count(".anchor:active .map-label") == 1
    assert css.count(".anchor.touched .map-label") == 1
    # pinned reveal trio still present (m21)
    assert ".anchor:hover .map-label" in css
    assert ".anchor:focus-visible .map-label" in css
    assert ".anchor.here .map-label" in css

    # ---------- pinned first-occurrence media blocks stay byte-exact ----------
    # m21/m121 parse the FIRST @media occurrence; this lane's new rules must
    # live strictly after both first blocks.
    grid_media = css.split("@media (max-width: 880px) {", 1)[1].split("\n}", 1)[0]
    assert grid_media == "\n  .grid { grid-template-columns: 1fr; }"
    small_media = css.split("@media (max-width: 520px) {", 1)[1].split("\n}", 1)[0]
    assert small_media == (
        "\n  .anchor .dot { width: 7px; height: 7px; }"
        "\n  .anchor .map-label { font-size: 9px; }"
    )
    assert "width: 7px" in small_media
    assert "font-size: 9px" in small_media
    assert css.index(".anchor::before {") > css.index("@media (max-width: 520px) {")

    # ---------- no collateral growth (m121/m124/m128 guards mirrored) ----------
    assert js.count("scene-view") == 3
    assert js.count('"Открыть Scene"') == 1
    assert js.count("innerHTML") == 0
    assert js.count('location.hash = "#/profile";') == 3
    assert ws.count('    @app.websocket("/ws")') == 1
    assert "MAP_LOW_MARKER_BUDGET = 120" in js

    # ---------- syntax gate (m121 subprocess pattern) ----------
    result = subprocess.run(
        ["node", "--check", "backend/app/web/app.js"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
