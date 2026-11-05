"""#269 W1 — shared player shell: warm-control + nav CSS pins.

These are regression guards and computed-contrast checks over the REAL
``backend/app/web/style.css`` and ``backend/app/web/app.js``. The contrast
values are computed from the actual parsed color values (with alpha
compositing over the real cream surfaces), not hardcoded. The CSS pins are a
regression guard only — actual 390/1440 browser layout acceptance is a
separate step and is NOT asserted here.

Pins:
- ``.chat-error`` TEXT contrast >= 4.5 over the real composited cream, with
  the ``var(--warn)`` border preserved (warning semantics + m63 legacy);
- the desire free-text field is a real ``textarea`` carrying the scoped warm
  treatment, with a readable placeholder (>= 4.5) and a focus border;
- ``#chat-msg-input`` stays an ``input`` (no invented textarea);
- ``.tabs`` wraps (nav not clipped) — CSS guard only;
- ``:root`` byte-identity, ``--w-line:`` count == 4, the
  ``.conn-banner.hidden`` tail rule, and the semantic primary/danger tokens
  all survive;
- ``node --check`` on app.js.
"""
import os
import re
import subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
APP_CSS = os.path.join(ROOT, "backend/app/web/style.css")

# Exact bytes of the :root block (captured at base; the wave must not drift a
# single global token). Re-states the m63 pin.
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


# ---------- color / contrast helpers (WCAG) ----------

def parse_color(s):
    """Parse #rrggbb / #rgb / rgb() / rgba() into (r, g, b, a) floats."""
    s = s.strip()
    m = re.match(r"^rgba?\(([^)]+)\)$", s)
    if m:
        parts = [p.strip() for p in m.group(1).split(",")]
        r, g, b = float(parts[0]), float(parts[1]), float(parts[2])
        a = float(parts[3]) if len(parts) > 3 else 1.0
        return (r, g, b, a)
    m = re.match(r"^#([0-9a-fA-F]{6})$", s)
    if m:
        h = m.group(1)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0)
    m = re.match(r"^#([0-9a-fA-F]{3})$", s)
    if m:
        h = m.group(1)
        return (int(h[0] * 2, 16), int(h[1] * 2, 16), int(h[2] * 2, 16), 1.0)
    raise ValueError(f"unparseable color: {s!r}")


def composite(fg, bg):
    """Alpha-composite fg (r,g,b,a) over opaque bg (r,g,b) -> (r,g,b)."""
    r, g, b, a = fg
    br, bg_, bb = bg
    return (r * a + br * (1 - a), g * a + bg_ * (1 - a), b * a + bb * (1 - a))


def _lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rel_luminance(rgb):
    r, g, b = rgb
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast_ratio(c1, c2):
    l1, l2 = rel_luminance(c1), rel_luminance(c2)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


def _block(css, selector):
    """Return the body of the first `selector { ... }` rule.

    Brace-matched: finds the opening brace right after the selector and
    scans to the matching close, skipping /* */ comments. Works for both
    one-line rules (`.tabs { ... }`) and multi-line blocks.
    """
    i = css.index(selector + " {")
    start = i + len(selector) + 2  # just past the opening brace
    depth = 1
    j = start
    in_comment = False
    while j < len(css) and depth > 0:
        c = css[j]
        if in_comment:
            if c == "*" and j + 1 < len(css) and css[j + 1] == "/":
                in_comment = False
                j += 1
        elif c == "/" and j + 1 < len(css) and css[j + 1] == "*":
            in_comment = True
            j += 1
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        j += 1
    assert depth == 0, f"unbalanced braces in rule for {selector!r}"
    return css[start:j - 1]


def _prop_value(block, name):
    m = re.search(re.escape(name) + r"\s*:\s*([^;]+);", block)
    return m.group(1).strip() if m else None


def _custom_prop(block, name):
    v = _prop_value(block, name)
    assert v is not None, f"custom property {name} not found in block"
    return v


def resolve_var(value, css, scopes=()):
    """Resolve var(--name) references against scope blocks, then :root."""
    v = value.strip()
    for _ in range(4):
        m = re.match(r"^var\((--[\w-]+)\)$", v)
        if not m:
            return v
        name = m.group(1)
        found = None
        for scope in scopes:
            found = _prop_value(scope, name)
            if found is not None:
                break
        if found is None:
            found = _prop_value(_block(css, ":root"), name)
        assert found is not None, f"unresolvable custom property {name}"
        v = found
    return v


def color_of(value, css, scopes=()):
    """Resolve var() references, then parse into (r, g, b, a)."""
    return parse_color(resolve_var(value, css, scopes))


# ---------- 1. .chat-error: readable text, warn border preserved ----------

