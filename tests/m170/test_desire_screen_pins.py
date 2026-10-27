"""#170 — Goal / Desire screen (#/desire), UI layer of design spec #64.

The issue body is the spec of record (gh issue view 170; design of record:
gh issue view 64 §3–§8, visual language: VISUAL-BIBLE §43.8/§44/§47).

Byte-pins over backend/app/web/app.js + backend/app/web/style.css, in the
m157/m158 house style (exact literal counts — update deliberately, never
weaken):

- Route + navigation: "#/desire" registered in the router registry exactly
  once, topbar tab «Цель» exactly once, direct hash navigation works.
- No-goal state (#64 §3): the free-text TEXTAREA is the primary control
  (VISUAL-BIBLE §47), the fixed question wording from §43.8, and six
  suggestion chips that only FILL the field and never submit (§64 §3 —
  chips are examples, not limits).
- Interpretation (#64 §4-5): the deterministic catalog result is shown
  immediately («Как система поняла» — no fake spinner: interpretation is
  instant with the LLM-off catalog, desire.py §docstring), with confirm /
  edit / drop affordances; nothing mutates hidden from the player.
  Review F2: interpretation now goes through the read-only
  POST /characters/{cid}/desire/interpret preview — the desire is created
  ONLY by the confirm button (the only create site); the premature-create
  S.desireJustSet mechanism is gone (pinned == 0).
- Active goal (#64 §6-7): current desire, qualitative progress note (the
  read model has no numeric progress — never faked), opportunity cards
  with reason/source/link-out, map focus through the existing mapLink.
- Lifecycle (#64 §8): set / replace / cancel through the existing
  one-active-desire endpoints. PAUSE IS DELIBERATELY ABSENT: no backend
  support (status machine has active|replaced|abandoned only), and adding
  one would exceed the bounded frontend scope of #170 — the absence is
  pinned here so it cannot silently regress into a fake control.
- Wiring: deep link from the Дела screen focuses the free-text field
  (S.desireFocus flag consumed by renderDesire); profile block keeps
  working and links over.
- Honest gaps (also pinned): no pause control, no history read, no
  numeric progress — nothing invented client-side.
"""

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
APP_CSS = os.path.join(ROOT, "backend/app/web/style.css")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_desire_screen_route_and_tab_pins():
    js = _read(APP_JS)
    # Route + tab + view, each exactly once — same shape as the m158
    # master-journey pins; direct hash navigation must resolve #/desire.
    assert js.count('"#/desire": viewDesire') == 1
    assert js.count('["#/desire", "Цель"]') == 1
    assert js.count("async function viewDesire()") == 1
    assert js.count("function renderDesire(") == 1
    assert js.count('location.hash = "#/desire";') == 1
    # Loader + compose() share the same active-tab bar: 2 call sites
    # (compose is the single re-render factor for no-goal/preview/active/
    # edit/replace compositions).
    assert js.count('topbar("#/desire")') == 2


def test_desire_no_goal_free_text_primary_pins():
    js = _read(APP_JS)
    # Free text is THE primary control: a textarea (multi-line), not the
    # profile block's single-line input — §64 §3 + VISUAL-BIBLE §47.
    assert js.count('id: "desire-input", rows: "3"') == 1
    assert js.count('"desire-input"') == 1
    assert js.count('"desire-editor"') == 1
    assert js.count('"desire-submit"') == 1
    # The fixed §43.8 question wording, exactly once.
    assert js.count('"Чего ты хочешь для своего персонажа?"') == 1
    # Suggestion chips are module data (#64 §3 examples), each once.
    assert js.count("const DESIRE_SUGGESTIONS = [") == 1
    for chip in ("Хочу завести отношения",
                 "Хочу открыть своё дело",
                 "Хочу стать мэром",
                 "Хочу больше путешествовать",
                 "Хочу построить большой дом",
                 "Хочу познакомиться с новыми людьми"):
        assert js.count(f'"{chip}"') == 1, f"chip {chip}"
    # Chips only FILL the field and focus it (chat-sugg shortcut pattern);
    # the single handler body proves nothing is submitted on click.
    assert js.count("field.value = s; field.focus();") == 1
    assert js.count('id: "desire-chips"') == 1
    # Review F4: chips are real buttons (keyboard-operable for free), the
    # ONLY new "sugg" button site — the chat suggestions stay spans, and
    # the scoped CSS resets the button look back to the .sugg style.
    assert js.count('class: "sugg", type: "button"') == 1
    assert js.count('el("span", { class: "sugg"') == 1


