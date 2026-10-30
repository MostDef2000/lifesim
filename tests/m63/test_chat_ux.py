"""Issue #63 — dialogue screen UX rework (warm family), byte-pins + behavior.

The approved visual contract (vision-worker spec, binding): warm-family
rework of the chat screen — hero band (reduction, not full-bleed), header
roles/location, 5-segment relationship scale, typing bubble in the chatlog,
inline error row, warm composer/chips. Client-only (app.js + style.css);
NO API/schema changes; :root and the cool screens stay byte-untouched.

Pin groups:
- "m63 pins" must FAIL on base (no .chat-shell / no band mapping) and pass
  after the implementation;
- "carry-forward pins" re-state the m156/m175 chat pins so this file alone
  proves the dialogue contour survived the rework;
- behavior: renderRelationshipScale maps affection boundary values to the
  correct bands (evaluated with node -e against the real app.js source);
- style.css: :root block byte-untouched (no global token drift).

Deliberate deviations (documented in style.css too):
- NO --w-line definition inside .chat-shell: m170/m173/m175 pin the
  --w-line token count at 4; the :root fallback (#172) carries the
  IDENTICAL value, so var(--w-line) resolves the same inside the shell.
- The #63 block sits BEFORE the #175 connection-banner block: m175 pins
  .conn-banner.hidden as the LAST rule of style.css (append-only tail).
"""
import os
import subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
APP_CSS = os.path.join(ROOT, "backend/app/web/style.css")

# Exact bytes of the :root block at base c355c2b (captured before the #63
# edits). The rework must not drift a single global token.
ROOT_BLOCK_EXPECTED = (
    "\n  --bg: #101418; --panel: #1a2027; --line: #2a323c;\n"
    "  --text: #d8dee6; --dim: #8a94a3; --accent: #4da3ff;"
    " --ok: #6fd08c; --warn: #e0b050;\n"
    "  /* #172: .task-row/.opp/.inv-item use var(--w-line) outside .world-shell\n"
    "     (tasks/inventory/chat) — without a root fallback the shorthand is\n"
    "     invalid at computed-value time and NO divider renders (verified in\n"
    '     browser: "0px none"). Same value as .world-shell:976. */\n'
    "  --w-line: rgba(148, 178, 205, 0.16);"
)


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------- 1. app.js byte-pins ----------

def test_chat_ux_new_pins():
    js = _read(APP_JS)
    # Shell scope rides on the grid, mirroring desire/shop shells; defined
    # exactly once as the wrapper (CSS definition is pinned separately).
    assert js.count('class: "grid chat-shell"') == 1
    # loc.name threading: viewChat passes the meeting location (== display
    # location, S.chatNpcs is filtered to characters_here); renderChat's
    # signature carries locName and the NPC-switch handler preserves it.
    assert js.count("renderChat(sess, loc.name)") == 1
    assert js.count("function renderChat(session, locName)") == 1
    assert js.count("renderChat(null, locName);") == 1
    # Subtitle «{primary role title} · {location}» — fed by the SAME single
    # roles fetch (the m156 pin api(`/characters/${npcSel.value}/roles`)
    # == 1 stays true; asserted in test_carry_forward_pins below).
    assert js.count('id: "chat-npc-sub"') == 1
    # Relationship widget: pure band mapper + DOM block with aria-label
    # (updated live via setAttribute).
    assert js.count("function renderRelationshipScale(rel)") == 1
    assert js.count('class: "rel-box", role: "img"') == 1
    assert js.count('relBox.setAttribute("aria-label", ariaText)') == 1
    assert js.count('relSegs.forEach') == 1
    # Typing bubble inside the chatlog: name + exactly three dots; the
    # assistive «Печатает…» line keeps living in #chat-status.
    assert js.count("chat-typing") == 1
    assert js.count("typing-dots") == 1
    assert js.count('el("i"), el("i"), el("i")') == 1
    # Typing bubble torn down on BOTH post-POST paths (success + error).
    assert js.count("typingBubble.remove();") == 2
    # Inline error row in the chatlog (toast + status line stay).
    assert js.count('class: "chat-error"') == 1
    # Stale error rows are cleared on a new attempt.
    assert js.count('log.querySelectorAll(".chat-error").forEach(n => n.remove())') == 1
    # Circular send button: icon glyph, accessible name, id/class preserved.
    assert js.count('id: "chat-send", class: "primary"') == 1
    assert js.count('"aria-label": "Отправить"') == 1
    # Honest fallback deduped to ONE literal (was 2 at base; the empty hero
    # state shows it, behavior unchanged on both code paths).
    assert js.count('"Визуал недоступен — диалог текстом"') == 1
    assert js.count("VISUAL_FALLBACK") == 3  # const + 2 uses
    # Hero band class (id "chat-visual" pin from m156 stays 1).
    assert js.count('class: "chat-hero", id: "chat-visual"') == 1