def test_chat_error_text_contrast_and_warn_border():
    css = _read(APP_CSS)
    err_block = _block(css, ".chat-error")
    m = re.search(r"color\s*:\s*([^;]+);", err_block)
    assert m, ".chat-error must set a text color"
    raw = m.group(1).strip()
    # The warn border must survive (warning semantics + the m63 legacy pin).
    assert re.search(r"border\s*:\s*1px solid var\(--warn\);", err_block), (
        ".chat-error must keep the 1px solid var(--warn) border"
    )
    # The actual cream surface: the chat panel (var(--w-panel)) over the chat
    # shell ground (var(--w-bg)).
    chat = _block(css, ".chat-shell")
    shell_bg = color_of(_custom_prop(chat, "--w-bg"), css, [chat])[:3]
    panel = color_of(_custom_prop(chat, "--w-panel"), css, [chat])
    surface = composite(panel, shell_bg)
    # Resolve the text ink (var() on older revisions) before parsing, so a
    # low-contrast token is measured, not a parse error.
    text = color_of(raw, css, [err_block, chat])
    ratio = contrast_ratio(text[:3], surface)
    assert ratio >= 4.5, (
        f".chat-error text {raw} over the real cream "
        f"({surface}) is {ratio:.2f}:1, below the 4.5 minimum"
    )
    # The old warn-gold text (var(--warn) = #e0b050) must NOT be the text ink
    # (it is ~1.7:1 on cream) — the border may still use it.
    assert raw != "var(--warn)", (
        ".chat-error text must not be the low-contrast warn-gold"
    )


# ---------- 2. desire field: real textarea, scoped warm, readable ----------

def test_desire_textarea_warm_and_readable():
    js = _read(APP_JS)
    css = _read(APP_CSS)
    # The free-text desire field is a real textarea (not an input).
    assert re.search(r'el\("textarea",\s*\{\s*id:\s*"desire-input"', js), (
        "#desire-input must be constructed as a textarea"
    )
    assert js.count('id: "desire-input"') == 1
    # Scoped warm treatment on the desire shell's textarea.
    desire = _block(css, ".desire-shell")
    ta = _block(css, ".desire-shell textarea")
    assert "background: var(--w-panel-deep);" in ta
    assert "color: var(--w-text);" in ta
    assert "border: 1px solid rgba(120, 96, 64, 0.35);" in ta
    # Readable placeholder + a visible focus border.
    assert re.search(
        r"\.desire-shell textarea::placeholder\s*\{\s*color:\s*var\(--w-dim\);\s*\}", css
    ), "the desire placeholder must be scoped and colored"
    assert re.search(
        r"\.desire-shell textarea:focus\s*\{\s*border-color:\s*var\(--w-accent\);\s*\}", css
    ), "the desire textarea must have a focus border"
    # Placeholder contrast over the real composited field surface.
    shell_bg = color_of(_custom_prop(desire, "--w-bg"), css, [desire])[:3]
    panel = color_of(_custom_prop(desire, "--w-panel"), css, [desire])
    panel_eff = composite(panel, shell_bg)
    field_bg = color_of(_custom_prop(desire, "--w-panel-deep"), css, [desire])
    field_eff = composite(field_bg, panel_eff)
    placeholder = color_of(_custom_prop(desire, "--w-dim"), css, [desire])[:3]
    ratio = contrast_ratio(placeholder, field_eff)
    assert ratio >= 4.5, (
        f"desire placeholder (--w-dim) over the real field surface "
        f"({field_eff}) is {ratio:.2f}:1, below the 4.5 minimum"
    )


# ---------- 3. chat input stays an input (no invented textarea) ----------

def test_chat_input_not_textarea():
    js = _read(APP_JS)
    assert re.search(r'el\("input",\s*\{\s*id:\s*"chat-msg-input"', js), (
        "#chat-msg-input must remain an input"
    )
    assert js.count('id: "chat-msg-input"') == 1
    assert not re.search(r'el\("textarea",\s*\{\s*id:\s*"chat-msg-input"', js), (
        "#chat-msg-input must not be turned into a textarea"
    )


# ---------- 4. nav: the tab row wraps (CSS guard only) ----------

def test_tabs_wrap_not_clipped():
    css = _read(APP_CSS)
    tabs = _block(css, ".tabs")
    assert "flex-wrap: wrap" in tabs, (
        ".tabs must wrap so every destination stays reachable at phone width"
    )
    # No overflow hiding on the tab row.
    assert "overflow" not in tabs, ".tabs must not clip via overflow"


# ---------- 5. root invariants (regression guards) ----------

def test_root_block_byte_untouched():
    css = _read(APP_CSS)
    root_block = css.split(":root {", 1)[1].split("\n}", 1)[0]
    assert root_block == ROOT_BLOCK_EXPECTED, (
        "the :root block must stay byte-identical (no global token drift)"
    )
    # Semantic tokens survive.
    assert "--accent: #4da3ff;" in root_block
    assert "--ok: #6fd08c;" in root_block
    assert "--warn: #e0b050;" in root_block


def test_wline_count_and_tail_rule():
    css = _read(APP_CSS)
    # The --w-line token count stays exactly 4 (:root + .world-shell +
    # .desire-shell + .shop-shell); the wave adds no fifth definition.
    assert css.count("--w-line:") == 4, (
        f"--w-line: count must stay 4, got {css.count('--w-line:')}"
    )
    # The appended-only tail convention: .conn-banner.hidden is the LAST rule.
    assert css.rstrip().endswith(".conn-banner.hidden { display: none; }")


def test_semantic_primary_danger_untouched():
    css = _read(APP_CSS)
    # The global primary + danger control rules survive byte-identical.
    assert "button.primary { background: var(--accent); color: #081018; " \
           "border: none; font-weight: 600; }" in css
    assert "button.danger { background: #b3402e; color: #fff; }" in css
    assert "button.danger:hover { background: #93301f; }" in css


# ---------- 6. hygiene ----------

def test_app_js_syntax_gate():
    result = subprocess.run(
        ["node", "--check", APP_JS],
        capture_output=True, text=True, cwd=ROOT, check=False,
    )
    assert result.returncode == 0, result.stderr
