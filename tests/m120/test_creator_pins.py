"""#118 (spec 020 P1) — character creator screen (#/create), UI layer.

The spec of record: specs/020-character-creator/{spec,plan}.md — Волна 1
item 4: UI-пины (литералы, структура формы), тег-список нейтральный
(стереотип-проверка), draft-сборка детерминирована.

Byte-pins over backend/app/web/app.js + backend/app/web/style.css, in the
m170 house style (exact literal counts — update deliberately, never
weaken). Frontend-only wave: no runner, no jsdom — the source text IS the
observable artifact.

- Route + structure: "#/create" registered, the four panels + confirm,
  the 12 structured appearance fields (CREATOR_FIELDS verbatim), the
  free-text maxlengths (looks ≤500, biography ≤2000).
- P2 persistence contract (evolved from P1's UI-only shape, plan.md:26):
  POST /characters carries name/sex/age/looks/biography + appearance/
  appearance_prompt; appearance is omitted when every field is empty.
- Outfit contract: the select mirrors the backend enum exactly
  (app.py set_outfit — cross-checked against the server source), PATCH
  /characters/{id}/outfit fires only for a non-auto choice, auto is the
  default by option ORDER (no selected attribute — pin what the code does).
- Tag neutrality (spec §P1 "без жёстких гендерных стереотипов"): every
  ingredient is a present-tense / prepositional construction — no
  feminine-marked past verb forms (-ла/-лась), no gendered person-noun
  stems from the blacklist.
- Draft determinism: creatorAssembleBio is a pure template function (no
  Math.random / Date / fetch / api), group order = CREATOR_TAGS order,
  joiners ", " / ". "; the biography textarea has exactly TWO writers
  (seed + rebuildDraft) — assembly is explicit-action only.
- Hash landmines (m128/m162 shared pins): #/profile stays 3, #/world
  stays 4, gotoWorld stays 4; the route() guard line is byte-identical;
  the creator normalizes its own hash to #/create and redirects a
  with-character visitor to #/profile.
- CSS: .creator-shell block exists, EVERY .creator-* selector is shell-
  scoped, --w-line was NOT added (count stays 4), and the m175 tail rule
  (.conn-banner.hidden last) survived the insertion.
- Hygiene: innerHTML count 0 in the creator block and file-wide.
"""

import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
APP_CSS = os.path.join(ROOT, "backend/app/web/style.css")
APP_PY = os.path.join(ROOT, "backend/app/api/app.py")

# The 12 structured appearance fields, verbatim from CREATOR_FIELDS (keys
# and Russian labels). Update only together with the source array.
EXPECTED_FIELDS = [
    ("face", "Лицо"), ("eyes", "Глаза"), ("nose", "Нос"), ("lips", "Губы"),
    ("hair", "Волосы"), ("hairColor", "Цвет волос"), ("skin", "Кожа"),
    ("height", "Рост"), ("weight", "Вес"), ("build", "Телосложение"),
    ("chest", "Грудь"), ("extra", "Дополнительные особенности"),
]

# Gendered person-noun stems (any case form matches — stems, not full
# words). The blacklist is the stereotype check of spec §P1 / plan Волна 1:
# feminine-marked profession nouns + explicitly gendered person words.
GENDERED_NOUN_STEMS = (
    "актрис", "артистк", "художниц", "писательниц", "учительниц",
    "воспитательниц", "официантк", "продавщиц", "уборщиц", "кассирш",
    "секретарш", "поварих", "портних", "прачк", "шве", "вязальщиц",
    "стюардесс", "лётчиц", "машинистк", "домработниц", "нян",
    "женщин", "мужчин", "женск", "мужск", "девочк", "мальчик",
)

# Feminine past-tense verb endings (the gendered form of a person verb).
FEMININE_PAST_SUFFIXES = ("ла", "лась")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _slice(text, start, end, what):
    assert start in text, f"{what}: start marker {start!r} missing"
    i = text.index(start)
    assert end in text[i:], f"{what}: end marker {end!r} missing"
    return text[i:text.index(end, i)]


def _body_blocks(js):
    """Every `body: { ... }` construction in app.js (brace-balanced)."""
    blocks = []
    for m in re.finditer(r"body: \{", js):
        depth = 0
        for j in range(m.end() - 1, len(js)):
            if js[j] == "{":
                depth += 1
            elif js[j] == "}":
                depth -= 1
                if depth == 0:
                    blocks.append(js[m.end() - 1:j + 1])
                    break
    return blocks


