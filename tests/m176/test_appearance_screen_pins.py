"""#65 — Appearance & Outfits screen (#/appearance).

The issue body is the spec of record (gh issue view 65). Byte-pins over
backend/app/web/app.js + backend/app/web/style.css, in the
m157/m170/m173/m175 house style (exact literal counts — update
deliberately, never weaken):

- Navigation: "#/appearance" registered in the routes registry exactly once
  (that entry IS the dispatch target of route()'s (routes[hash] || viewWorld)
  call), topbar tab «Образ» exactly once as the LAST base tab, and the
  role-gated «Админ» tab still pushed AFTER it (appearance before admin).
- Screen structure: viewAppearance + renderAppearance each once;
  viewAppearance re-reads the AUTHORITATIVE character (GET
  /characters/${cid}, bare template literal) before the inventory read; the
  five zone headings («Текущий образ» / «Режим одежды» / «Сохранённые
  образы» / «Гардероб» / «Примерка») are present as text literals.
- Honest states (#65 = no fake capability): saved outfits are NOT offered —
  «Сохранённые образы пока недоступны» and NO «Сохранить образ»-like button
  anywhere in app.js; the worn list shows the honest empty «Ничего не
  надето»; the empty wardrobe links over to #/shop (nothing is invented).
- No forbidden capability: the m157 file-wide census (Съесть / Выпить /
  Использовать / Выбросить == 0) re-asserted here for locality of this block.
- Single-site contracts: POST /visual/preview stays at 2 call sites (creator
  + appearance), PATCH /outfit at 3 (creator + appearance + profile),
  POST /wear at 1 (shared wearToggle).
- Preview discipline: the appearance preview is the full_body_outfit
  variant, the caption states it is NOT saved, and APPEARANCE_DRAFT is a
  client-only module const (no localStorage/sessionStorage write in the
  appearance block), reset at every auth transition (login, logout,
  account deletion — W118-RV1 F1 precedent) right after creatorResetDraft().
- Chips: suggestion chips only FILL the textarea (draft + value + focus,
  the explicit draft write because assignment fires no input event) — the
  chip handler makes no API call, and the preview site count stays 2.
- Wear: wearToggle re-renders the ACTIVE screen (branch on
  location.hash === "#/appearance"), and the appearance wear button disables
  itself while the toggle is pending.
- CSS: .appearance-shell defined exactly once, the 900px collapse rules sit
  between the appearance block and the m175 conn-banner, and the conn-banner
  tail still ENDS the file (m175 append-only pin kept).
- Wiring: the inventory footer links over with «Образ и одежда →» → #/appearance.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "backend" / "app" / "web" / "app.js"
APP_CSS = ROOT / "backend" / "app" / "web" / "style.css"

# The #65 block is delimited by its own banner comment and the next screen's
# (#173) banner — scoped assertions read it so this suite keeps locality.
APPEARANCE_MARK = "/* ---------- #65: «Образ и одежда» (#/appearance) ---------- */"
MARKET_MARK = "/* ---------- #173"


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# Source of truth, read once (the JS/CSS globals of the m176 sibling file).
JS = _read(APP_JS)
CSS = _read(APP_CSS)


def _slice(src, start, end):
    i = src.index(start)
    return src[i:src.index(end, i)]


def _appearance_block():
    return _slice(JS, APPEARANCE_MARK, MARKET_MARK)


def _inventory_block():
    return _slice(JS, "function renderInventory(", APPEARANCE_MARK)


def _view_appearance_block():
    return _slice(JS, "async function viewAppearance()", "function renderAppearance(")


def _wear_toggle_block():
    return _slice(JS, "async function wearToggle(", "function invSellRow(")


def _chips_block():
    return _slice(JS, "const chipsRow = el(", "let previewBusy = false;")


# ---------- 1: route + tab + dispatch ----------

def test_appearance_route_and_tab_pins():
    # Route + tab + view, each exactly once — same shape as the m170/m173
    # route pins; direct hash navigation must resolve #/appearance.
    assert JS.count('"#/appearance": viewAppearance') == 1
    assert JS.count('["#/appearance", "Образ"]') == 1
    assert JS.count("async function viewAppearance()") == 1
    assert JS.count("function renderAppearance(") == 1
    assert JS.count('location.hash = "#/appearance";') == 1
    # Loader + final render share the same active-tab bar: 2 call sites.
    assert JS.count('topbar("#/appearance")') == 2
    # Dispatch: the entry sits INSIDE the routes registry object — that is
    # what route()'s (routes[hash] || viewWorld)() dispatches through.
    routes_def = JS.index("const routes = {")
    routes_end = JS.index("};", routes_def)
    assert routes_def < JS.index('"#/appearance": viewAppearance') < routes_end
    # Tab order (#65): «Образ» closes the BASE tabs array, and the
    # role-gated «Админ» tab is pushed strictly AFTER that position —
    # appearance before admin, and admin is still appended at all.
    assert JS.count('["#/appearance", "Образ"]];') == 1
    assert JS.count('tabs.push(["#/admin", "Админ"]);') == 1
    topbar_def = JS.index("function topbar(active) {")
    idx_appearance = JS.index('["#/appearance", "Образ"]')
    idx_admin = JS.index('tabs.push(["#/admin", "Админ"]);')
    assert topbar_def < idx_appearance < idx_admin


# ---------- 2: structure + zones ----------

def test_appearance_structure_and_zone_pins():
    block = _appearance_block()
    assert JS.count("async function viewAppearance()") == 1
    assert JS.count("function renderAppearance(") == 1
    # Authoritative read: viewAppearance re-reads the character (bare GET
    # /characters/${cid}) BEFORE the inventory, then merges into S — the
    # profile selector's outfit_mode and the /visual/preview sex+age
    # contract must not run on stale state.
    vblock = _view_appearance_block()
    assert vblock.count("api(`/characters/${S.character.id}`)") == 1
    assert vblock.count("api(`/characters/${S.character.id}/inventory`)") == 1
    assert vblock.index("api(`/characters/${S.character.id}`)") < vblock.index("/inventory`")
    # #93 transient failure keeps the retry affordance (m175 pattern).
    assert vblock.count("loadRetryNode(loading, () => viewAppearance());") == 1
    # Five zones as text literals, each exactly once, all inside the block.
    for zone in ('"Текущий образ"', '"Сохранённые образы"', '"Гардероб"',
                 '"Примерка"'):
        assert JS.count(zone) == 1, zone
        assert zone in block, zone
    # «Режим одежды» is pinned in its two exact forms: the panel heading and
    # the try-on select's aria-label (the preview caption is unrelated text).
    assert JS.count('el("h2", {}, "Режим одежды")') == 1
    assert JS.count('"aria-label": "Режим одежды"') == 1
    assert JS.count('el("h2", {}, "Образ и одежда")') == 1
    # Warm shell container (m170/m173 shape): one class site, one render.
    assert JS.count('class: "grid appearance-shell"') == 1


# ---------- 3: honest states ----------

def test_appearance_honest_state_pins():
    block = _appearance_block()
    # Saved outfits are a documented GAP, not a fake affordance.
    assert JS.count('"Сохранённые образы пока недоступны"') == 1
    assert '"Сохранённые образы пока недоступны"' in block
    # NO save-outfit button anywhere in app.js (Russian or EN wording).
    assert JS.count("Сохранить образ") == 0
    assert JS.count("Save Outfit") == 0
    # Honest empty worn list + honest empty wardrobe that links to the shop.
    assert JS.count('"Ничего не надето"') == 1
    assert JS.count('"Гардероб пуст — загляните на "') == 1
    assert block.count('el("a", { href: "#/shop" }') == 1
    # Exactly two #/shop link sites file-wide: the inventory footer (m173)
    # and this wardrobe empty state.
    assert JS.count('href: "#/shop"') == 2


# ---------- 4: no forbidden capability ----------

def test_no_forbidden_capability_pins():
    # The m157 file-wide census, re-asserted here so this suite alone fails
    # if the appearance block ever grows an eat/drink/use/drop control.
    for word in ("Съесть", "Выпить", "Использовать", "Выбросить"):
        assert JS.count(word) == 0, word
        assert word not in _appearance_block(), word


# ---------- 5: single-site endpoint contracts ----------

def test_appearance_single_site_endpoint_pins():
    block = _appearance_block()
    # POST /visual/preview: exactly 2 call sites (creator + appearance).
    assert JS.count('api("/visual/preview"') == 2
    assert block.count('api("/visual/preview"') == 1
    # PATCH /outfit: exactly 3 sites — creator, appearance, profile; each
    # /outfit call carries its PATCH on the same or the next line.
    assert JS.count("/outfit`") == 3
    assert JS.count('"PATCH"') == 3
    assert JS.count("/characters/${r.id}/outfit`") == 1
    assert JS.count("/characters/${S.character.id}/outfit`") == 2
    assert block.count("/outfit`") == 1
    idx, sites = 0, []
    while True:
        i = JS.find("/outfit`", idx)
        if i < 0:
            break
        sites.append(i)
        idx = i + 1
    for i in sites:
        assert 'method: "PATCH"' in JS[i:i + 120], JS[i:i + 120]
    # POST /wear: a single shared site (wearToggle), none in this block.
    assert JS.count('api("/wear"') == 1
    assert _wear_toggle_block().count('api("/wear"') == 1


# ---------- 6: preview discipline ----------

def test_appearance_preview_discipline_pins():
    block = _appearance_block()
    # The appearance preview is the full-body OUTFIT variant of the same
    # endpoint (the creator's own portrait preview keeps its mode option).
    assert JS.count('mode: "full_body_outfit"') == 1
    assert "mode: \"full_body_outfit\"" in block
    # The result is labelled UNSAVED — nothing mutates the character/garments.
    caption = '"Предпросмотр — не сохранён; персонаж и гардероб не изменены"'
    assert JS.count(caption) == 1
    assert caption in block
    assert JS.count('id: "appear-outfit-prompt"') == 1
    # APPEARANCE_DRAFT: one module-level client const, never mirrored onto S.
    assert JS.count('const APPEARANCE_DRAFT = { prompt: ""') == 1
    assert JS.count("S.APPEARANCE_DRAFT") == 0
    # ...and client-ONLY: the appearance block never touches web storage.
    assert "localStorage" not in block
    assert "sessionStorage" not in block


# ---------- 7: chips fill the field only ----------

def test_appearance_chips_fill_only_pins():
    chips = _chips_block()
    # Chip data is module data, five examples, each exactly once.
    assert JS.count("const APPEARANCE_OUTFIT_CHIPS = [") == 1
    for pair in ('["Повседневный", "повседневный стиль"]',
                 '["Спорт", "спортивный стиль"]',
                 '["Зимний", "зимний образ"]',
                 '["Элегантный", "элегантный образ"]',
                 '["Праздничный", "праздничный образ"]'):
        assert JS.count(pair) == 1, pair
    assert JS.count('class: "appear-chip",') == 1
    assert JS.count("Примеры только заполняют поле") == 1
    # The click handler assigns the DRAFT first, then the field, then
    # focuses — programmatic assignment fires no input event, so the draft
    # write must be explicit for chip text to survive a re-render; the
    # chips row (through its trailing note) still makes NO api() call.
    assert JS.count("APPEARANCE_DRAFT.prompt = text;") == 1
    assert JS.count("promptField.value = text;") == 1
    assert JS.count("promptField.focus();") == 1
    assert "api(" not in chips
    # The field stays draft-synced both ways (typed text survives re-render).
    assert JS.count('promptField.value = APPEARANCE_DRAFT.prompt || "";') == 1
    assert JS.count("APPEARANCE_DRAFT.prompt = promptField.value;") == 1
    # Preview site count is unchanged: chips never call it.
    assert JS.count('api("/visual/preview"') == 2


# ---------- 7b: draft auth isolation (validator round 2, W118-RV1 F1) ----------

def test_appearance_auth_isolation_pins():
    # APPEARANCE_DRAFT is per-session private input (W118-RV1 F1 precedent):
    # a reset helper exists and fires at ALL THREE auth transitions — fresh
    # login, logout, account deletion — always right after the existing
    # creatorResetDraft() call at that site.
    assert JS.count("function appearanceResetDraft()") == 1
    # Definition + 3 call sites (file-wide count == 4).
    assert JS.count("appearanceResetDraft();") == 3
    assert JS.count("appearanceResetDraft()") == 4
    # Every call site sits IMMEDIATELY after the creatorResetDraft() call of
    # the same transition (line-scan adjacency, not byte coincidence).
    lines = JS.splitlines()
    sites = [i for i, ln in enumerate(lines)
             if "appearanceResetDraft();" in ln]
    assert len(sites) == 3
    for i in sites:
        assert "creatorResetDraft();" in lines[i - 1], lines[i]
    # The helper resets all four draft fields.
    helper = _slice(JS, "function appearanceResetDraft() {", "}")
    for field in ('APPEARANCE_DRAFT.prompt = "";',
                  'APPEARANCE_DRAFT.hair = "";',
                  'APPEARANCE_DRAFT.hair_color = "";',
                  'APPEARANCE_DRAFT.build = "";'):
        assert helper.count(field) == 1, field


# ---------- 8: wear toggle ----------

def test_appearance_wear_toggle_pins():
    block = _appearance_block()
    wear = _wear_toggle_block()
    # wearToggle re-renders the ACTIVE screen: from #/appearance it stays
    # there; every other origin is the inventory (historical behavior).
    branch = 'if (location.hash === "#/appearance") viewAppearance();'
    assert JS.count(branch) == 1
    assert branch in wear
    assert JS.count("else viewInventory();") == 1
    assert "else viewInventory();" in wear
    assert wear.index('await api("/wear"') < wear.index(branch)
    # The appearance wear button disables itself for the pending toggle and
    # re-arms only on failure (the success path re-renders the node away).
    disable = "ev.currentTarget.disabled = true;"
    rearmed = "if (btn.isConnected) btn.disabled = false;"
    assert JS.count(disable) == 1
    assert JS.count(rearmed) == 1
    assert disable in block and rearmed in block
    wear_btn = JS.index("function wearBtn(it)")
    assert JS.index(APPEARANCE_MARK) < wear_btn < JS.index(disable) \
        < JS.index(rearmed) < JS.index("const wardrobeBox = el(")


# ---------- 9: CSS ----------

def test_appearance_css_pins():
    # ONE definition site (sub-rules like ".appearance-shell .panel {" are
    # separate selectors — the class itself appears exactly once as a rule).
    assert CSS.count(".appearance-shell {") == 1
    # Whole-file occurrence census: 1 rule opener + 4 scoped sub-selectors
    # (.panel/.muted/button.primary/button.primary:hover) + 1 mention inside
    # the #65 CSS comment block above the rule.
    assert CSS.count(".appearance-shell") == 6
    app_idx = CSS.index(".appearance-shell {")
    # The #65 rules are inserted BEFORE the m175 tail: the 900px collapse
    # sits between the appearance block and the conn-banner block.
    banner_idx = CSS.index(".conn-banner {")
    media900 = CSS.index("@media (max-width: 900px)", app_idx)
    assert app_idx < media900 < banner_idx
    assert CSS.count(".appear-hero, .appear-tryon { grid-template-columns: 1fr; }") == 1
    # The knobs collapse (640px) likewise sits inside the appearance span.
    knobs = CSS.index(".appear-knobs { grid-template-columns: 1fr; }")
    assert CSS.index("@media (max-width: 640px)", app_idx) < knobs < banner_idx
    # m175 append-only tail still ENDS the file, and the appearance block
    # never lands after it.
    assert CSS.count(".conn-banner {") == 1
    assert CSS.count(".conn-banner.hidden { display: none; }") == 1
    assert CSS.rstrip().endswith(".conn-banner.hidden { display: none; }")
    assert app_idx < banner_idx


# ---------- 10: inventory link-over ----------

def test_inventory_links_over_to_appearance_pin():
    # renderInventory's footer grows exactly one link-over entry.
    assert JS.count("function renderInventory(") == 1
    # == 2 since #66: the desire screen adds its own navigation-only
    # «Одежда и образ →» link-over (the inventory entry below is the other).
    assert JS.count('href: "#/appearance"') == 2
    assert JS.count('"Образ и одежда →"') == 1
    inv = _inventory_block()
    assert inv.count('el("a", { href: "#/appearance" }, "Образ и одежда →")') == 1