def test_desire_interpretation_and_lifecycle_pins():
    js = _read(APP_JS)
    # Interpretation result, shown immediately (instant deterministic
    # catalog) — the structured "как система поняла" panel.
    assert js.count('"Как система поняла"') == 1
    assert js.count('"desire-confirm"') == 1
    assert js.count('"desire-active"') == 1
    assert js.count('"desire-opps"') == 1
    # Confirm / edit / drop affordances (#64 §5), each exactly once.
    assert js.count('"Подтвердить"') == 1
    assert js.count('"Изменить формулировку"') == 1
    assert js.count('"Сформулировать желание"') == 1
    # Review F2: confirm gates the mutation — the ONLY create site is the
    # confirm button's POST with the previewed draft; interpret is a
    # read-only call; «Отказаться» drops the DRAFT without any API call.
    assert js.count("/desire/interpret") == 1
    assert js.count('{ method: "POST", body: { text: preview.text } }') == 1
    assert js.count('"Отказаться"') == 1
    # Honesty disclosures: replacing an active desire keeps it active until
    # confirmed (editor + confirm panel), a fresh desire activates on
    # confirmation only.
    assert js.count("Текущая цель остаётся активной, "
                    "пока вы не подтвердите новую.") == 2
    assert js.count("Желание станет активным после подтверждения.") == 1
    # The premature-create mechanism is REMOVED (pin == 0 so it cannot
    # silently return): no S.desireJustSet, no «уже активно» wording.
    assert js.count("S.desireJustSet") == 0
    assert js.count('"Желание уже активно"') == 0
    # #93 wording: the interpret failure names the recoverable step.
    assert js.count('"Не удалось разобрать желание. " + RETRY_HINT') == 1
    # Guarded refresh after lifecycle actions (no yank-back navigation).
    assert js.count('if (location.hash !== "#/desire") return;') == 1
    assert js.count('location.hash === "#/desire"') == 1
    # Active-goal lifecycle (#64 §8): replace + abandon.
    assert js.count('"Заменить цель"') == 1
    assert js.count('"Отказаться от цели"') == 1
    # Honest-degradation negatives: NO pause control (no backend support —
    # gap documented, never faked) and NO fake analyzing/spinner state (the
    # interpretation is instant and rendered at once).
    assert js.count('"Приостановить"') == 0
    assert js.count('"На паузе"') == 0
    assert js.count('"Анализирую"') == 0
    assert js.count('"Желание анализируется"') == 0
    # XSS hygiene holds file-wide while the new block was added.
    assert js.count("innerHTML") == 0
    # Qualitative progress is stated as such — no invented progress bar.
    assert js.count('"Прогресс качественный') == 1


def test_desire_deep_link_and_wiring_pins():
    js = _read(APP_JS)
    # Deep link: the Дела empty state links to #/desire and flags the focus;
    # renderDesire consumes the flag exactly once and focuses the textarea.
    assert js.count('onclick: () => { S.desireFocus = true; } }') == 1
    assert js.count("const wantFocus = S.desireFocus === true;") == 1
    assert js.count("S.desireFocus = false;") == 1
    # Review F1: focusField() is invoked exactly once — inside compose(),
    # which is the single re-render factor for all screen compositions.
    assert js.count("focusField();") == 1
    # Link-over entries: Дела empty state, Дела standing link, profile hook.
    # +1 (#65): the appearance prompt zone adds a link-over entry.
    assert js.count('href: "#/desire"') == 4
    # The standing Дела link renders only when a desire exists (ternary).
    assert js.count("tasks-desire-link") == 1
    # Opportunity cards reuse the existing map focus mechanism — no new
    # "Показать на карте" literal (m157 pins it == 1 inside mapLink()).
    assert js.count("mapLink(locId)") == 2  # def + desireOppCard call


def test_desire_endpoint_use_pins():
    js = _read(APP_JS)
    # #170 is frontend-only: the screen reuses the #90 endpoints as-is.
    # Bare-desire template literal: profile POST+DELETE + #/desire
    # confirm-POST + DELETE (all four with options object).
    assert js.count("`/characters/${S.character.id}/desire`,") == 4
    # Opportunities read model: desireStateFetch (def + 2 call sites) —
    # a single authoritative read keeps the literal at 2.
    assert js.count("/desire/opportunities") == 2
    assert js.count("function desireStateFetch()") == 1
    assert js.count("desireStateFetch()") == 3
    # Guarded lifecycle refresh (def + confirm-POST + DELETE call sites).
    assert js.count("async function refreshDesire()") == 1
    assert js.count("await refreshDesire();") == 2
    # Warm shell: review F3 — the world token set is mirrored on the
    # desire grid container (class), tokens defined once in CSS.
    assert js.count('class: "grid desire-shell"') == 1


def test_desire_css_pins():
    css = _read(APP_CSS)
    # New rules are appended after the pinned media blocks (m157 pins those
    # byte-exact) — each desire rule exists exactly once.
    for rule in (".desire-question {", "#desire-input {", ".desire-chips {",
                 ".desire-actions {", ".desire-quote {", ".desire-opp-title {"):
        assert css.count(rule) == 1, rule
    # Review F3: the warm token mirror block — tokens defined once, and the
    # quote accent now reads var(--w-accent) (edited in place; a scoped
    # override would duplicate the ".desire-quote {" opener).
    assert css.count(".desire-shell {") == 1
    assert css.count(".desire-shell .panel {") == 1
    assert css.count("border-left: 3px solid var(--w-accent);") == 1
    # Review F4: the scoped chip reset turns real buttons back into the
    # .sugg look.
    assert css.count(".desire-chips .sugg { margin: 0; background: "
                     "transparent; font: inherit; }") == 1


def test_root_wline_fallback_pin():
    """#172: .task-row/.opp/.inv-item consume var(--w-line) on screens that
    render outside .world-shell (tasks/inventory/chat). Without a :root
    fallback the border shorthand is invalid at computed-value time and the
    divider silently disappears (browser-verified "0px none"). The fallback
    value must equal .world-shell's own --w-line; four definitions total
    (:root + .world-shell + .desire-shell + .shop-shell)."""
    css = _read(APP_CSS)
    # Four scopes: :root fallback (#172) + .world-shell + .desire-shell
    # (the latter deliberately self-contained — stays correct even if the
    # root fallback is ever removed) + .shop-shell (#173 — same warm values,
    # same self-contained pattern).
    assert css.count("--w-line:") == 4
    root = css.split("}", 1)[0]
    assert "--w-line: rgba(148, 178, 205, 0.16);" in root
    world = css[css.index(".world-shell {"):css.index(".world-shell {") + 400]
    assert "rgba(148, 178, 205, 0.16)" in world
