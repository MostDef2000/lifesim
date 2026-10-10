"""Issue #144 — AUTONOMOUS UI gates on direct-control surfaces (UI-only fix).

Server behavior is correct per §61 (409 refusals) — this lane is frontend:
- Change 1: moveTo() gates the map-anchor MOVE post client-side. In
  AUTONOMOUS the server refuses /actions with 409 §61, so clicking an anchor
  must show the localized hint and NOT POST; the Lane A tap-label behavior
  (onpointerdown -> markTouchedAnchor) is independent and must not regress.
- Change 2: ws.onmessage re-renders the world view on a FRESH CONTROL_CHANGED
  (mode flipped from another tab/session). CRITICAL GUARD: ws.onmessage fires
  on ALL views, so the re-render only runs when location.hash === "#/world"
  (never hijack #/profile or #/chat); viewWorld() only reads state, so no
  event/POST feedback loop is possible.
- Change 3: every direct-control catch (doAction, moveTo, viewExternal
  TRAVEL_EXTERNAL) maps THAT specific §61 refusal detail ("character is
  AUTONOMOUS") to the same localized hint via the isAutonomousRefusal helper;
  all other errors stay raw. Peer-review condition: the moveTo and
  viewExternal catches were raw-detail leaks in the first implementation and
  are pinned here too.

Byte-pin style (m21/m121/m124/m128/m131/m139/m146 lineage): previously pinned
blocks stay byte-exact; this lane only appends. el() (on* -> addEventListener
wiring) stays untouched.
"""
import os
import subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")

# Single source of truth for the localized refusal message (m121 pins the
# literal; this lane reuses it via one const so all three surfaces match).
AUTONOMOUS_HINT_BLOCK = (
    "/* #144/§61: AUTONOMOUS characters refuse direct actions server-side (409),\n"
    "   so every direct-control surface says the same localized thing instead of\n"
    '   leaking the raw English server detail. */\n'
    'const AUTONOMOUS_HINT = '
    '"Персонаж действует сам — переключите режим в Профиле";'
)

MOVE_GATE_BLOCK = (
    "  // #144/§61: in AUTONOMOUS the server refuses MOVE with 409 — gate it\n"
    "  // client-side instead of posting a doomed /actions request.\n"
    '  if (S.character.control_mode === "AUTONOMOUS") {\n'
    "    toast(AUTONOMOUS_HINT);\n"
    "    return;\n"
    "  }"
)

CONTROL_CHANGED_BLOCK = (
    '          if (fresh.some((e) => e.event_type === "CONTROL_CHANGED")\n'
    '            && location.hash === "#/world") {\n'
    "            viewWorld();\n"
    "          }"
)

ACTION_CATCH_BLOCK = (
    "  } catch (e) {\n"
    "    // #144/§61: map the AUTONOMOUS refusal to the localized hint; every\n"
    "    // other server error keeps its raw message (out of scope here).\n"
    "    if (isAutonomousRefusal(e)) {\n"
    "      toast(AUTONOMOUS_HINT, true);\n"
    "    } else { toast(e.message, true); }\n"
    "  }"
)

MOVE_CATCH_BLOCK = (
    "  } catch (e) {\n"
    "    if (isAutonomousRefusal(e)) {\n"
    "      // #144/§61: stale S.character (GUIDED shown, server AUTONOMOUS) — the\n"
    "      // client gate passed but the server refused; speak the hint, not §61.\n"
    "      toast(AUTONOMOUS_HINT, true);\n"
    "    } else if (e.status === 422 && String(e.detail).includes(\"needs_move\")) {\n"
    "      toast(\"Сначала дойдите до промежуточной точки\", true);\n"
    "    } else { toast(e.message, true); }\n"
    "  }"
)

