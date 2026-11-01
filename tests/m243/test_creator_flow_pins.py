"""#78 — creator guided flow: in-place redesign of viewCreateCharacter.

Approved architecture (binding), decisions recorded for the stranger
reader:

- In-place redesign of the EXISTING #/create route: NO new route, NO
  registry/topbar changes, NO backend changes, NO new APIs. The single
  page becomes a GUIDED flow: a numbered section guide in the hero, the
  sections in a deliberate order (кто вы → образ → биография → одежда →
  предпросмотр), the preview panel promoted to a narrow right column
  beside the editing sections on wide+tall screens (sticky ONLY there),
  confirm full-width below. No submission/preview/draft behavior change.
- Section guide is NAVIGATION ONLY: each entry is a button that scrolls
  its section into view and focuses it (sections carry tabindex="-1").
  Deliberate NON-features: no hash assignment (the m120/m162 hash counts
  stay put), no re-render, no validation, no draft writes, no completion
  state, no Next/Back, no percentages.
- Mockup REJECTIONS (fake capability — same discipline as #77): a
  stateful wizard with steps/progress, color swatches, separate
  Background/Personality stages, a guaranteed portrait at creation, and
  a distinct personal arrival scene are all REJECTED. The preview stays
  an explicit on-demand action that can fail; the honest NEEDS-BACKEND
  rows below say what the backend does not offer yet.
- Preview honesty: a placeholder («Предпросмотр пока не создан…») is
  visible until the FIRST successful generation hides it; a failed
  regeneration keeps the last good image and the placeholder stays
  hidden (previewOk semantics). A persistent explanation says the
  preview is not a saved portrait and the player may appear on the
  island without one. W2 holds: the preview handler never re-renders
  the form; typed values survive every outcome; no auto-preview on
  input/navigation/initial render (POST /visual/preview stays 1 site in
  the creator, 2 file-wide with the appearance screen, m176).
- CSS: the creator block is EXTENDED, never duplicated — the columns
  wrapper owns the section spacing (the shell grid gap only separates
  its DIRECT children), the sticky preview lives ONLY inside a wide+tall
  media query, and scoped [hidden] overrides make hidden actually hide
  (an author display rule outranks the UA [hidden] style — the #77
  lesson: .preview-image { display: block } would keep a hidden image
  laid out). No new color tokens (--w-line count stays 4); the m175
  banner tail stays last.

Byte-pins over backend/app/web/app.js + backend/app/web/style.css, in
the m157/m170/m176/m237/m238/m239/m240/m241/m242 house style (exact
literal counts — update deliberately, never weaken; anchors are commas
/ line-ends / indentation, NEVER bare identifier prefixes).
"""

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
APP_CSS = os.path.join(ROOT, "backend/app/web/style.css")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _creator_view(js):
    start = js.index("function viewCreateCharacter() {")
    end = js.index("/* ---------- portrait + scene", start)
    return js[start:end]


def _creator_css(css):
    start = css.index(
        "/* ---------- 020 P1 (#118): character-creator onboarding screen")
    end = css.index(".portrait-ref-controls {", start)
    return css[start:end]


# ---------- a: guided section order ----------

def test_creator_section_order_pins():
    js = _read(APP_JS)
    view = _creator_view(js)
    # The five sections are named nodes, focusable via tabindex="-1"
    # (the section guide's scroll+focus targets), declared in the guided
    # order. Full declaration lines — anchored, not bare prefixes.
    for decl in (
        'const secWho = el("section", { class: "panel", tabindex: "-1" },',
        'const secLook = el("section", { class: "panel", tabindex: "-1" },',
        'const secBio = el("section", { class: "panel", tabindex: "-1" },',
        'const secOutfit = el("section", { class: "panel", tabindex: "-1" },',
        'const secPreview = el("section", '
        '{ class: "panel creator-preview", tabindex: "-1" },',
    ):
        assert view.count(decl) == 1, decl
    assert view.index("const secWho = ") < view.index("const secLook = ")
    assert view.index("const secLook = ") < view.index("const secBio = ")
    assert view.index("const secBio = ") < view.index("const secOutfit = ")
    assert view.index("const secOutfit = ") < view.index(
        "const secPreview = ")
    # The composition: biography BEFORE outfit inside .creator-form; the
    # preview panel AFTER the form (its DOM position on narrow screens);
    # the confirm section full-width below the columns wrapper.
    columns = ('el("div", { class: "creator-columns" },\n'
               '      el("div", { class: "creator-form" },\n'
               '        secWho,\n'
               '        secLook,\n'
               '        secBio,\n'
               '        secOutfit),\n'
               '      secPreview),')
    assert view.count(columns) == 1
    assert view.index(columns) < view.index(
        'el("section", { class: "panel creator-confirm" },')
    # The headings tell the same story, each exactly once (m120 pins).
    assert view.index('el("h2", {}, "Кто вы")') < \
        view.index('el("h2", {}, "Образ")') < \
        view.index('el("h2", {}, "Биография")') < \
        view.index('el("h2", {}, "Одежда")') < \
        view.index('el("h2", {}, "Предпросмотр")')
    # Exactly five focusable sections — the guide targets, nothing else.
    assert view.count('tabindex: "-1"') == 5


