"""m313 shell a11y + mobile nav — source-contract pins: two-row pill grid,
   aria-current, safe-area insets, 44px targets, labelled chat controls,
   native suggestion buttons, focus-visible rings.

   SOURCE-STRUCTURE pins only (app.js / style.css read as text) — NOT
   geometry proof: rendered rows at 390px and real 44px hit targets are
   verified by a separate browser test. Expected RED on base 6c3aa7f;
   the nine destinations and their order, the welcome <details>, and the
   chat presence / send-busy / draft literals pass on base and must stay
   green.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = (ROOT / "backend" / "app" / "web" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "backend" / "app" / "web" / "style.css").read_text(encoding="utf-8")

# #289: the nine pill destinations — the grid re-layout must not reorder them.
PILL_ORDER = (
    ("#/world", "Мир"), ("#/chat", "Чат"), ("#/people", "Люди"),
    ("#/inventory", "Инвентарь"), ("#/shop", "Рынок"), ("#/tasks", "Дела"),
    ("#/desire", "Цель"), ("#/profile", "Профиль"), ("#/appearance", "Образ"),
)


# ---- helpers (m270 house pattern) ----
def _balanced(text, marker, open_ch, close_ch):
    i, depth, quote = text.index(open_ch, text.index(marker)), 0, None
    for j in range(i, len(text)):
        c = text[j]
        if quote:
            quote = None if c == quote else quote
        elif c in "\"'`":
            quote = c
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return text[i + 1 : j]
    raise AssertionError(f"unbalanced {open_ch!r} at {marker!r}")


def _fn_body(text, marker):
    """Brace-balanced body of the function introduced by marker."""
    i = text.index("{", text.index(marker))
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1 : j]
    raise AssertionError(f"unbalanced function body at {marker!r}")


def _media_blocks(css):
    out = []
    for m in re.finditer(r"@media\s*\(\s*max-width:[^)]*\)\s*\{", css):
        depth, i = 1, m.end()
        while i < len(css) and depth:
            depth += css[i] == "{"
            depth -= css[i] == "}"
            i += 1
        out.append(css[m.start() : i])
    return out


def _flat_rules(css):
    """(selector, body) for every brace-flat rule, media-inner rules included."""
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel = re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S).strip()
        out.append((sel, m.group(2)))
    return out


def _css_rule(marker):
    """Body of the FIRST rule whose header contains marker (base rule)."""
    return _balanced(CSS, marker, "{", "}")


def _mobile_pill_body():
    """Body of the .world-pill override inside a max-width media query."""
    for block in _media_blocks(CSS):
        if ".world-pill {" in block:
            return _balanced(block, ".world-pill {", "{", "}")
    raise AssertionError("no .world-pill override inside a max-width media query")


def _pill_tabs_body():
    return _balanced(APP_JS, "const PILL_TABS = [", "[", "]")


def _pill_map_body():
    """The single PILL_TABS.map anchor construction (the future aria-current site)."""
    return _balanced(APP_JS, "...PILL_TABS.map(", "(", ")")


def _npc_sel_body():
    return _balanced(APP_JS, 'const npcSel = el("select"', "(", ")")


def _chat_npc_label_body():
    i = APP_JS.index('"NPC на этой локации"')
    j = APP_JS.rindex('el("label"', 0, i)
    return _balanced(APP_JS[j:], 'el("label"', "(", ")")


def _chat_input_body():
    return _balanced(APP_JS, 'el("input", { id: "chat-msg-input"', "(", ")")


def _place_talk_window():
    """Props→literal window of the place «Поговорить» button (dialogue-origin
    handler). Window, not paren-balancing: the onclick comments carry
    apostrophes that defeat the quote-aware balancer."""
    anchor = APP_JS.index("chatSourceLocationId = locId;")
    start = APP_JS.rindex('{ class: "place-cta"', 0, anchor)
    end = APP_JS.index('} }, "Поговорить")', anchor)
    return APP_JS[start:end]


def _welcome_body():
    start = APP_JS.index("function welcomeCard()")
    m = re.search(r"\n(?:async )?function ", APP_JS[start + 1:])
    return APP_JS[start : start + 1 + m.start()]


# ---- preservation pins: green on base 6c3aa7f, must stay green ----
def test_pill_nine_destinations_and_order_unchanged():
    pairs = re.findall(r'\["(#[^"]+)",\s*"([^"]+)"', _pill_tabs_body())
    assert tuple(pairs) == PILL_ORDER, f"pill destinations drifted: {pairs}"


def test_pill_anchor_props_preserved():
    body = _pill_map_body()
    assert "href: hash" in body, f"pill anchor lost href: {body!r}"
    assert "title: label" in body, f"pill anchor lost title: {body!r}"
    assert '"aria-label": label' in body, f"pill anchor lost aria-label: {body!r}"


def test_welcome_details_untouched():
    assert 'el("details"' in _welcome_body(), "welcome <details> disclosure must stay"


def test_chat_presence_literals_unchanged():
    assert '{ id: "chat-presence", class: "muted" }' in APP_JS
    assert "chatPresenceCaption(rt.presence)" in APP_JS
    assert "function chatPresenceDisablesSend(" in APP_JS


def test_chat_send_busy_latch_literals_unchanged():
    assert "if (rt.sendBusy) return;" in APP_JS
    assert "rt.sendBusy = true;" in APP_JS
    assert "rt.sendBusy = false;" in APP_JS


def test_chat_draft_binding_literals_unchanged():
    assert 'input.addEventListener("input", () => { S.chatDraft = input.value; });' \
        in APP_JS
    assert 'S.chatDraft = gameUiContext.draftByNpc[npcSel.value] || "";' in APP_JS
    assert "S.chatDraft = s;" in APP_JS


# ---- m313 RED pins: must FAIL on base 6c3aa7f ----
def test_mobile_pill_is_a_grid_not_flex_wrap():
    body = _mobile_pill_body()
    assert "display: grid" in body, f"mobile .world-pill still flex-wrap: {body!r}"


def test_mobile_pill_grid_has_five_columns():
    body = _mobile_pill_body()
    assert re.search(r"grid-template-columns:\s*repeat\(5", body), \
        f"no 5-column grid in mobile .world-pill: {body!r}"


def test_mobile_pill_has_explicit_width():
    body = _mobile_pill_body()
    assert re.search(r"(?<![-a-z])width:\s*\d", body), \
        f"no explicit width in mobile .world-pill: {body!r}"


def test_aria_current_page_single_construction_site():
    n = APP_JS.count("aria-current")
    assert n == 1, f"expected exactly one aria-current site, found {n}"


def test_aria_current_page_rides_the_pill_anchor():
    body = _pill_map_body()
    assert '"aria-current"' in body, f"pill anchor lacks aria-current: {body!r}"
    assert '"page"' in body, "aria-current must mark the page"


def test_safe_area_inset_in_mobile_pill_rule():
    body = _mobile_pill_body()
    assert "env(safe-area-inset-bottom" in body, \
        f"mobile .world-pill ignores the home indicator: {body!r}"


def test_safe_area_inset_in_chat_shell_rule():
    bodies = [b for s, b in _flat_rules(CSS) if s.startswith(".chat-shell")]
    assert any("env(safe-area-inset-bottom" in b for b in bodies), \
        "no .chat-shell rule reserves env(safe-area-inset-bottom)"


def test_chat_send_button_is_44px():
    body = _css_rule(".chat-shell #chat-send {")
    assert "width: 44px" in body, f"send width still 40px: {body!r}"
    assert "height: 44px" in body, f"send height still 40px: {body!r}"
    assert "flex: 0 0 44px" in body, f"send flex-basis still 40px: {body!r}"


def test_chat_npc_select_has_id():
    body = _npc_sel_body()
    assert re.search(r"\bid\s*:", body), f"npc select has no id: {body!r}"


def test_chat_npc_label_points_at_the_select():
    body = _chat_npc_label_body()
    assert re.search(r"\bfor\s*:", body), f"NPC label lacks for=: {body!r}"


def test_chat_msg_input_has_accessible_name():
    body = _chat_input_body()
    assert "aria-label" in body, f"message input has no accessible name: {body!r}"


def test_sugg_span_construction_is_gone():
    n = APP_JS.count('el("span", { class: "sugg"')
    assert n == 0, f"{n} clickable <span> chip construction(s) remain"


def test_sugg_chips_are_native_buttons():
    body = _fn_body(APP_JS, "function renderSugg(")
    assert 'el("button"' in body, f"renderSugg builds no <button>: {body!r}"
    assert 'type: "button"' in body, "sugg chips must be type=button"
    assert 'class: "sugg"' in body, "sugg chips must keep the .sugg class"


def test_place_talk_button_labels_the_npc():
    window = _place_talk_window()
    assert "aria-label" in window, f"place «Поговорить» unnamed: {window!r}"
    assert "o.name" in window, f"aria-label must carry the NPC name: {window!r}"


def test_focus_visible_rings_for_shell_controls():
    for sel in (".world-pill a:focus-visible", "#chat-msg-input:focus-visible",
                "#chat-send:focus-visible", ".place-cta:focus-visible",
                ".place-actions button:focus-visible"):
        assert sel in CSS, f"missing :focus-visible ring for {sel}"
    rings = [s for s, _b in _flat_rules(CSS) if ":focus-visible" in s]
    assert any("select" in s for s in rings), \
        "chat NPC select has no :focus-visible ring"


def test_world_inspect_hit_target_44():
    body = _css_rule(".world-inspect {")
    assert "min-height: 44px" in body, f".world-inspect under 44px tall: {body!r}"
    assert "min-width: 44px" in body, f".world-inspect under 44px wide: {body!r}"


def test_poi_card_close_hit_target_44():
    body = _css_rule(".poi-card-close {")
    assert "min-height: 44px" in body, f".poi-card-close under 44px: {body!r}"
    assert "min-width: 44px" in body, f".poi-card-close under 44px wide: {body!r}"


def test_poi_card_actions_hit_target_44():
    body = _css_rule(".poi-card-actions a,")
    assert "min-height: 44px" in body, f"POI actions under 44px: {body!r}"
    assert "min-width: 44px" in body, f"POI actions under 44px wide: {body!r}"


def test_place_cta_hit_target_44():
    body = _css_rule(".place-cta {")
    assert "min-height: 44px" in body, f".place-cta under 44px: {body!r}"
    assert "min-width: 44px" in body, f".place-cta under 44px wide: {body!r}"


def test_place_actions_hit_target_44():
    bodies = [b for s, b in _flat_rules(CSS)
              if re.search(r"\.place-actions\s+button", s)]
    assert bodies, "no .place-actions button rule"
    joined = "\n".join(bodies)
    assert "min-height: 44px" in joined, ".place-actions buttons under 44px tall"
    assert "min-width: 44px" in joined, ".place-actions buttons under 44px wide"


def test_place_stage_button_hit_target_44():
    # «Показать сцену» — the Place scene control (122×28 before #313).
    body = _css_rule(".place-stage button {")
    assert "min-height: 44px" in body, f".place-stage button under 44px tall: {body!r}"
    assert "min-width: 44px" in body, f".place-stage button under 44px wide: {body!r}"
