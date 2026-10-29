"""#66 — Free Requests slice: bounded enrichment of the #/desire screen.

Approved architecture (binding): ENRICHMENT only — NO new route, NO new
topbar tab, NO new API endpoints, NO backend changes. The pre-confirmation
interpretation step is named «Свободный запрос»; the existing
create/replace/abandon lifecycle is untouched.

Byte-pins over backend/app/web/app.js + backend/app/web/style.css, in the
m157/m170/m176 house style (exact literal counts — update deliberately,
never weaken):

- Step labeling: the h3 heading «Свободный запрос» (desire-step-label) sits
  above the free-text field; the m170 literals stay intact (no new tab, no
  new route, desire-confirm/«Как система поняла» untouched).
- First-class clarification: the interpret preview's 200 + catalog_key:null
  + clarification response renders a DISTINCT .desire-clarify panel
  («Уточнение») — prominent reason text, guidance naming the four supported
  catalog domains (Отношения, Публичная роль, Дело и достаток, Спокойная
  жизнь — the authoritative CATALOG title_ru from app/desire/desire.py; the
  issue text's «Дело и доход» was superseded by the catalog name), and the
  honest «Это не результат разбора» note. The confirm button is NOT part of
  the clarify composition (clarifyPanel contains no desire-confirm id and
  no confirmBtn). desireResetDraft() resets the module-level request state
  at every auth transition (login, logout, account deletion — W118-RV1 F1
  precedent; draft leaks across accounts are validator-BLOCK material).
- Pending state: the interpret button shows a REAL pending state (disabled
  + «Разбираем запрос…») — a genuine network state, not a fake analyzer
  spinner (m170's «Анализирую»==0 negatives untouched); stale responses are
  dropped (sequence + hash + session + unedited text + connectivity).
- How-to-get-it: desireOppStep is a PURE presentation mapping of the
  planner's action descriptor (dialogue/read/WORK/MOVE/SLEEP; unknown → no
  step line); rendered only under ACTIVE-goal cards (oppsPanel(true)),
  never in the preview/edit-back compositions (oppsPanel(false)); no
  invented prerequisites, reason/source/link stay authoritative. The card
  line reads «Следующий шаг: …» (the issue's «Шаг: …» prefix is rendered as
  the muted line label).
- Appearance link: navigation-only «Одежда и образ →» → #/appearance with
  the market note (clothing arrives through the market and is worn in the
  wardrobe) — href: "#/appearance" moves file-wide 1→2 (m176 updated
  honestly with a #66 comment).
- Opportunity labeling: the panel heading «Возможности по цели» + the muted
  «не по черновику запроса» line keep opportunities tied to the ACTIVE goal
  so they never masquerade as results of an unconfirmed request.
- CSS: the six #66 rules live INSIDE the desire block — after
  .desire-shell .sugg:hover, BEFORE the #173 shop comment, and the desire
  block precedes the .appearance-shell block (m176/m175 order pins hold).
"""

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
APP_CSS = os.path.join(ROOT, "backend/app/web/style.css")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _clarify_panel_body(js):
    """The clarifyPanel function body — used to prove the confirm button is
    NOT part of the clarification composition."""
    start = js.index("function clarifyPanel(")
    end = js.index("function oppsPanel(", start)
    return js[start:end]


# ---------- a: per-session request state + auth transitions ----------

def test_free_request_request_state_reset_pins():
    js = _read(APP_JS)
    # Module-level request sequence (the ONLY new module state) + its
    # per-session reset, exactly once.
    assert js.count("let desireInterpSeq = 0;") == 1
    assert js.count("function desireResetDraft()") == 1
    # Three auth-transition call sites: login, logout, account deletion.
    assert js.count("desireResetDraft();") == 3
    # Adjacency (W118-RV1 F1 precedent): each call sits immediately after
    # the #65 appearanceResetDraft() call at that site.
    assert js.count("appearanceResetDraft();  // #65: same isolation for "
                    "the outfit prompt draft\n"
                    "      desireResetDraft();") == 1  # login
    assert js.count("appearanceResetDraft();  // #65: logout clears the "
                    "outfit prompt draft\n"
                    "          desireResetDraft();") == 1  # logout
    assert js.count("appearanceResetDraft();  // #65: deletion clears the "
                    "outfit prompt draft\n"
                    "            desireResetDraft();") == 1  # deletion