def _creator_tag_pool(js):
    block = _slice(js, "const CREATOR_TAGS = [", "];\nconst CREATOR_GROUP_NAMES",
                   "CREATOR_TAGS")
    groups = re.findall(r'\["(\w+)", \[(.*?)\]\]', block, flags=re.S)
    return {g: re.findall(r'"([^"]+)"', items) for g, items in groups}


def _creator_view(js):
    return _slice(js, "function viewCreateCharacter() {",
                  "/* ---------- portrait + scene", "viewCreateCharacter")


# ---------- 1. route + structure ----------

def test_creator_route_and_structure_pins():
    js = _read(APP_JS)
    # #/create is a registered route exactly once; the route() guard line
    # (m128 shared byte pin) survived the wave intact.
    assert js.count('"#/create": viewCreateCharacter') == 1
    assert js.count(
        'if (!S.character && hash !== "#/profile") return viewCreateCharacter();') == 1
    view = _creator_view(js)
    # One shell + the four panels + the confirm section, each exactly once.
    assert view.count('app.replaceChildren(el("main", { class: "creator-shell" },') == 1
    for heading in ("Кто вы", "Образ", "Одежда", "Биография"):
        assert view.count(f'el("h2", {{}}, "{heading}")') == 1, heading
    assert view.count('el("section", { class: "panel creator-confirm" },') == 1
    # Free-text textareas keep the m14 placeholders + maxlengths verbatim.
    assert js.count('const looks = el("textarea", { placeholder: "Внешность (необязательно)", '
                    'maxlength: "500" });') == 1
    assert js.count('const biography = el("textarea", { placeholder: "Биография (необязательно)", '
                    'maxlength: "2000" });') == 1


def test_creator_fields_pins():
    js = _read(APP_JS)
    block = _slice(js, "const CREATOR_FIELDS = [", "];", "CREATOR_FIELDS")
    pairs = re.findall(r'\["(\w+)", "([^"]+)"\]', block)
    assert pairs == EXPECTED_FIELDS  # 12 keys + labels, order verbatim
    view = _creator_view(js)
    # Every field renders as a labelled input in the appearance grid.
    assert view.count("...CREATOR_FIELDS.map(([key, lbl]) => {") == 1
    assert view.count('el("div", { class: "creator-field" }, el("label", {}, lbl), input);') == 1
    assert view.count('el("div", { class: "creator-appearance" },') == 1


# ---------- 2. P2 persistence contract ----------

def test_p1_persistence_contract_pins():
    # 020 P2: payload contract evolved per plan.md:26 (structured appearance
    # persists at confirm) — pin updated deliberately, not weakened. The
    # P1 "UI-ONLY" leak assertions are superseded: the confirm POST now
    # carries appearance + appearance_prompt; the empty case is omitted
    # client-side (absent == the pre-020 server behavior).
    js = _read(APP_JS)
    # Draft state exists (module-level; P2 wired its persistence).
    assert js.count('const CREATOR_DRAFT = { appearance: {}, appearancePrompt: "", '
                    'bio: "", tags: [] };') == 1
    # POST /characters body shape (verbatim block, 020 P2 contract).
    post = ('const r = await api("/characters", { method: "POST", body: {\n'
            '          name: name.value, sex: sex.value, age: Number(age.value),\n'
            '          looks: looks.value.trim() || undefined,\n'
            '          biography: biography.value.trim() || undefined,\n'
            '          appearance: Object.keys(appearance).length ? appearance : undefined,\n'
            '          appearance_prompt: looks.value.trim() || undefined } });')
    assert js.count(post) == 1
    assert js.count('api("/characters", { method: "POST"') == 1
    # the free text rides BOTH keys (looks unchanged + appearance_prompt).
    # W2: a third site is the creator preview POST — it rides the same
    # appearance_prompt expression; the confirm-POST block above stays
    # pinned at exactly one occurrence.
    assert js.count("looks.value.trim() || undefined") == 3
    assert js.count("biography.value.trim() || undefined") == 1
    # The POST body key set is EXACTLY name/sex/age/looks/biography/
    # appearance/appearance_prompt. NOTE: the ternary "appearance : undefined"
    # also matches the key regex — assert the SET plus first-occurrence
    # order (deduped), not the raw findall list.
    block = _body_blocks(js)
    post_block = next(b for b in block if "name: name.value" in b)
    keys = re.findall(r"(?<![\w$])(\w+)\s*:", post_block)
    assert set(keys) == {"name", "sex", "age", "looks", "biography",
                         "appearance", "appearance_prompt"}
    assert list(dict.fromkeys(keys)) == [
        "name", "sex", "age", "looks", "biography",
        "appearance", "appearance_prompt"]
    # appearance is omitted ENTIRELY when every field is empty; the payload
    # builder sends filled fields only, mapped to the canonical snake_case
    # storage keys (hairColor→hair_color, extra→features).
    assert js.count("function creatorAppearancePayload() {") == 1
    # W2: the preview panel calls the same builder for its own POST —
    # two call sites total, the confirm-path one stays unique below.
    assert js.count("const appearance = creatorAppearancePayload();") == 2
    assert js.count('const rename = { hairColor: "hair_color", extra: "features" };') == 1
    assert js.count('const v = (CREATOR_DRAFT.appearance[key] || "").trim();') == 1
    assert js.count("if (v) out[rename[key] || key] = v;") == 1


