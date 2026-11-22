"""m313 composition — source-contract pins for the #313 remainder: contextual
   header (existing back affordance + shrinkable title + native «Разделы»
   disclosure), place-stage viewport min-heights, dialogue panel geometry +
   decorative scrim, composer/soft-keyboard viewport sizing, Escape ordering.

   SOURCE-STRUCTURE pins only (app.js / style.css read as text) — NOT geometry
   proof: rendered header height at 390px, stage % of innerHeight, panel width
   at 1440px, scrim alpha and 200%-font growth are verified by a separate
   browser JS test (composition.browser.js, same split as m270). Expected RED
   on base 24bfc8e: stage min-heights (A), dialogue panel/scrim CSS (B),
   ctx-header construction (C6-C8), composer viewport sizing (D10-D13) and
   the Escape disclosure-first branch (E). Preservation pins pass on base and
   must stay green: the destination registry stays built exactly once (C9)
   and the chat-shell construction, back-affordance gotoWorld, presence/draft
   literals and the CSS census (F).

   PINNED RESIZE CONTRACT (D11-D13): exactly ONE permanent window resize
   registration — `window.addEventListener("resize"` count == 1 AND
   `removeEventListener("resize"` count == 0 (the #310 Escape once-guard house
   pattern: bind once, guard inside, never teardown). NOT the teardown-owned
   add/remove alternative. The handler body must be layout-only: no
   renderChat, no api( — soft-keyboard/viewport updates resize, never
   re-render, never fetch.

   Scope guard: pins are extracted-span and specific-rule scoped (quote-aware
   balancer, m270 pattern) — never whole-file line counts — so the concurrent
   one-line app.js keydown guard PR cannot shift them.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = (ROOT / "backend" / "app" / "web" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "backend" / "app" / "web" / "style.css").read_text(encoding="utf-8")


# ---- helpers (m270 house pattern: quote-aware balanced-span extraction) ----
def _sq_string(text, i):
    """True when the ' at i opens a real single-quoted string.

    app.js comments carry possessive apostrophes (viewWorld's) that must not
    poison the balance: a single quote only opens a string when it follows an
    opener-ish char AND closes on the same line.
    """
    if i and text[i - 1] not in "(,=:[!&|?{};> \n\t":
        return False
    close = text.find("'", i + 1)
    line_end = text.find("\n", i)
    return close != -1 and (line_end == -1 or close < line_end)


def _span(text, open_idx, open_ch, close_ch):
    """Content between open_ch at open_idx and its matching close_ch."""
    depth, quote, i, n = 0, None, open_idx, len(text)
    while i < n:
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"`" or (c == "'" and _sq_string(text, i)):
            quote = c
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return text[open_idx + 1 : i], i
        i += 1
    raise AssertionError(f"unbalanced {open_ch!r} span from {open_idx}")


def _balanced(text, marker, open_ch, close_ch):
    """m270 helper: content of the first balanced open_ch..close_ch after marker."""
    i = text.index(open_ch, text.index(marker))
    return _span(text, i, open_ch, close_ch)[0]


def _fn_body(text, pos):
    """Body of the function enclosing source position pos."""
    search = pos
    while True:
        f = text.rfind("function", 0, search)
        if f < 0:
            raise AssertionError("no enclosing function found")
        before = text[f - 1] if f else " "
        after = text[f + 8 : f + 9]
        if before in " \n\t{(;}=" and after in " (":
            try:
                p = text.index("(", f)
                _span(text, p, "(", ")")  # parameter list must balance
                b = text.index("{", p)
                body, close = _span(text, b, "{", "}")
                if f < pos < close:
                    return body
            except (AssertionError, ValueError):
                pass
        search = f


def _strip_css_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _css_leaf_rules(css):
    """(selector, body, open_idx, close_idx) for every leaf CSS rule.

    Comments stripped; @media/@keyframes wrappers are recursed, not yielded
    (an @media condition rides the selector text of its FIRST inner rule).
    """
    text = _strip_css_comments(css)
    out, i = [], 0
    while True:
        b = text.find("{", i)
        if b < 0:
            break
        try:
            body, close = _span(text, b, "{", "}")
        except AssertionError:
            break
        if "{" in body:  # wrapper block — recurse into it
            i = b + 1
            continue
        prev = max(text.rfind("}", 0, b), text.rfind(";", 0, b))
        out.append((text[prev + 1 : b].strip(), body, b, close))
        i = close
    return out


def _media_blocks(css, cond_pattern):
    """Full text of every @media block whose condition matches cond_pattern."""
    text = _strip_css_comments(css)
    out = []
    for m in re.finditer(r"@media[^{]*\{", text):
        if not re.search(cond_pattern, m.group(0)):
            continue
        _, close = _span(text, m.end() - 1, "{", "}")
        out.append(text[m.start() : close + 1])
    return out


def _sel_tokens(selector):
    return [t.strip() for t in selector.split(",")]


# ---- A. stage CSS: viewport-relative min-heights (RED on base) ----
def _stage_rules():
    """Ordered leaf rules + indexes of the two .place-stage rules."""
    leaves = _css_leaf_rules(CSS)
    hits = [i for i, (sel, _, _, _) in enumerate(leaves)
            if ".place-stage" in [t.strip() for t in sel.split(",")]]
    assert len(hits) == 2, f"expected 2 .place-stage rules, found {len(hits)}"
    return leaves, hits


def _dvh_with_fallback(body, prev_body):
    dvh = re.search(r"min-height:\s*36dvh\b", body)
    assert dvh, "rule must declare min-height: 36dvh"
    assert re.search(r"min-height:\s*36vh\b", body[: dvh.start()]) or \
        re.search(r"min-height:\s*36vh\b", prev_body or ""), \
        "36vh fallback must be declared before 36dvh (same or prior rule)"


def test_stage_base_rule_viewport_min_height():
    # A1: the base .place-stage rule (~3513) sizes to the viewport with a
    # 36vh fallback; content expansion stays allowed (exception, not a cap).
    leaves, hits = _stage_rules()
    body = leaves[hits[0]][1]
    prev = leaves[hits[0] - 1][1] if hits[0] else ""
    _dvh_with_fallback(body, prev)


def test_stage_mobile_override_viewport_min_height():
    # A2: the <=720px override declares the same pair; the fixed 200px goes.
    blocks = [b for b in _media_blocks(CSS, r"max-width:\s*720px")
              if re.search(r"\.place-stage\s*\{", b)]
    assert blocks, "no <=720px media block styles .place-stage"
    body = _balanced(blocks[0], ".place-stage {", "{", "}")
    assert "200px" not in body, "mobile override must drop the fixed 200px"
    leaves, hits = _stage_rules()
    _dvh_with_fallback(body, leaves[hits[0]][1])


# ---- B. dialogue CSS: scrim + panel geometry (RED on base) ----
def test_dialogue_scrim_rule():
    # B3: decorative scrim — dim rgba paint, never interactive, no ARIA modal.
    rules = [r for r in _css_leaf_rules(CSS) if ".chat-backdrop" in r[0]]
    assert rules, ".chat-backdrop rule missing in style.css"
    body = rules[0][1]
    assert re.search(r"background\s*:[^;]*rgba\(", body), \
        "scrim must declare a dim rgba background"
    assert re.search(r"pointer-events:\s*none", body), \
        "scrim must be noninteractive (pointer-events: none)"


def _panel_rule():
    for sel, body, _, _ in _css_leaf_rules(CSS):
        if all(lit in body for lit in ("460px", "420px", "500px")):
            return sel, body
    raise AssertionError(
        "no CSS rule constrains the dialogue panel to 460px in [420px, 500px]")


def test_dialogue_panel_desktop_geometry():
    # B4: one panel rule carries the default 460px and the [420, 500] clamp.
    _panel_rule()


def test_dialogue_panel_mobile_fills_width():
    # B5: a <=720px block gives the same panel a 100%-class width.
    sel, _ = _panel_rule()
    tokens = set(re.findall(r"\.[\w-]+", sel))
    for block in _media_blocks(CSS, r"max-width:\s*720px"):
        for msel, mbody, _, _ in _css_leaf_rules(block):
            if tokens & set(re.findall(r"\.[\w-]+", msel)) and \
                    re.search(r"width:\s*100%", mbody):
                return
    raise AssertionError("panel must fill available width in a <=720px block")


# ---- C. contextual header: back + title + «Разделы» disclosure (RED) ----
def _ctx_fn_body():
    m = re.search(r'class:\s*"ctx-topbar', APP_JS)
    assert m, 'ctx-topbar construction missing in app.js (class: "ctx-topbar…")'
    return _fn_body(APP_JS, m.start())


def test_ctx_header_uses_native_disclosure():
    # C6/C7: destinations ride a native <details> whose <summary> is labelled
    # «Разделы» — no button/aria-expanded re-invention of the disclosure.
    body = _ctx_fn_body()
    details = _balanced(body, 'el("details"', "(", ")")
    assert 'el("summary"' in details, "disclosure needs a native <summary>"
    assert "Разделы" in details, "summary must be labelled «Разделы»"


def test_ctx_header_class_in_both_sources():
    # C8: one class wires the construction (app.js) and the rule (style.css).
    assert re.search(r'class:\s*"ctx-topbar', APP_JS), \
        "ctx-topbar construction missing in app.js"
    assert re.search(r"\.ctx-topbar\b", CSS), ".ctx-topbar rule missing in style.css"


def test_destination_registry_not_duplicated():
    # C9 (preservation — green on base): the helper must REUSE the topbar()
    # registry/nodes. The role-gated admin push and the destination array
    # stay built exactly once; a second registry would add copies of either.
    assert APP_JS.count('tabs.push(["#/admin", "Админ"])') == 1, \
        "destination registry must stay built exactly once (topbar)"
    assert APP_JS.count('["#/world", "Мир"]') == 1, \
        "destination array must not be duplicated for the ctx header"


# ---- D. composer / soft-keyboard viewport sizing (RED on base) ----
def _callback_body(handler):
    """Body of an inline arrow, a named function reference, or a call."""
    handler = handler.strip()
    if re.fullmatch(r"[\w$]+", handler):  # bare named handler reference
        f = APP_JS.index(f"function {handler}(")
        return _span(APP_JS, APP_JS.index("{", f), "{", "}")[0]
    if re.match(r"[\w$]+\s*\(", handler):  # factory/handler call — one hop
        name = re.match(r"[\w$]+", handler).group(0)
        f = APP_JS.index(f"function {name}(")
        return _span(APP_JS, APP_JS.index("{", f), "{", "}")[0]
    if "=>" in handler:  # inline arrow
        rest = handler[handler.index("=>") + 2 :].lstrip()
        if rest.startswith("{"):
            return _span(handler, handler.index("{"), "{", "}")[0]
        return rest
    raise AssertionError(f"unsupported handler shape: {handler[:60]!r}")


def _resize_handler_body():
    n = APP_JS.count('window.addEventListener("resize"')
    assert n == 1, f"pinned contract: exactly ONE resize registration, found {n}"
    assert APP_JS.count('removeEventListener("resize"') == 0, \
        "pinned contract: permanent guarded listener — no teardown remove"
    i = APP_JS.index('window.addEventListener("resize"')
    args = _span(APP_JS, APP_JS.index("(", i), "(", ")")[0]
    handler = args[args.index(",") + 1 :]  # first arg is the plain "resize"
    return _callback_body(handler)


def test_visual_viewport_feature_detect():
    # D10: soft-keyboard geometry comes from window.visualViewport, guarded.
    assert "visualViewport" in APP_JS, \
        "composer sizing must feature-detect window.visualViewport"


def test_resize_registration_contract():
    # D13 — pinned contract (module docstring): ONE permanent registration.
    _resize_handler_body()


def test_resize_handler_is_layout_only():
    # D11: viewport updates resize — never re-render the chat, never fetch.
    body = _resize_handler_body()
    assert "renderChat" not in body, "resize handler must not re-render"
    assert "api(" not in body, "resize handler must not fetch"


def test_chat_route_dynamic_viewport_height():
    # D12: the chat route sizes against 100dvh — CSS side must stay
    # route-local (chat/dialogue-scoped rule); an app.js site also passes.
    chat_rules = [r for r in _css_leaf_rules(CSS)
                  if "100dvh" in r[1]
                  and any(t in (r[0] + r[1]).lower() for t in ("chat", "dialogue"))]
    assert chat_rules or "100dvh" in APP_JS, \
        "100dvh must size the chat route (base has no dvh anywhere)"


# ---- E. Escape: disclosure closes before contextual back (RED on base) ----
def _escape_candidate_bodies():
    """Keydown handler bodies handling Escape + one-hop callee bodies."""
    out = []
    for m in re.finditer(r'window\.addEventListener\("keydown"', APP_JS):
        p = APP_JS.index("(", m.start())
        args = _span(APP_JS, p, "(", ")")[0]
        try:
            body = _callback_body(args[args.index(",") + 1 :])
        except (AssertionError, ValueError):
            continue
        if "Escape" not in body:
            continue
        out.append(body)
        for name in re.findall(r"\b([a-zA-Z_$][\w$]*)\s*\(", body):
            marker = f"function {name}("
            if marker in APP_JS:  # one hop: the handler's own helpers
                f = APP_JS.index(marker)
                out.append(_span(APP_JS, APP_JS.index("{", f), "{", "}")[0])
    return out


def test_escape_closes_disclosure_before_back_navigation():
    # E14: on contextual routes Escape closes an OPEN disclosure FIRST and
    # only then takes the contextual back path (source Place, else World).
    # The existing #310 POI handler has neither reference — this pins the
    # NEW branch, not the old one.
    open_state = re.compile(r"\.open\b|[\"']open[\"']")
    back_nav = re.compile(r"gotoWorld\(|location\.hash\s*=(?!=)|history\.back\(")
    for body in _escape_candidate_bodies():
        o, b = open_state.search(body), back_nav.search(body)
        if o and b and o.start() < b.start():
            return
    raise AssertionError(
        "no Escape path references the disclosure open-state before back-nav")


# ---- F. preservation (GREEN on base — guards the fix from over-reaching) ----
def test_chat_shell_construction_intact():
    # F15: m63 pin — the warm chat shell construction literal survives.
    assert APP_JS.count('class: "grid chat-shell"') == 1


def test_place_back_affordance_goto_world():
    # F16: the #92/#161 back handler — manual map focus + single-dispatch
    # gotoWorld() — survives verbatim (reused by the ctx header, never
    # replaced by a raw hash assignment or history.back).
    sites = [m.start() for m in re.finditer(r"S\.mapFocusManual = true;", APP_JS)]
    assert len(sites) == 2, "map-focus back handler sites drifted from base 2"
    assert any("gotoWorld();" in APP_JS[s : s + 450] for s in sites), \
        "back affordance must still navigate via gotoWorld()"


def test_presence_literals_intact():
    # F17: the presence machine's verdict literals — counts frozen at base.
    for word, n in (("none", 7), ("checking", 1), ("valid", 6),
                    ("departed", 5), ("unavailable", 9)):
        assert APP_JS.count(f'"{word}"') == n, f'presence literal "{word}" drifted'


def test_draft_literals_intact():
    # F18: per-NPC composer drafts — the layout wave must not touch them.
    assert APP_JS.count("S.chatDraft") == 12
    assert APP_JS.count("gameUiContext.draftByNpc") == 9


def test_css_census_unchanged():
    # F19: m238/m63 censuses — selector counts and token definitions stay.
    assert CSS.count(".place-grid {") == 2
    assert CSS.count(".place-stage {") == 2
    assert CSS.count("--w-line:") == 4
    assert CSS.count(".chat-shell {") == 1
    assert CSS.count("@media (prefers-reduced-motion: reduce)") == 3


# ---- G. 200% reflow: pill label is rem-sized (RED on the px base) ----
def test_world_pill_label_rem_sized():
    # G20: the floating nav label rides the root font size (0.625rem = 10px
    # at a 16px root, 20px at 200%) so it doubles instead of staying at an
    # absolute 10px; exactly ONE source site carries the literal.
    rules = [r for r in _css_leaf_rules(CSS) if r[0].strip() == ".world-pill a span"]
    assert len(rules) == 1, \
        f"expected one .world-pill a span rule, found {len(rules)}"
    body = rules[0][1]
    assert re.search(r"font-size:\s*0\.625rem\b", body), \
        ".world-pill a span must declare font-size: 0.625rem"
    assert re.search(r"line-height:\s*1\b", body), \
        "the 1-line pill label baseline must stay"
    assert CSS.count("font-size: 0.625rem") == 1, \
        "the 0.625rem pill label literal must have exactly one source site"
