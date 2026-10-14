"""#107 — concierge cat first-visit welcome card on #/world: UI pins.

UI-only work order (no backend routes, no TestClient): a dismissible
welcome card (class ``welcome-card``) renders as the FIRST child of the
.world-shell main node when the player opens #/world for the first time.

Behavior contract implemented in app.js:
- Gate: ``window.localStorage.getItem("vl1.welcome.done") === "1"`` hides
  the card; a storage-denied/private-mode exception fails CLOSED
  (catch ⇒ seen ⇒ render nothing — the card never shows).
- Dismiss («Я освоился») persists best-effort via setItem inside try/catch
  and removes the node; a denied write still dismisses for the session.
- Content is built with the existing ``el()`` helper (XSS-safe; no
  innerHTML) and uses the two staged reference photos under
  backend/app/web/static/landing/.

Pins follow the m69 §c house style: exact literal counts over
backend/app/web/app.js / style.css read as text, plus an ordering pin for
the .welcome-card CSS block relative to the #175 connection-banner comment.

Hard-constraint guard: the work order forbids ADDING any of the
whole-file-pinned literals (m147/m153/m154/m156/m157 family) inside the
welcome block — the definition-slice test pins that none of them appear
between ``function welcomeCard()`` and ``async function viewWorld()``.
"""

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
STYLE_CSS = os.path.join(ROOT, "backend/app/web/style.css")
LANDING_DIR = os.path.join(ROOT, "backend/app/web/static/landing")

PHOTOS = ("concierge-cat-ref-face-tail.jpg", "concierge-cat-ref-loaf.jpg")

# Literals whose whole-file counts are pinned elsewhere (m147/m153/m154/
# m156/m157); the welcome block must not add a single occurrence.
FORBIDDEN_IN_WELCOME_BLOCK = (
    'location.hash = "#/world";',
    'const loading = el("div", { class: "muted" }, "Загрузка…");',
    "loadRetryNode(loading, () => view",
    "if (S.character.location_id == null)",
    "Портрет недоступен",
    "fetchPortraitBlob",
    "Number.isFinite(a)",
)

ADVICE_LINES = (
    "„Мир“ покажет, где ты, и что вокруг.",
    "„Люди“ — кто тебя знает и как к тебе относится.",
    "„Дела“ — работа и поручения: кормят и знакомят.",
)


def _js():
    with open(APP_JS, "r", encoding="utf-8") as f:
        return f.read()


def _css():
    with open(STYLE_CSS, "r", encoding="utf-8") as f:
        return f.read()


def _welcome_js_slice(js):
    """The welcomeCard() definition — everything between its header and the
    next view function (viewWorld follows it directly in source order)."""
    start = js.index("function welcomeCard()")
    return js[start:js.index("async function viewWorld()")]


def _welcome_css_section(css):
    """The .welcome-card CSS block, sliced from its first selector to the
    #175 connection-banner comment that must follow it."""
    return css[css.index(".welcome-card"):css.index("/* #175: connection banner")]


# ---------- (a) visibility gate: localStorage one-time flag ----------

def test_welcome_card_is_wired_as_first_child_of_world_shell():
    """The card must actually render: welcomeCard() called between the
    world-shell main open and the map stage (peer-review call-site pin —
    deleting the call must fail this test)."""
    js = _js()
    idx_main = js.index('el("main", { class: "world-shell" },')
    idx_map = js.index('class: "world-map-stage"', idx_main)
    between = js[idx_main:idx_map]
    assert between.count("welcomeCard(),") == 1

def test_welcome_dismiss_has_in_memory_fallback():
    """Write-denied storage must not resurrect the card on the next
    full re-render: in-memory S.welcomeDismissed guard, checked first."""
    js = _js()
    assert js.count("S.welcomeDismissed") == 2  # gate check + dismiss set
    assert js.index("if (S.welcomeDismissed) return null;") < js.index(
        'localStorage.getItem("vl1.welcome.done")')

def test_welcome_key_read_site_once():
    # Exactly one read site of the one-time flag (loose pin per the work
    # order: the raw key substring occurs twice total — one read + one write).
    assert _js().count('localStorage.getItem("vl1.welcome.done")') == 1