# ---------- b: first-class clarification ----------

def test_free_request_clarify_discriminator_pins():
    js = _read(APP_JS)
    # The clarify branch discriminates on the endpoint contract itself:
    # 200 + catalog_key:null + clarification (m157 API pins the backend).
    assert js.count("r.catalog_key === null") == 1
    # The DISTINCT panel: styled + id-ed, defined once, never reused.
    assert js.count('class: "panel desire-clarify"') == 1
    assert js.count('"desire-clarify"') == 1
    assert js.count("function clarifyPanel(") == 1
    assert js.count('"Уточнение"') == 1
    # The not-a-result honesty note, exactly once.
    assert js.count("Это не результат разбора") == 1


def test_free_request_clarify_is_not_a_result_pins():
    js = _read(APP_JS)
    body = _clarify_panel_body(js)
    # The clarify panel is NOT an interpreted result: no confirm id, no
    # confirm button node, no «Как система поняла» heading inside it.
    assert body.count("desire-confirm") == 0
    assert body.count("confirmBtn") == 0
    # The quoted literal (not the prose comment) is the render-site check.
    assert body.count('"Как система поняла"') == 0
    # …and the whole file keeps exactly one interpreted-result panel (m170).
    assert js.count('"Как система поняла"') == 1
    assert js.count('"desire-confirm"') == 1
    # The clarify composition renders editor + clarify panel (the draft
    # text stays in the field), never confirmPanel.
    assert js.count("compose(editorPanel(text), "
                    "clarifyPanel(r.clarification)") == 1
    # Domains guidance — the four authoritative CATALOG titles (docstring:
    # «Дело и достаток» supersedes the issue's «Дело и доход»).
    assert js.count("Свободный запрос понимает четыре темы:") == 1
    for domain in ("Отношения, Публичная роль, ", "Дело и достаток, ",
                   "Спокойная жизнь"):
        assert js.count(domain) == 1, domain


# ---------- c: real pending state ----------

def test_free_request_pending_state_pins():
    js = _read(APP_JS)
    # Real network pending state on the interpret button: disabled + label
    # swap (restored in finally) — NOT a fake analyzer spinner (the m170
    # «Анализирую»==0 / «Желание анализируется»==0 negatives stay intact).
    assert js.count('"Разбираем запрос…"') == 1
    assert js.count("const prevLabel = submitBtn.textContent;") == 1
    assert js.count("submitBtn.textContent = prevLabel;") == 1
    # The disabled+label pair is scoped to THIS handler (the auth form's
    # submit button shares the variable name — adjacency makes it unique).
    assert js.count("submitBtn.disabled = true;\n"
                    "      const prevLabel = submitBtn.textContent;") == 1
    assert js.count("submitBtn.disabled = false;\n"
                    "        submitBtn.textContent = prevLabel;") == 1
    # Stale-response guard: latest request + still on #/desire + same
    # session + unedited draft + owning composition still connected —
    # enforced on both the apply and the error path.
    assert js.count("const seq = ++desireInterpSeq;") == 1
    assert js.count("seq !== desireInterpSeq") == 2
    assert js.count('location.hash !== "#/desire"') == 3  # refresh + 2 guards
    assert js.count("field.value.trim() !== text") == 1
    assert js.count("!submitBtn.isConnected") == 2
    # No artificial delays: the pending state lasts exactly as long as the
    # fetch — no sleep-style await exists anywhere in the client.
    assert js.count("await new Promise") == 0


# ---------- d: how-to-get-it step helper (active goal cards only) ----------