# ---------- b: numbered section guide ----------

def test_creator_section_guide_pins():
    js = _read(APP_JS)
    view = _creator_view(js)
    # The guide label, exactly once, in the muted eyebrow style.
    assert view.count(
        'el("div", { class: "muted creator-eyebrow" }, '
        '"Разделы создания персонажа")') == 1
    # Five numbered entries, guided order, each exactly once.
    for entry in ('guideEntry(secWho, "1. Кто вы")',
                  'guideEntry(secLook, "2. Образ")',
                  'guideEntry(secBio, "3. Биография")',
                  'guideEntry(secOutfit, "4. Одежда")',
                  'guideEntry(secPreview, "5. Предпросмотр")'):
        assert view.count(entry) == 1, entry
    # Navigation mechanics: scroll + focus (preventScroll — the scroll
    # already positioned the section). #79 intent update: the scroll is no
    # longer unconditionally smooth — it honours the effective-motion policy
    # (OS prefers-reduced-motion OR the user's settings toggle): "auto" when
    # reduced, "smooth" otherwise.
    assert view.count(
        'section.scrollIntoView({ behavior: S.mapReducedMotion '
        '? "auto" : "smooth", block: "start" });') == 1
    assert view.count("section.focus({ preventScroll: true });") == 1
    # The guide block (guideEntry + sectionGuide) is navigation ONLY:
    # no hash assignment, no re-render, no validation, no draft writes.
    guide = view[view.index("const guideEntry = (section, label) =>"):
                 view.index("app.replaceChildren(")]
    assert "location.hash" not in guide
    assert "route(" not in guide
    assert "CREATOR_DRAFT" not in guide
    assert "rebuildDraft" not in guide
    # Shared hash landmines unchanged (m128/m162): the guide adds NO hash
    # assignment site anywhere.
    assert js.count('location.hash = "#/create";') == 1
    assert js.count('location.hash = "#/profile";') == 3
    assert js.count('location.hash = "#/world";') == 4
    assert js.count("gotoWorld();") == 5
    assert view.count("route();") == 0


# ---------- c: preview placeholder + persistent explanation ----------

def test_creator_preview_placeholder_pins():
    js = _read(APP_JS)
    view = _creator_view(js)
    # Exact copy: the honest empty state and the persistent explanation.
    assert view.count('"Предпросмотр пока не создан. Нажмите «Создать '
                      'предпросмотр», чтобы увидеть образ."') == 1
    assert view.count('"Это предпросмотр, а не сохранённый портрет. После '
                      'изменения полей создайте его заново. Можно появиться '
                      'на острове без предпросмотра."') == 1
    # The placeholder node carries the CSS hook class (the scoped
    # [hidden] override in style.css keys on it).
    assert view.count(
        'class: "muted creator-note creator-preview-empty"') == 1
    # Initial state: the placeholder is VISIBLE on first load — the
    # declaration carries NO `hidden: true` (a regression adding it would
    # blank the honest empty state on first load with every other pin
    # green). The declaration head is pinned as an exact multi-line block
    # (comma + indent anchoring); the full statement is then sliced from
    # the source for the hidden-attribute check.
    assert view.count('const previewPlaceholder = el("p",\n'
                      '    { class: "muted creator-note '
                      'creator-preview-empty" },') == 1
    decl = view[view.index('const previewPlaceholder = el("p",'):
                view.index("const previewImg")]
    assert "hidden: true" not in decl
    # Hidden-after-success mechanism: exactly ONE hide site, inside the
    # success path (after the image reveal, before the catch); the
    # placeholder is NEVER un-hidden — a failed regeneration keeps the
    # last good image and the placeholder stays hidden (previewOk).
    assert view.count("previewPlaceholder.hidden = true;") == 1
    assert view.count("previewPlaceholder.hidden = false;") == 0
    assert view.index("previewImg.hidden = false;") < view.index(
        "previewPlaceholder.hidden = true;")
    assert view.index("previewPlaceholder.hidden = true;") < view.index(
        "} catch (e) {")


# ---------- d: disclosure copy + NEEDS-BACKEND rows ----------

