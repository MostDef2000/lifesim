"""Issue #106 Lane B: houses are ambient dots, not interactive anchors.

Owner verdict (recorded 2026-10-08), byte-pin style (m21/m121/m124/m128/
m131/m139/m146/m147 lineage): previously pinned blocks stay byte-exact;
this lane only appends.

- Region zoom: houses do NOT render at all — the exclusion now rides the
  shared ``positioned`` filter in visibleMapLocations, so island AND region
  both drop houses and local keeps them (mirrors the island LOD exclusion;
  visual bible §zoom-levels: no separate houses above the local level).
- Local zoom: houses render as NON-interactive spans (``anchor--house``):
  same 8px ``.anchor .dot`` visual, no button, no tabindex, no
  onpointerdown reveal, no ::before hit target, no MOVE wiring. Night
  lights + NPC clusters keep consuming the same visibleLocs set.
- NO interactive own-house anchor: the client cannot know the player's
  home id — ``Character.home_location_id`` exists (db/models.py) but is not
  exposed by any client payload (/characters/by-user, /characters/{cid},
  /auth/me, /world, /locations, POST /characters). Backend is out of scope
  for this lane, so path (b) applies: all 24 houses are spans and home
  MOVE from the map is dropped (alternative: profile screen / future pill).
- Lane A must not regress: ``onpointerdown: markTouchedAnchor`` stays
  count==1, the touched classList pins stay, and the 24px ::before hit
  target stays for POI anchors (now without the 24-house overlap — the
  point of this lane).
- Item 5 (SE-edge houses on the grass slope) stays visible as spans at
  local — terrain-reseed territory (Lane C), NOT fixed here.
"""
import os
import subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
APP_CSS = os.path.join(ROOT, "backend/app/web/style.css")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_lane_b_region_exclusion_pins():
    """Houses are excluded from the marker set at island AND region zoom."""
    js = _read(APP_JS)
    # One shared exclusion rides the positioned filter (island LOD already
    # dropped houses; region now does too — local keeps them, as spans).
    assert js.count('S.mapLod === "local" || l.type !== "house"') == 1
    # The island early-return no longer re-filters houses itself.
    assert js.count('if (S.mapLod === "island") return positioned;') == 1
    # The old island-only house filter is gone (no double filtering).
    assert 'return positioned.filter(l => l.type !== "house")' not in js
    # Region keeps its radius-28 POI geometry (no collateral zoom change).
    assert 'S.mapLod === "region" ? 28 : 14' in js


def test_lane_b_house_span_pins():
    """Local zoom: houses are non-interactive spans; POIs stay buttons."""
    js = _read(APP_JS)
    # Exactly one house-span render path.
    assert js.count('class: "anchor anchor--house"') == 1
    span_at = js.index('class: "anchor anchor--house"')
    # The span block carries no interactive wiring: no click handler, no
    # Lane A pointer reveal, no tab stop, no accessible name (ambient dot).
    span_block = js[span_at:js.index("const isHere", span_at)]
    assert "onclick" not in span_block
    assert "onpointerdown" not in span_block
    assert "tabindex" not in span_block
    assert '"aria-label"' not in span_block
    # Spans mount inside #map-anchors, painted BELOW the POI buttons.
    assert js.count("...houseSpans,") == 1
    assert js.index("...houseSpans,") < js.index("...locationAnchors,")
    # The old single-map button builder is gone (houses never reach it).
    assert js.count("const locationAnchors = [];") == 1
    # Night lights + NPC clusters keep consuming visibleLocs (houses feed
    # the local-zoom night-lights type list exactly as before).
    assert js.count(
        '["settlement", "house", "shop", "workshop", "kitchen"]') == 1
    # Lane A regression guards (m146/m147).
    assert js.count("onpointerdown: markTouchedAnchor") == 1
    assert js.count('classList.remove("touched")') == 1
    assert js.count('classList.add("touched")') == 1
    # House LABELS stay suppressed on the button path (m21 aria path);
    # spans render no .map-label child at all.
    assert js.count("!/^House \\d+$/.test(l.name)") == 1
    assert js.count('"aria-label": l.name') == 1


def test_lane_b_no_own_house_button():
    """Path (b): the client never learns home_location_id — no «Дом» anchor."""
    js = _read(APP_JS)
    # No backend field leaks into the client and no own-house button exists.
    assert js.count("home_location_id") == 0
    assert js.count("Дом") == 0  # no «Дом» label — path (b) fallback
    assert js.count("anchor--house") == 1  # spans only; no button variant


def test_lane_b_css_pins():
    """Houses: ambient dot styling, no hit target; Lane A targets intact."""
    css = _read(APP_CSS)
    assert css.count(".anchor--house") == 2  # base rule + ::before kill
    house_rule = css.split(".anchor--house {", 1)[1].split("}", 1)[0]
    assert "pointer-events: none" in house_rule
    assert "cursor: default" in house_rule
    before_rule = css.split(".anchor--house::before {", 1)[1].split("}", 1)[0]
    assert "content: none" in before_rule
    # Lane A 24px hit targets stay byte-pinned for POI anchors (m146).
    assert css.count(".anchor::before") == 1
    poi_before = css.split(".anchor::before {", 1)[1].split("}", 1)[0]
    assert "inset: -8px" in poi_before
    assert "position: absolute" in poi_before
    # The 8px dot visual is shared by spans and POI buttons (m21 pin
    # survives). Two occurrences total: the base rule plus the pre-existing
    # small-viewport media override (see tests/m21:185-188) — this lane
    # adds no new .anchor .dot rule.
    assert css.count(".anchor .dot {") == 2


def test_lane_b_invariants():
    """m121/m146/m147 invariant counts survive this lane untouched."""
    js = _read(APP_JS)
    assert js.count('location.hash = "#/profile";') == 3
    assert js.count("${proto}://${location.host}/ws") == 1
    assert js.count("new WebSocket") == 1
    assert js.count("scene-view") == 3
    assert js.count('document.getElementById("scene-view")') == 2
    assert js.count('"Открыть Scene"') == 1
    assert js.count("pollEventsFeed") == 2
    assert js.count("innerHTML") == 0
    assert js.count("AUTONOMOUS_HINT") == 8  # 1 decl + 7 usages (#93 scene-act)
    assert js.count("isAutonomousRefusal") == 6  # +1 (#93 scene-act catch)
    assert "MAP_LOW_MARKER_BUDGET = 120" in js
    assert js.count("markerCount = houseSpans.length + locationAnchors.length") == 1
    # el() byte-pins untouched (m22/m121/m146/m147).
    assert "for (const item of child) appendKids(node, item);" in js
    assert 'else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);' in js


def test_lane_b_syntax_gate():
    """node --check subprocess gate (m121/m146/m147 pattern)."""
    result = subprocess.run(
        ["node", "--check", "backend/app/web/app.js"],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