def test_free_request_opportunity_step_pins():
    js = _read(APP_JS)
    # Pure presentation helper, defined once; maps the planner's action
    # descriptor only — unknown shapes yield no step line.
    assert js.count("function desireOppStep(") == 1
    assert js.count('a.mechanic === "dialogue"') == 1
    assert js.count('a.mechanic === "read"') == 1
    assert js.count('a.action_type === "WORK"') == 1
    assert js.count('a.action_type === "MOVE"') == 1
    assert js.count('a.action_type === "SLEEP"') == 1
    # The human step literals, each once (docstring: «Следующий шаг:» is the
    # muted line label — the issue's «Шаг: …» prefix rides on it).
    assert js.count("`Поговорить — ${o.title") == 1
    assert js.count("`Изучить — ${o.title") == 1
    assert js.count('return "Работать";') == 1
    assert js.count('return "Переехать";') == 1
    assert js.count('return "Отдохнуть";') == 1
    # The muted line under active-goal cards, exactly once.
    assert js.count("`Следующий шаг: ${step}`") == 1
    assert js.count('class: "muted desire-opp-step"') == 1
    # Steps ride ONLY the showSteps flag: preview and edit-back pass false.
    assert js.count("oppsPanel(false)") == 2
    assert js.count("oppsPanel(true)") == 3


# ---------- e: step + opportunity labeling ----------

def test_free_request_labeling_pins():
    js = _read(APP_JS)
    # The interpretation step is NAMED: small h3 above the free-text field.
    assert js.count('"Свободный запрос"') == 1
    assert js.count('class: "desire-step-label"') == 1
    assert js.count('el("h3", { class: "desire-step-label" }, '
                    '"Свободный запрос")') == 1
    # Opportunities are labeled as belonging to the ACTIVE goal — never as
    # results of an unconfirmed request draft.
    assert js.count('"Возможности по цели"') == 1
    assert js.count("Возможности подбираются по активной цели — "
                    "не по черновику запроса.") == 1
    # The old label is gone.
    assert js.count('"Возможности для цели"') == 0
    # The m170 primary-control pins hold file-wide.
    assert js.count('"Чего ты хочешь для своего персонажа?"') == 1


# ---------- f: appearance navigation link ----------

def test_free_request_appearance_link_pins():
    js = _read(APP_JS)
    # Navigation-only link-over from the desire screen: file-wide count is
    # 2 since #66 (inventory footer + desire editor) — m176 updated
    # honestly with the same #66 comment.
    assert js.count('href: "#/appearance"') == 2
    # The desire link's own literal, exactly once (distinct from the pinned
    # inventory footer's «Образ и одежда →»).
    assert js.count('"Одежда и образ →"') == 1
    # The note states the honest flow: clothing arrives through the market
    # and is worn in the wardrobe — no interpretation claim.
    assert js.count("Одежда появляется через рынок") == 1
    assert js.count('class: "desire-appear-link"') == 1
    assert js.count('class: "muted desire-appear-note"') == 1


# ---------- g: CSS placement + uniqueness ----------

def test_free_request_css_pins():
    css = _read(APP_CSS)
    # The six #66 rule openers, each exactly once.
    for rule in (".desire-step-label {", ".desire-clarify {",
                 ".desire-clarify-text {", ".desire-opp-step {",
                 ".desire-appear-link {", ".desire-appear-note {"):
        assert css.count(rule) == 1, rule
    # The link accent style + note sizing ride the block too.
    assert css.count(".desire-appear-link a {") == 1
    # Placement: inside the desire block — after the shell rules, BEFORE
    # the #173 shop comment, and the desire block precedes the appearance
    # block (m176 order) which precedes the conn-banner tail (m175).
    idx_label = css.index(".desire-step-label {")
    idx_clarify = css.index(".desire-clarify {")
    idx_sugg = css.index(".desire-shell .sugg:hover {")
    idx_shop = css.index("/* #173")
    idx_appear = css.index(".appearance-shell {")
    idx_banner = css.index(".conn-banner {")
    assert idx_sugg < idx_label < idx_clarify < idx_shop < idx_appear
    assert idx_appear < idx_banner
    # No new --w-line definition (m170 pins the total at 4) and the pinned
    # desire rules stay untouched.
    assert css.count("--w-line:") == 4
    assert css.count('border-left: 3px solid var(--w-accent);') == 1