EXTERNAL_CATCH_BLOCK = (
    "            } catch (e) {\n"
    "              // #144/§61: same stale-state class as moveTo — localize.\n"
    "              err.textContent = isAutonomousRefusal(e)\n"
    "                ? AUTONOMOUS_HINT : String(e.detail);\n"
    "              if (isAutonomousRefusal(e))\n"
    "                toast(AUTONOMOUS_HINT, true);\n"
    "              if (String(e.detail).includes(\"port\"))\n"
    "                toast(\"Сначала дойдите до порта\", true);\n"
    "            }"
)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_autonomous_ui_gate_pins():
    """Issue #144: moveTo gate + CONTROL_CHANGED refresh + §61 toast mapping."""
    js = _read(APP_JS)

    # ---------- shared localized message (sub-items 1+3) ----------
    # One const definition; the raw literal survives exactly once (inside the
    # const — tests/m121:47 keeps pinning the literal itself); usages: world
    # note (m121), moveTo gate, doAction catch, moveTo catch, viewExternal ×2.
    assert js.count(AUTONOMOUS_HINT_BLOCK) == 1
    assert js.count("Персонаж действует сам — переключите режим в Профиле") == 1
    assert js.count("AUTONOMOUS_HINT") == 8  # 1 decl + 7 usages (#93 scene-act)
    assert js.count("toast(AUTONOMOUS_HINT);") == 1
    assert js.count("toast(AUTONOMOUS_HINT, true);") == 3

    # ---------- §61 refusal matcher (single source for all catches) ----------
    assert js.count('String(e.message ?? e.detail ?? "").includes('
                    '"character is AUTONOMOUS")') == 1
    assert js.count("isAutonomousRefusal") == 6  # 1 decl + 5 catch usages
    # (doAction ×1, moveTo ×1, viewExternal ×2, #93 scene-act ×1)
    # The inline check is gone (single source through the helper).
    assert 'String(e.message).includes("character is AUTONOMOUS")' not in js

    # ---------- Change 1: moveTo gate ----------
    assert js.count(MOVE_GATE_BLOCK) == 1
    assert js.count('if (S.character.control_mode === "AUTONOMOUS") {') == 1
    # The gate sits inside moveTo, BEFORE the MOVE post (no doomed request).
    move_fn = js.index("async function moveTo(loc) {")
    move_post = js.index('action_type: "MOVE"')
    gate = js.index(MOVE_GATE_BLOCK)
    assert move_fn < gate < move_post
    # Same-location early return stays first (own-anchor tap stays silent).
    assert js.index('if (loc.id === S.character.location_id) return;') < gate

    # ---------- Change 2: CONTROL_CHANGED refresh (guard lives in ws.onmessage) ----------
    assert js.count(CONTROL_CHANGED_BLOCK) == 1
    assert js.count('e.event_type === "CONTROL_CHANGED"') == 1
    assert js.count('location.hash === "#/world"') == 1
    # The refresh lives in the WS path (connectWs), gated on the world view,
    # and only fires for FRESH (deduped) events.
    connect_ws = js.index("function connectWs() {")
    fresh_dedup = js.index("const fresh = data.events.filter", connect_ws)
    refresh = js.index(CONTROL_CHANGED_BLOCK)
    assert connect_ws < fresh_dedup < refresh
    # The REST fallback path (pollEventsFeed) is untouched by this lane.
    assert js.count("async function pollEventsFeed() {") == 1

    # ---------- Change 3: §61 detail -> localized toast in ALL direct-control catches ----------
    assert js.count(ACTION_CATCH_BLOCK) == 1
    do_action = js.index("async function doAction(")
    assert do_action < js.index("if (isAutonomousRefusal(e)) {") < move_fn
    # moveTo catch: §61 mapping (peer-review condition) BEFORE the pre-existing
    # needs_move branch; raw passthrough stays for every other error.
    assert js.count(MOVE_CATCH_BLOCK) == 1
    move_catch = js.index(MOVE_CATCH_BLOCK)
    assert move_fn < move_post < move_catch
    # viewExternal TRAVEL_EXTERNAL catch: same mapping (peer-review condition).
    assert js.count(EXTERNAL_CATCH_BLOCK) == 1
    travel_post = js.index('action_type: "TRAVEL_EXTERNAL"')
    assert js.index(EXTERNAL_CATCH_BLOCK) > travel_post
    # Raw passthrough stays for every other error (moveTo's pre-existing else
    # branch + the doAction else branch).
    assert js.count("} else { toast(e.message, true); }") == 2

    # ---------- Lane A regression guards (m146) ----------
    # onpointerdown label reveal is independent of the moveTo gate.
    assert js.count("onpointerdown: markTouchedAnchor") == 1
    assert js.count('classList.remove("touched")') == 1
    assert js.count('classList.add("touched")') == 1

    # ---------- el() byte-pins untouched (m22/m121/m146) ----------
    assert "for (const item of child) appendKids(node, item);" in js
    assert 'else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);' in js

    # ---------- invariants still hold (m121/m146 counts) ----------
    assert js.count('location.hash = "#/profile";') == 3
    assert js.count("new WebSocket") == 1
    assert js.count("${proto}://${location.host}/ws") == 1
    assert js.count("scene-view") == 3
    assert js.count('document.getElementById("scene-view")') == 2
    assert js.count('"Открыть Scene"') == 1
    assert js.count("pollEventsFeed") == 2
    assert js.count("innerHTML") == 0

    # ---------- syntax gate (m121/m146 subprocess pattern) ----------
    result = subprocess.run(
        ["node", "--check", "backend/app/web/app.js"],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