# ---------- 3. outfit contract ----------

def test_outfit_contract_pins():
    js = _read(APP_JS)
    py = _read(APP_PY)
    # Backend enum of record (#205): the server accepts exactly these four.
    assert py.count('if body.outfit_mode not in ("auto", "casual", "formal", "nude"):') == 1
    # W2: the slice ends at the preview-panel comment — the preview panel
    # (its own select with its own labels) was inserted between the outfit
    # select and the tag cloud; this pin scopes the OUTFIT select only.
    sel = _slice(js, 'const outfit = el("select", {},',
                 "/* W2: creator preview panel", "creator outfit select")
    # Option VALUES match the backend enum, auto FIRST — the browser
    # default selection is the first option, so auto is the default.
    assert re.findall(r'el\("option", \{ value: "(\w+)" \}', sel) == \
        ["auto", "casual", "formal", "nude"]
    # The labels mirror the profile selector (single wording of record).
    assert sel.count('el("option", { value: "auto" }, "auto — как сейчас"),') == 1
    assert sel.count('el("option", { value: "nude" }, "nude — без одежды (18+)"));') == 1
    # "auto is default" is implemented by ORDER only — no selected attr.
    view = _creator_view(js)
    assert view.count("selected") == 0
    # PATCH fires ONLY for an explicit non-auto choice, after the POST,
    # against the fresh character id, with the outfit_mode body key.
    assert view.count('if (outfit.value !== "auto") {') == 1
    patch = ('await api(`/characters/${r.id}/outfit`, { method: "PATCH",\n'
             '              body: { outfit_mode: outfit.value } });')
    assert view.count(patch) == 1
    assert view.index('if (outfit.value !== "auto") {') < view.index(patch)
    assert view.count("S.character.outfit_mode = outfit.value;") == 1
    # The creator PATCH site is distinct from the profile one (outfitSel).
    assert js.count("body: { outfit_mode: outfit.value }") == 1


# ---------- 4. tag pool + neutrality ----------

def test_tag_pool_structure_pins():
    js = _read(APP_JS)
    assert js.count("const CREATOR_TAGS = [") == 1
    pool = _creator_tag_pool(js)
    assert list(pool) == ["origin", "work", "nature", "dreams"]
    for group, items in pool.items():
        assert len(items) >= 5, f"group {group} too small"
    assert sum(len(v) for v in pool.values()) > 20
    # Group display names (Откуда/Занятия/Характер/Мечты), verbatim once.
    assert js.count('origin: "Откуда"') == 1
    assert js.count('work: "Занятия"') == 1
    assert js.count('nature: "Характер"') == 1
    assert js.count('dreams: "Мечты"') == 1


def test_tag_pool_gender_neutrality_pins():
    pool = _creator_tag_pool(_read(APP_JS))
    ingredients = [t for items in pool.values() for t in items]
    assert ingredients
    for text in ingredients:
        low = text.lower()
        # (a) no gendered person-noun from the blacklist (stem match —
        # catches any case form of the word);
        for stem in GENDERED_NOUN_STEMS:
            assert stem not in low, \
                f"gendered noun stem {stem!r} in ingredient {text!r}"
        # (b) no feminine-marked past-tense verb form (-ла/-лась) — the
        # pool is deliberately present-tense / prepositional.
        for word in re.findall(r"\w+", low):
            assert not word.endswith(FEMININE_PAST_SUFFIXES), \
                f"feminine past form {word!r} in ingredient {text!r}"