def test_creator_disclosure_pins():
    js = _read(APP_JS)
    view = _creator_view(js)
    # Disclosure copy, exact strings, each exactly once.
    for line in (
        '"Биография — стартовый контекст, а не жёсткая судьба. '
        'Происхождение, занятия, характер и мечты здесь описываются '
        'словами: они не выбирают игровые характеристики или профессию."',
        '"Нажатие на тег или «Собрать черновик» пересобирает текст '
        'биографии. После этого его можно отредактировать."',
        '"После создания откроется «Мир» — карта острова и первые дела. '
        'При первом посещении этого браузера кошка-консьержка может '
        'показать подсказки."',
        '"Поля внешности сохраняются при создании; редактирование этих '
        'полей после создания пока недоступно. Режим одежды можно '
        'изменить позже."',
        # the replaced «Образ» note: the full literal (the rest of the
        # note kept intact, the stale location sentence gone)
        '"Свободное описание — основное; отдельные поля подсказывают '
        'детали. Предпросмотр собирается по текущим полям формы по '
        'запросу — правки в саму форму не вносятся и не сбрасываются."',
    ):
        assert view.count(line) == 1, line
    # The stale location wording («в панели „Предпросмотр“ ниже») is gone.
    assert "Живой предпросмотр" not in view
    # NEEDS-BACKEND rows: compact, muted, one per line, exact copy.
    for row in (
        '"Структурированный выбор происхождения недоступен."',
        '"Выбор игровых черт личности недоступен."',
        '"Персональная сцена прибытия по вашей биографии недоступна."',
    ):
        assert view.count(row) == 1, row


# ---------- e: CSS layout / sticky / hidden-state / tokens / tail ----------

def test_creator_css_layout_pins():
    css = _read(APP_CSS)
    block = _creator_css(css)
    # The columns wrapper: single column by default, two columns ONLY
    # inside the wide+tall media query; the form column owns the section
    # spacing (the shell grid gap only separates its direct children).
    assert block.count(".creator-shell .creator-columns {") == 2
    assert block.count(
        ".creator-shell .creator-form { display: grid; gap: 14px; }") == 1
    media = "@media (min-width: 1100px) and (min-height: 720px) {"
    assert block.count(media) == 1
    # Sticky preview ONLY inside the media query (one site, after it).
    assert block.count("position: sticky") == 1
    assert block.index("position: sticky") > block.index(media)
    assert "position: sticky" not in block[:block.index(media)]
    # The narrow right column width is pinned.
    assert block.count(
        "grid-template-columns: minmax(0, 1fr) 280px;") == 1
    # Section guide styling exists, shell-scoped.
    assert block.count(
        ".creator-shell .creator-guide { margin-top: 12px; }") == 1
    assert block.count(".creator-shell .creator-guide-link {") == 1


def test_creator_css_hidden_state_and_tail_pins():
    css = _read(APP_CSS)
    block = _creator_css(css)
    # The #77 lesson: an author display rule outranks the UA [hidden]
    # style — scoped overrides make hidden actually hide (image +
    # placeholder).
    assert block.count(
        ".creator-shell .preview-image[hidden] { display: none; }") == 1
    assert block.count(
        ".creator-shell .creator-preview-empty[hidden] "
        "{ display: none; }") == 1
    # Token discipline: --w-line was NOT added (file-wide count stays 4).
    assert css.count("--w-line:") == 4
    # m175 tail pin survives the insertion.
    assert css.rstrip().endswith(".conn-banner.hidden { display: none; }")
    assert css.index(".creator-shell {") < css.index(".conn-banner {")
    # The pinned appearance-grid responsive rules are untouched.
    assert css.count('.creator-shell .creator-appearance {\n  display: grid;\n'
                     '  grid-template-columns: repeat(3, minmax(0, 1fr));') == 1
    assert css.count(".creator-shell .creator-appearance { "
                     "grid-template-columns: repeat(2, minmax(0, 1fr)); }") == 1
    assert css.count(".creator-shell .creator-appearance { "
                     "grid-template-columns: 1fr; }") == 1
    # Scope discipline: every occurrence of the new classes is
    # shell-scoped (no global selectors invented).
    for cls in (".creator-columns", ".creator-form", ".creator-guide",
                ".creator-preview-empty"):
        assert css.count(cls) == css.count(".creator-shell " + cls), cls


# ---------- f: on-demand preview semantics (W2) ----------

def test_creator_preview_on_demand_pins():
    js = _read(APP_JS)
    view = _creator_view(js)
    # The creator's own preview POST: exactly one site (file-wide stays 2:
    # creator + appearance screen — m176).
    assert view.count('api("/visual/preview"') == 1
    assert js.count('api("/visual/preview"') == 2
    # runPreview is reachable ONLY from the two explicit buttons
    # (definition + two onclick sites) — no auto-fire path exists.
    assert view.count("runPreview") == 3
    # Every input listener in the creator is draft capture — none calls
    # the preview (no auto-preview on typing).
    input_lines = [ln for ln in view.splitlines()
                   if 'addEventListener("input"' in ln]
    assert len(input_lines) == 3
    for ln in input_lines:
        assert "CREATOR_DRAFT" in ln, ln
        assert "runPreview" not in ln, ln


# ---------- g: no wizard state + hygiene ----------

def test_creator_no_wizard_state_pins():
    js = _read(APP_JS)
    view = _creator_view(js)
    # No storage-backed step state, no step-completion markers, no
    # Next/Back controls, no progress percentages — a stateful wizard
    # over a single page would be fake structure.
    assert "localStorage" not in view
    assert "sessionStorage" not in view
    assert "Далее" not in view
    assert "Назад" not in view
    assert "%" not in view
    assert "data-step" not in view
    # Hygiene (m120 mirror): the creator block builds DOM via el() only.
    assert "innerHTML" not in view
