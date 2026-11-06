"""m270 world-first-view — source-contract pins: native <details> welcome
   card + mobile map-first grid override.

   SOURCE-STRUCTURE pins only (app.js / style.css read as text) — NOT geometry
   proof: actual rendered sizes (e.g. mobile map >= 160px) are verified by a
   separate browser JS test (next order). Expected RED on base f58015b;
   storage/dismiss invariants pass on base and must stay green.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = (ROOT / "backend" / "app" / "web" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "backend" / "app" / "web" / "style.css").read_text(encoding="utf-8")

PHOTOS = ("concierge-cat-ref-face-tail.jpg", "concierge-cat-ref-loaf.jpg")
TIPS = ("„Мир“ покажет", "„Люди“", "„Дела“")
CLOSING = "Если потеряешься"


def _welcome_body():
    start = APP_JS.index("function welcomeCard()")
    m = re.search(r"\n(?:async )?function ", APP_JS[start + 1:])
    return APP_JS[start : start + 1 + m.start()]


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


def _details_span():
    body = _welcome_body()
    assert 'el("details"' in body, "welcome card must use a native <details> disclosure"
    return body, _balanced(body, 'el("details"', "(", ")")


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


def test_native_details_with_meaningful_russian_summary():
    body, span = _details_span()
    assert 'el("summary"' in span, "disclosure must use a native <summary>"
    label = _balanced(span, 'el("summary"', "(", ")")
    texts = re.findall(r'["\']([^"\']+)["\']', label)
    assert any(sum("\u0400" <= c <= "\u04ff" for c in s) >= 2 for s in texts), \
        "summary label must be Russian (e.g. 'Советы и фотографии', 'Фото', 'Справка')"
    attrs = _balanced(body, 'el("details"', "{", "}")
    assert not re.search(r"\bopen\s*:", attrs), \
        "welcome <details> must start closed (no open attribute)"


def test_photos_tips_closing_inside_disclosure():
    _, span = _details_span()
    for s in (*PHOTOS, *TIPS, CLOSING):
        assert s in span, f"{s!r} must be inside the <details> disclosure"


def test_heading_greeting_dismiss_stay_outside():
    body, span = _details_span()
    outside = body.replace(span, "", 1)
    assert "Кошка-консьержка" in _balanced(outside, 'el("h2"', "(", ")")
    assert "Добро пожаловать" in outside
    assert "Я освоился" in outside and 'el("button"' in outside


def test_storage_semantics_frozen():
    body = _welcome_body()
    assert body.count("localStorage.setItem") == 1  # dismiss only; toggle writes nothing
    assert body.count("localStorage.getItem") == 1
    assert re.search(r"catch\s*\([^)]*\)\s*\{\s*seen\s*=\s*true", body)  # fail-closed
    assert "S.welcomeDismissed = true" in body and "node.remove()" in body


def test_mobile_media_override_puts_map_first():
    clean = [re.sub(r"/\*.*?\*/", "", b, flags=re.S) for b in _media_blocks(CSS)]
    hits = [b for b in clean if ".world-map-stage" in b
            and re.search(r"grid-template-areas\s*:\s*[\"']map\b", b)]
    assert hits, "mobile @media: .world-map-stage + grid-template-areas first row 'map'"
