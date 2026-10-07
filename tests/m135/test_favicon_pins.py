"""Issue #135: inline SVG favicon — browser stops requesting /favicon.ico
(last console-noise line on a clean session)."""
import re


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_favicon_pins():
    html = _read("backend/app/web/index.html")

    # ---------- favicon pins (RED on base) ----------
    # Exactly one icon link, declared as an inline SVG data URI.
    assert html.count('rel="icon"') == 1
    assert html.count("data:image/svg+xml") == 1
    # The cat-concierge emoji must be percent-encoded ASCII inside the data
    # URI (pure-ASCII guard): no literal 🐈 byte sequence may ship in the file.
    assert "%F0%9F%90%88" in html
    assert "🐈" not in html

    # ---------- regression pins (must pass on base too) ----------
    assert "ВЛ1: Рейнеке — живой остров" in html
    assert html.count("/static/app.js") == 1
    assert html.count("/static/style.css") == 1
    assert html.count("theme-color") == 1

    # ---------- positional sanity ----------
    # The icon link sits inside <head>: after the theme-color line and
    # before </head>.
    assert html.index('rel="icon"') > html.index("theme-color")
    assert html.index('rel="icon"') < html.index("</head>")
    # Line-anchored check: theme-color line, then icon line, then </head>.
    lines = html.splitlines()
    theme_line = next(i for i, ln in enumerate(lines) if "theme-color" in ln)
    icon_line = next(i for i, ln in enumerate(lines) if 'rel="icon"' in ln)
    close_head = next(i for i, ln in enumerate(lines) if re.fullmatch(r"</head>", ln.strip()))
    assert theme_line < icon_line < close_head
    # The icon line is exactly the approved one-liner (byte-pin).
    assert lines[icon_line] == (
        '  <link rel="icon" href="data:image/svg+xml,<svg xmlns=\'http://www.w3.org/2000/svg\' '
        "viewBox='0 0 16 16'><text y='14' font-size='14'>%F0%9F%90%88</text></svg>\">"
    )