def test_relationship_band_mapping_table_documented():
    js = _read(APP_JS)
    # The explicit 5-band mapping table exists with ALL five Russian band
    # words (враждебные/холодные/нейтральные/тёплые/близкие) — the previous
    # #88 band words are kept, the ±20 banding is replaced by 5 bands.
    for word in ("враждебные", "холодные", "нейтральные", "тёплые", "близкие"):
        assert word in js
    # The mapping is DOCUMENTED at the function (a stranger reads it).
    assert js.count("EXPLICIT band mapping") == 1
    # Neutral fallback for missing/NaN affection — no crash, no invention.
    assert js.count("Number.isFinite(a)") == 3


def test_carry_forward_pins():
    """m156 dialogue pins + m175 chat pins re-stated: the rework must not
    disturb the pinned construction strings."""
    js = _read(APP_JS)
    # m156: screen ids / composer / status / roles / visual.
    assert js.count('el("input", { id: "chat-msg-input", placeholder: "Сообщение…" })') == 1
    assert js.count('"chat-send"') == 1
    assert js.count('"chat-view"') == 1
    assert js.count('"chat-status"') == 1
    assert js.count('"chat-roles"') == 1
    assert js.count('"chat-visual"') == 1
    assert js.count('"chat-log"') == 1
    assert js.count("Печатает…") >= 2
    assert js.count("const t0 = Date.now();") == 1
    assert js.count("Math.round((Date.now() - t0) / 1000)") == 1
    assert js.count("clearInterval(tick);") == 2
    assert js.count("input.value = draft;") == 1
    assert js.count('input.value = "";') == 2
    assert js.count('"Не удалось отправить — проверьте связь и повторите"') == 1
    assert js.count('"LLM недоступен, отвечает fallback"') == 1
    assert js.count("fetchPortraitBlob") == 6  # +1 (#65 appearance portrait)
    assert js.count("/visual/characters/${npcSel.value}/portrait") == 1
    assert js.count("api(`/characters/${npcSel.value}/roles`)") == 1
    assert js.count("/characters/${S.character.id}/relationships") == 1
    # m156: profile roles hook (NOT the chat line — different string).
    assert js.count('"Роли: " + (rolesText || "нет")') == 1
    assert js.count("profile-roles") == 1
    # #75: 1→2 — the Career screen fetches roles for itself; one request
    # PER SCREEN (the profile card still reuses its single fetch, never
    # re-fetches for its summary/chip/list).
    assert js.count('api(`/characters/${S.character.id}/roles`)') == 2
    # m156: suggested replies remain fill-never-send shortcuts.
    assert js.count("if (inp) { inp.value = s; inp.focus(); }") == 1
    assert js.count("input.value = s; send();") == 0
    assert js.count('"Подсказки заполняют поле ввода, свободный текст — основной"') == 1
    # m175: draft survival pins.
    assert js.count('chatDraft: "",\n  bootCursor: Infinity };') == 1
    assert js.count('input.value = S.chatDraft || "";') == 1
    assert js.count(
        'input.addEventListener("input", () => { S.chatDraft = input.value; });'
    ) == 1
    assert js.count('S.chatDraft = "";') == 1


# ---------- 2. behavior: band mapping evaluated against the real source ----------

def test_relationship_band_boundaries_via_node():
    """renderRelationshipScale maps affection boundary values to the correct
    bands (node -e over the actual app.js source, m156-style UI pins are
    byte-only — this is the behavior check)."""
    script = f"""
const fs = require("fs");
const src = fs.readFileSync({APP_JS!r}, "utf-8");
const m = src.match(/function renderRelationshipScale\\(rel\\) \\{{[\\s\\S]*?\\n\\}}/);
if (!m) {{ console.error("renderRelationshipScale not found"); process.exit(2); }}
eval(m[0]);
const probes = [
  [-41, "враждебные", 1, "rel-hostile"],
  [-40, "враждебные", 1, "rel-hostile"],
  [-13, "холодные", 2, "rel-cold"],
  [-12, "нейтральные", 3, "rel-neutral"],
  [12, "нейтральные", 3, "rel-neutral"],
  [13, "тёплые", 4, "rel-warm"],
  [40, "тёплые", 4, "rel-warm"],
  [41, "близкие", 5, "rel-close"],
  [-5, "нейтральные", 3, "rel-neutral"],          // negative but neutral band
  [undefined, "нейтральные", 3, "rel-neutral"],   // no relationship row
  [null, "нейтральные", 3, "rel-neutral"],
  [{{}}, "нейтральные", 3, "rel-neutral"],          // object without affection
  [NaN, "нейтральные", 3, "rel-neutral"],
  [{{affection: -50}}, "враждебные", 1, "rel-hostile"],  // rel-object form
  [{{affection: 7, trust: 4}}, "нейтральные", 3, "rel-neutral"],
];
const out = probes.map(([v, w, f, c]) => {{
  const b = renderRelationshipScale(v);
  return [b.words, b.filled, b.cls];
}});
console.log(JSON.stringify(out));
"""
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    import json
    got = json.loads(result.stdout)
    expected = [
        ["враждебные", 1, "rel-hostile"],
        ["враждебные", 1, "rel-hostile"],
        ["холодные", 2, "rel-cold"],
        ["нейтральные", 3, "rel-neutral"],
        ["нейтральные", 3, "rel-neutral"],
        ["тёплые", 4, "rel-warm"],
        ["тёплые", 4, "rel-warm"],
        ["близкие", 5, "rel-close"],
        ["нейтральные", 3, "rel-neutral"],
        ["нейтральные", 3, "rel-neutral"],
        ["нейтральные", 3, "rel-neutral"],
        ["нейтральные", 3, "rel-neutral"],
        ["нейтральные", 3, "rel-neutral"],
        ["враждебные", 1, "rel-hostile"],
        ["нейтральные", 3, "rel-neutral"],
    ]
    assert got == expected


