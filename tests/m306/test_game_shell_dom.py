"""Issue #306 — game-shell DOM semantics: the helpers are EXECUTED, not grepped.

Harness: the #306 helper block + el()/appendKids are extracted verbatim from
backend/app/web/app.js, embedded in a temp JS file with a minimal DOM stub
(whose innerHTML setter throws — any write is a test failure), run under
`node` via subprocess, and the JSON description of every returned node is
asserted in Python. Follows the m121 (node via subprocess) + m270 (source
extraction) house style.

RED on base 99f38fa (no #306 block to extract); green with the
implementation in the tree.
"""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP_JS_PATH = ROOT / "backend" / "app" / "web" / "app.js"
CSS_PATH = ROOT / "backend" / "app" / "web" / "style.css"

HARNESS = """\
"use strict";
/* ---- minimal DOM stub (harness only; extracted source is verbatim) ---- */
class Node {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.nodeType = 1;
    this.childNodes = [];  // ordered, like the real DOM (text + elements interleave)
    this.attrs = {};
    this.className = "";
    this.onclick = null;
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  append(child) { this.childNodes.push(child); }
  get textContent() {
    let t = "";
    for (const c of this.childNodes) t += c.nodeType === 3 ? c.data : c.textContent;
    return t;
  }
  set innerHTML(v) { throw new Error("innerHTML write: " + v); }
}
const document = {
  createElement: (tag) => new Node(tag),
  createTextNode: (data) => ({ nodeType: 3, data: String(data) }),
};

/* ---- extracted verbatim from backend/app/web/app.js ---- */
__APPEND_KIDS__

__EL__

__GAME_SHELL_BLOCK__

/* ---- exercise every helper with sample args ---- */
function describe(node) {
  if (node === null || node === undefined) return null;
  return {
    tag: node.tagName,
    class: node.className,
    attrs: node.attrs,
    text: node.textContent,
    hasOnclick: typeof node.onclick === "function",
    children: node.childNodes.filter((c) => c.nodeType === 1).map(describe),
  };
}
const out = {
  header: describe(gameHeader("Тест", "День 1, 08:00")),
  headerNoMeta: describe(gameHeader("Тест")),
  panel: describe(contextPanel("Панель", "тело", gameEmpty("пусто"))),
  panelNoTitle: describe(contextPanel(null, "только тело")),
  button: describe(actionButton("Пойти", () => {}, { kind: "primary", title: "Подсказка" })),
  buttonPlain: describe(actionButton("Просто", () => {})),
  buttonDisabled: describe(actionButton("Нельзя", () => {}, { disabled: true })),
  statusOk: describe(actionStatus("Готово", "ok")),
  statusErr: describe(actionStatus("Ошибка", "err")),
  statusPlain: describe(actionStatus("Просто")),
  empty: describe(gameEmpty("Ничего нет")),
  loadingDefault: describe(gameLoading()),
  loadingText: describe(gameLoading("Идёт загрузка")),
  errorRetry: describe(gameError("Сбой", () => {})),
  errorNoRetry: describe(gameError("Сбой")),
};
console.log(JSON.stringify(out));
"""


def _js():
    return APP_JS_PATH.read_text(encoding="utf-8")


def _function_source(js, name):
    """The full `function name(...) { ... }` source, paren+brace-matched.

    The body brace is the first `{` AFTER the parameter list's closing `)`
    (default params like `attrs = {}` carry their own braces).
    """
    start = js.index(f"function {name}(")
    depth, quote = 1, None
    j = js.index("(", start) + 1  # the opening paren is already counted
    while j < len(js):
        c = js[j]
        if quote:
            quote = None if c == quote else quote
        elif c in "\"'`":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                break
        j += 1
    k = js.index("{", j)
    depth, quote = 1, None
    for j in range(k + 1, len(js)):  # the opening brace is already counted
        c = js[j]
        if quote:
            quote = None if c == quote else quote
        elif c in "\"'`":
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return js[start : j + 1]
    raise AssertionError(f"unbalanced braces in function {name!r}")


def _game_shell_block(js):
    """The #306 helper section, from its banner comment to the #295 comment."""
    start = js.index("/* ---------- #306: game-shell primitives")
    end = js.index("/* #295: display dictionaries", start)
    return js[start:end]


def _css_rule(css, selector):
    i = css.index(selector + " {")
    return css[i:css.index("}", i)]


