"""#260 — chat portrait guard byte-pins (m179/m258 house style).

The chat screen (#/chat, the #88 canonical NPC visual block) used to fire
the doomed GET /visual/characters//portrait (double slash — empty character
id) whenever the location has no NPCs: npcSel.value === "" still reached the
fetch. The #260 guard routes an empty selection into the EXISTING fallback
branch (the same code path as the S.visualDisabled branch) WITHOUT any
fetch — one condition, no new markup, no new VISUAL_FALLBACK use (the m63
census of 3 stays: const + catch use + else use).

The #88/#132 contract is re-asserted here: the chat block READS the
S.visualDisabled negative cache but never sets it — m132 pins the flag's
single set-site inside portraitBlock() (tests/m132 owns that census; the
key literals are duplicated below per the work order).
"""

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")

JS = ""

# The #260 guard comment + condition — byte-exact. An empty npcSel.value
# (no NPCs on the location) must never reach the portrait fetch: it falls
# into the shared fallback else-branch, the same code path the
# S.visualDisabled branch renders.
GUARD_BLOCK = (
    "  // #260: empty npcSel (no NPCs on the location) — no doomed //portrait\n"
    "  // fetch; the fallback branch below renders the shared fallback directly\n"
    "  // (same path as S.visualDisabled; the #132 flag itself stays unset).\n"
    "  if (visualOn && npcSel.value) {"
)

# Byte-exact adjacency proving the fetch opens the try INSIDE the guarded
# IIFE — an empty npcSel.value can never reach the fetch line.
GUARDED_FETCH_OPEN = (
    "  if (visualOn && npcSel.value) {\n"
    "    (async () => {\n"
    "      try {\n"
    "        const asset = await api(`/visual/characters/${npcSel.value}/portrait`);"
)

FETCH_LINE = "const asset = await api(`/visual/characters/${npcSel.value}/portrait`);"

# The #88/#132 contract comment at the head of the chat visual block:
# chat reads the flag but never writes it (m132 pins the single set-site).
CONTRACT_COMMENT = (
    "  // cache (S.visualDisabled) to skip the doomed request, but does NOT set\n"
    "  // the flag — m132 pins its single set-site inside portraitBlock(), and"
)


def setup_module(module):
    global JS
    with open(APP_JS, "r", encoding="utf-8") as f:
        JS = f.read()


# ---------- (a) the empty-selection guard exists in the chat visual block ----------

def test_empty_npc_guard_pin():
    # The guard literal exists exactly once, file-wide (the people view's
    # plain `if (visualOn) {` is a different literal and stays untouched).
    assert JS.count("if (visualOn && npcSel.value) {") == 1
    # ...with the #260 rationale comment attached, byte-exact.
    assert JS.count(GUARD_BLOCK) == 1
    # The guard sits inside the #88 chat block: after the VISUAL_FALLBACK
    # const, before the chat log construction.
    assert JS.count("const VISUAL_FALLBACK =") == 1
    assert JS.count('const log = el("div", { class: "chatlog"') == 1
    fallback_const = JS.index("const VISUAL_FALLBACK =")
    guard = JS.index(GUARD_BLOCK)
    chat_log = JS.index('const log = el("div", { class: "chatlog"')
    assert fallback_const < guard < chat_log


# ---------- (b) the fetch is only reached when the guard passes ----------

def test_fetch_inside_guarded_branch():
    # The fetch line exists exactly once (m63/m156 pin the same census).
    assert JS.count(FETCH_LINE) == 1
    # Guard -> IIFE -> try -> fetch, byte-exact: the fetch is unreachable
    # when npcSel.value is empty.
    assert JS.count(GUARDED_FETCH_OPEN) == 1
    # The fetch sits strictly BEFORE the guard's fallback else-branch.
    guard = JS.index("if (visualOn && npcSel.value) {")
    else_branch = JS.index("} else {", guard)
    assert guard < JS.index(FETCH_LINE) < else_branch


# ---------- (c) the #88/#132 contract: no flag set-site in the chat block ----------

def test_132_contract_no_set_site_in_chat_block():
    # The contract comment survives byte-exact at the block head.
    assert JS.count(CONTRACT_COMMENT) == 1
    # The chat visual block region (VISUAL_FALLBACK const .. chat log)
    # never mutates the flag — it only reads it via `const visualOn = ...`.
    region = JS[
        JS.index("const VISUAL_FALLBACK ="):
        JS.index('const log = el("div", { class: "chatlog"')
    ]
    assert "S.visualDisabled = true" not in region
    assert "S.visualDisabled = false" not in region
    # File-wide census identical to m132's pins: one set-site, one clear.
    assert JS.count("S.visualDisabled = true") == 1
    assert JS.count("S.visualDisabled = false") == 1
    # The single set-site lives inside portraitBlock() (def .. next
    # top-level function).
    pb_start = JS.index("function portraitBlock() {")
    pb_end = JS.index("\nfunction sceneDescriptorData", pb_start)
    assert pb_start < JS.index("S.visualDisabled = true") < pb_end