# ---------- 3. style.css pins ----------

def _chat63_section(css):
    """The #63 chat rework section: between its header comment and the #175
    connection-banner block (which must stay the file's LAST rule)."""
    return css.split("issue #63: chat screen warm-family rework", 1)[1].split(
        "/* #175: connection banner", 1)[0]


def test_chat_shell_css_pins():
    css = _read(APP_CSS)
    # One shell definition, one section.
    assert css.count(".chat-shell {") == 1
    assert css.count("issue #63: chat screen warm-family rework") == 1
    sec = _chat63_section(css)
    # Hero band: full-width 16/9 visual — the aspect-ratio appears EXACTLY
    # ONCE inside the #63 section (other screens have their own).
    assert sec.count("aspect-ratio: 16 / 9") == 1
    assert ".chat-hero" in sec
    assert "object-fit: cover" in sec
    # Warm tokens in scope; NO --w-line definition (deliberate deviation —
    # the m170/m173/m175 pin counts exactly 4, :root fallback is identical).
    assert "--w-accent" in sec
    assert "--w-panel-deep" in sec
    assert "--w-rel-fill" in sec  # warm pink/magenta segment fill
    assert "--w-rel-hostile" in sec
    assert "--w-line:" not in sec
    assert css.count("--w-line:") == 4
    # Relationship scale widget styles.
    assert ".rel-scale" in sec
    assert ".rel-seg.on" in sec
    assert ".rel-word" in sec
    assert ".rel-trust" in sec
    # Message bubbles: translucent warm cards, sender label NOT --ok green.
    assert ".chat-shell .chatmsg .who" in sec
    assert "--w-accent" in sec
    # Typing dots: CSS animation + reduced-motion static fallback, exactly
    # one reduced-motion block for the typing dots (3 blocks file-wide).
    assert sec.count("@keyframes typing-bounce") == 1
    assert sec.count("@media (prefers-reduced-motion: reduce)") == 1
    assert css.count("@media (prefers-reduced-motion: reduce)") == 3
    prm_block = sec.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert ".typing-dots i" in prm_block
    assert "animation: none" in prm_block
    # Inline error row reuses the warm warn signal.
    assert ".chat-error" in sec and "var(--warn)" in sec
    # Composer: rounded input + circular send button; suggestion chips.
    # (The typing dots are circles too — the 50% radius is pinned inside
    # BOTH element blocks, not file- or section-wide.)
    send_rule = sec.split(".chat-shell #chat-send {", 1)[1].split("}", 1)[0]
    assert "border-radius: 50%" in send_rule
    dots_rule = sec.split(".typing-dots i {", 1)[1].split("}", 1)[0]
    assert "border-radius: 50%" in dots_rule
    assert sec.count("border-radius: 50%") == 2
    assert ".chat-shell .sugg" in sec
    # The appended-only tail convention (m175) is respected.
    assert css.rstrip().endswith(".conn-banner.hidden { display: none; }")


def test_root_block_byte_untouched():
    css = _read(APP_CSS)
    root_block = css.split(":root {", 1)[1].split("\n}", 1)[0]
    assert root_block == ROOT_BLOCK_EXPECTED
    # Diff-inspectable token values (the global cool palette must not drift).
    assert "--bg: #101418;" in root_block
    assert "--panel: #1a2027;" in root_block
    assert "--accent: #4da3ff;" in root_block


# ---------- 4. hygiene ----------

def test_app_js_syntax_gate():
    result = subprocess.run(
        ["node", "--check", "backend/app/web/app.js"],
        capture_output=True, text=True, check=False, cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