@pytest.fixture(scope="module")
def harness_out(tmp_path_factory):
    js = _js()
    harness = (
        HARNESS
        .replace("__APPEND_KIDS__", _function_source(js, "appendKids"))
        .replace("__EL__", _function_source(js, "el"))
        .replace("__GAME_SHELL_BLOCK__", _game_shell_block(js))
    )
    path = tmp_path_factory.mktemp("m306") / "harness.js"
    path.write_text(harness, encoding="utf-8")
    result = subprocess.run(["node", str(path)], capture_output=True, text=True,
                            check=False)
    assert result.returncode == 0, f"node harness failed:\n{result.stderr}"
    return json.loads(result.stdout)


def test_game_header_semantics(harness_out):
    h = harness_out["header"]
    assert h["tag"] == "HEADER", "gameHeader must return a <header> element"
    assert h["class"] == "game-header"
    title, meta = h["children"]
    assert title["tag"] == "H2"
    assert title["class"] == "game-header-title"
    assert title["text"] == "Тест"
    assert meta["tag"] == "DIV"
    assert meta["class"] == "game-header-meta"
    assert meta["text"] == "День 1, 08:00"
    # meta omitted → no meta child
    assert len(harness_out["headerNoMeta"]["children"]) == 1
    # Georgia binding: the title is an h2, and the CSS binds Georgia 400 to
    # .game-shell h1/h2/h3 (the header renders inside .game-shell).
    css = CSS_PATH.read_text(encoding="utf-8")
    rule = _css_rule(css, ".game-shell h1, .game-shell h2, .game-shell h3")
    assert "font-family: Georgia" in rule
    assert "font-weight: 400" in rule


def test_context_panel_semantics(harness_out):
    p = harness_out["panel"]
    assert p["tag"] == "SECTION"
    assert p["class"] == "game-panel"
    title = p["children"][0]
    assert title["tag"] == "H3"
    assert title["class"] == "game-panel-title"
    assert title["text"] == "Панель"
    body = p["children"][1]
    assert body["tag"] == "DIV"
    assert "game-state-empty" in body["class"].split()
    assert p["text"] == "Панельтелопусто"
    # title omitted → no h3 child, body text only
    nt = harness_out["panelNoTitle"]
    assert nt["children"] == []
    assert nt["text"] == "только тело"


def test_action_button_semantics(harness_out):
    b = harness_out["button"]
    assert b["tag"] == "BUTTON"
    assert b["class"] == "game-action game-action-primary"
    assert b["attrs"].get("type") == "button"
    assert b["attrs"].get("title") == "Подсказка"
    assert b["hasOnclick"] is True
    assert b["text"] == "Пойти"
    # no kind → exactly the base class
    assert harness_out["buttonPlain"]["class"] == "game-action"
    # disabled → the attribute is present
    assert "disabled" in harness_out["buttonDisabled"]["attrs"]
    # The >= 44px target comes from the stylesheet, not the DOM.
    css = CSS_PATH.read_text(encoding="utf-8")
    rule = _css_rule(css, ".game-action")
    assert "min-height: 44px" in rule


def test_action_status_semantics(harness_out):
    ok = harness_out["statusOk"]
    assert ok["tag"] == "DIV"
    assert ok["class"] == "game-status game-status-ok"
    assert ok["text"] == "Готово"
    err = harness_out["statusErr"]
    assert err["class"] == "game-status game-status-err"
    assert err["text"] == "Ошибка"
    assert harness_out["statusPlain"]["class"] == "game-status"


def test_state_nodes(harness_out):
    e = harness_out["empty"]
    assert e["tag"] == "DIV"
    assert e["class"] == "game-state game-state-empty"
    assert e["text"] == "Ничего нет"
    ld = harness_out["loadingDefault"]
    assert ld["class"] == "game-state game-state-loading"
    assert ld["text"] == "Загрузка…"
    assert harness_out["loadingText"]["text"] == "Идёт загрузка"
    err = harness_out["errorRetry"]
    assert err["class"] == "game-state game-state-error"
    assert err["text"].startswith("Сбой")
    buttons = [c for c in err["children"] if c["tag"] == "BUTTON"]
    assert len(buttons) == 1
    assert buttons[0]["text"] == "Повторить"
    assert "game-action" in buttons[0]["class"].split()
    assert buttons[0]["hasOnclick"] is True
    # no retry → no button child
    assert harness_out["errorNoRetry"]["children"] == []
    assert harness_out["errorNoRetry"]["text"] == "Сбой"


def test_helpers_never_touch_innerhtml(harness_out):
    # Executed: the DOM stub's innerHTML setter throws — the harness above
    # would have failed if any helper wrote innerHTML.
    # Source: the extracted #306 block is textContent-only.
    block = _game_shell_block(_js())
    assert "innerHTML" not in block
    assert "insertAdjacentHTML" not in block
    assert "document.write" not in block