# ---------- 5. draft determinism ----------

def test_draft_determinism_pins():
    js = _read(APP_JS)
    asm = _slice(js, "function creatorAssembleBio(selected) {",
                 "\n\nfunction viewCreateCharacter", "creatorAssembleBio")
    # Pure template function: no randomness, no clock, no I/O of any kind.
    assert "Math.random" not in asm
    assert "Date" not in asm
    assert "fetch" not in asm
    assert "api(" not in asm
    # Group order = CREATOR_TAGS order; ingredients ", ", groups ". ".
    assert asm.count("for (const [group] of CREATOR_TAGS) {") == 1
    assert asm.count('items.join(", ")') == 1
    assert asm.count('parts.join(". ")') == 1
    # Single consumer: the definition + the one rebuildDraft call site.
    assert js.count("creatorAssembleBio") == 2
    # Explicit-assembly only: exactly TWO writers into biography.value —
    # the CREATOR_DRAFT seed and rebuildDraft. Tag click / «Собрать
    # черновик» are the only paths that reach the second one.
    assert js.count("biography.value =") == 2
    view = _creator_view(js)
    assert view.count("biography.value =") == 2
    assert view.count("rebuildDraft") == 3  # def + tag click + button
    assert view.count('onclick: rebuildDraft }, "Собрать черновик"') == 1


# ---------- 6. hash landmines ----------

def test_hash_landmine_pins():
    js = _read(APP_JS)
    # New in 020: the creator owns its route normalization and the
    # with-character redirect (the third legitimate #/profile site).
    assert js.count('location.hash = "#/create";') == 1
    assert js.count('if (S.character) { location.hash = "#/profile"; return; }') == 1
    # Shared counts (m128/m162) must stay honest after the wave:
    assert js.count('location.hash = "#/profile";') == 3
    assert js.count('location.hash = "#/world";') == 4
    assert js.count("gotoWorld();") == 4
    # The creator never assigns #/world directly and never re-enters
    # route() next to a hash assignment (#161 single-dispatch rule).
    view = _creator_view(js)
    assert view.count('location.hash = "#/world"') == 0
    assert view.count("gotoWorld();") == 1
    assert view.count("route();") == 0


# ---------- 7. CSS ----------

def test_creator_css_block_pins():
    css = _read(APP_CSS)
    # The shell block exists exactly once, warm tokens mirrored.
    assert css.count(".creator-shell {") == 1
    assert css.count(".creator-shell .panel {") == 1
    # Appearance grid 3 → 2 (≤1100px) → 1 (≤640px), profile precedent.
    assert css.count('.creator-shell .creator-appearance {\n  display: grid;\n'
                     '  grid-template-columns: repeat(3, minmax(0, 1fr));') == 1
    assert css.count(".creator-shell .creator-appearance { "
                     "grid-template-columns: repeat(2, minmax(0, 1fr)); }") == 1
    assert css.count(".creator-shell .creator-appearance { "
                     "grid-template-columns: 1fr; }") == 1
    # Selected chip state.
    assert css.count(".creator-shell .creator-tag.sel {") == 1


def test_creator_css_scope_and_tail_pins():
    css = _read(APP_CSS)
    # No unscoped .creator-* selector anywhere: strip comments, then every
    # selector segment mentioning .creator must be under .creator-shell.
    stripped = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    segments = [s for s in re.split(r"[{}]", stripped) if ".creator" in s]
    assert len(segments) >= 10  # the creator block really landed
    for sel in segments:
        assert ".creator-shell" in sel, f"unscoped creator selector: {sel.strip()!r}"
    # --w-line was NOT added (m170/m173/m175 count pin): the shell block
    # itself must not define it; the file-wide count stays 4.
    assert css.count("--w-line:") == 4
    shell = css[css.index(".creator-shell {"):]
    assert "--w-line:" not in shell[:shell.index("}")]
    # m175 tail pin: the creator block sits BEFORE the conn-banner block
    # and .conn-banner.hidden is still the LAST rule in the file.
    assert css.index(".creator-shell {") < css.index(".conn-banner {")
    assert css.rstrip().endswith(".conn-banner.hidden { display: none; }")


# ---------- 8. hygiene ----------