def test_welcome_key_write_site_once():
    js = _js()
    # Exactly one write site (the dismiss handler), and the raw key occurs
    # exactly twice overall — read + write, no third site.
    assert js.count('localStorage.setItem("vl1.welcome.done", "1")') == 1
    assert js.count("vl1.welcome.done") == 2


def test_storage_denied_fails_closed():
    # Private mode / denied storage: catch ⇒ seen ⇒ render nothing.
    # The card must NEVER show when localStorage is unavailable.
    js = _js()
    assert js.count("seen = true") == 1
    assert js.count("if (seen) return null;") == 1


# ---------- (b) content: card structure and literals ----------

def test_welcome_card_class_used_once():
    # app.js renders exactly one .welcome-card subtree; style.css styles it.
    assert _js().count('"panel welcome-card"') == 1
    assert _css().count(".welcome-card") >= 1


def test_advice_lines_once_each():
    js = _js()
    for line in ADVICE_LINES:
        assert js.count(line) == 1, line
    # three tips inside the tips container, container itself exactly once
    assert js.count('"welcome-tip"') == 3
    assert js.count('"welcome-tips"') == 1


def test_copy_heading_and_dismiss_once():
    js = _js()
    # copy paragraph (second sentence; the greeting mirrors the m9 landing pin)
    assert js.count("Остров живёт своей жизнью — а я подскажу, с чего начать.") == 1
    assert js.count("Добро пожаловать на Рейнеке. Остров живёт своей жизнью") == 1
    # heading as a standalone quoted literal (alts are longer strings)
    assert js.count('"Кошка-консьержка"') == 1
    # dismiss button
    assert js.count('"Я освоился"') == 1


# ---------- (c) reference photos: staged assets + wired refs ----------

def test_reference_photos_staged():
    for name in PHOTOS:
        path = os.path.join(LANDING_DIR, name)
        assert os.path.isfile(path), name
        size = os.path.getsize(path)
        assert 0 < size < 400_000, (name, size)


def test_photo_refs_and_alts_once():
    js = _js()
    assert js.count('"/static/static/landing/concierge-cat-ref-face-tail.jpg"') == 1
    assert js.count('"/static/static/landing/concierge-cat-ref-loaf.jpg"') == 1
    assert js.count("Кошка-консьержка — справочная фотография") == 1
    assert js.count("Кошка-консьержка — поза отдыхающей кошки") == 1


# ---------- (d) style.css: ordering + geometry + collapse ----------

def test_css_block_ordered_before_175_comment():
    # House ordering pin (indexOf comparison): the .welcome-card block is
    # inserted before the #175 connection-banner section.
    css = _css()
    assert css.index(".welcome-card") < css.index("/* #175: connection banner")


def test_css_section_geometry_pins():
    section = _welcome_css_section(_css())
    # thumbnails: portrait crop, no circle avatars, no 16/9 hero ratio
    assert "aspect-ratio: 3 / 4" in section
    assert "object-fit: cover" in section
    assert "border-radius: 50%" not in section
    assert "aspect-ratio: 16 / 9" not in section


def test_css_mobile_collapse_at_900():
    section = _welcome_css_section(_css())
    assert "@media (max-width: 900px)" in section
    assert "grid-template-columns: 1fr" in section


# ---------- (e) hard constraints: no pinned-literal drift, landing intact ----------

def test_welcome_block_adds_no_pinned_literals():
    block = _welcome_js_slice(_js())
    for lit in FORBIDDEN_IN_WELCOME_BLOCK:
        assert lit not in block, lit


def test_m9_landing_literals_untouched():
    # The m9 landing pins are substring checks (tests/m9/test_chunk_a.py:72-75,
    # 85); the welcome card adds occurrences but must not remove the originals.
    js = _js()
    assert "Остров живёт, даже когда тебя нет." in js
    assert "Начать жизнь на Рейнеке" in js
    assert "Кошка-консьержка" in js
    assert '"/static/static/landing/concierge-cat-hero.webp"' in js