def test_creator_hygiene_pins():
    js = _read(APP_JS)
    block = _slice(js, "/* ---------- character creation (020",
                   "/* ---------- portrait + scene", "creator block")
    # DOM built via el() + textContent only — no HTML string injection.
    assert "innerHTML" not in block
    assert js.count("innerHTML") == 0


# ---------- 9. W118-RV1 F1: draft lifecycle ----------

def test_creator_draft_reset_pins():
    """F1: the creator draft is per-session private input — it must be reset
    on every auth transition (login, logout, account deletion) and consumed
    on successful creation, so the next browser user never inherits it."""
    js = _read(APP_JS)
    # the helper exists next to the draft singleton and clears every field
    assert js.count("function creatorResetDraft()") == 1
    helper = _slice(js, "function creatorResetDraft() {",
                    "function viewCreateCharacter", "reset helper")
    for field in ("appearance", "appearancePrompt", "bio", "tags"):
        assert f"CREATOR_DRAFT.{field} = " in helper, field
    # exactly four call sites, one per auth/creation transition
    assert js.count("creatorResetDraft();") == 4
    # each site is in the right neighborhood: login success, creator confirm,
    # logout, account deletion
    assert "fresh login must not inherit" in js
    assert "consumed draft must not leak" in js
    assert "logout clears the creator draft" in js
    assert "deletion clears the creator draft" in js
    # the logout + deletion sites sit in the profile view (after the creator
    # block), the login + confirm sites before it
    logout_i = js.index("logout clears the creator draft")
    delete_i = js.index("deletion clears the creator draft")
    login_i = js.index("fresh login must not inherit")
    confirm_i = js.index("consumed draft must not leak")
    creator_block_i = js.index("/* ---------- character creation (020")
    assert login_i < creator_block_i < confirm_i < logout_i < delete_i


# ---------- 12. W2 preview panel: regenerate availability ----------

def test_w2_preview_panel_regenerate_pins():
    """W2 (validator fix): «Обновить» must survive a FAILED retry. The
    finally block restores the regenerate button conditioned on the
    previewOk flag — set on first success, deliberately NEVER reset on
    failure (the shown image stays the last good one, so a failed
    regenerate must not strand the button disabled forever). Success path
    alone is NOT enough: the restore lives in finally, not in try."""
    js = _read(APP_JS)
    view = _creator_view(js)
    # the success flag: declared once, set ONLY on success, never cleared
    assert view.count("let previewOk = false;") == 1
    assert view.count("previewOk = true;") == 1
    ok_i = view.index("previewOk = true;")
    catch_i = view.index("} catch (e) {")
    assert ok_i < catch_i, "previewOk must be set inside try (success only)"
    assert "previewOk is deliberately NOT reset" in view
    # the restore is conditioned on the flag and lives in finally —
    # after the primary button's unconditional restore
    assert view.count("previewRegenBtn.disabled = !previewOk;") == 1
    finally_i = view.index("} finally {")
    primary_i = view.index("previewBtn.disabled = false;", finally_i)
    regen_i = view.index("previewRegenBtn.disabled = !previewOk;")
    assert primary_i < regen_i, "regen restore must follow the primary one"
    assert finally_i < regen_i
    # the double-submit guard semantics are unchanged
    assert view.count("if (previewBusy) return;") == 1
    # regen starts disabled (nothing to regenerate yet)
    assert view.count('disabled: true }, "Обновить");') == 1


# ---------- 13. W2 preview mode selector (023r3 disable) ----------

def test_w2_preview_mode_selector_pins():
    """023 round 3 (#120): base_body is server-side DISABLED (always 422)
    — the creator selector must not offer it. Exactly three options stay:
    portrait + the two full-body modes; no «Проверка фигуры» label
    anywhere in the creator view. The disable note above the selector
    documents why (a naive re-add trips this pin)."""
    js = _read(APP_JS)
    view = _creator_view(js)
    # the disabled option is gone — value AND label
    assert view.count('el("option", { value: "base_body" }') == 0
    assert "Проверка фигуры" not in view
    # exactly the three live modes remain, in order (scoped to the
    # preview selector — the view holds other selects: sex, outfit)
    sel = _slice(js, 'const previewMode = el("select", {},',
                 "// 023 round 3 (#120)", "preview mode selector")
    assert re.findall(r'el\("option", \{ value: "(\w+)" \}', sel) == \
        ["portrait", "full_body_clothed", "full_body_outfit"]
    # the server-disable note anchors the removal
    assert "server-side DISABLED (always 422)" in view
