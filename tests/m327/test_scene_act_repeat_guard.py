"""m327 scene-action repeat guard — source-contract pins: the Place
   scene-action input's keydown handler must ignore OS auto-repeat Enter
   (a held Enter re-submits the same action once the #304 latch releases,
   especially on a rejection that keeps S.sceneActDraft), while a plain
   Enter still submits.

   SOURCE-STRUCTURE pins only (app.js read as text) — NOT behaviour proof:
   the actual held-Enter double-POST is verified by a separate browser
   test. Expected RED on base 24bfc8e: the keydown handler has no
   `ev.repeat` guard. The plain-Enter submit pin and the #304 latch /
   draft / send-button preservation pins pass on base and must stay green.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = (ROOT / "backend" / "app" / "web" / "app.js").read_text(encoding="utf-8")


# ---- helpers (m270 house pattern) ----
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


def _scene_act_bar_body():
    """Brace-balanced body of sceneActBar() — the #scene-act-input host."""
    return _balanced(APP_JS, "function sceneActBar(", "{", "}")


def _keydown_body():
    """Brace-balanced body of the input's onkeydown arrow handler."""
    return _balanced(_scene_act_bar_body(), "onkeydown:", "{", "}")


def _submit_scene_act_body():
    """Brace-balanced body of submitSceneAct() — the single /scene/act POST."""
    return _balanced(APP_JS, "function submitSceneAct(", "{", "}")


# ---- RED on base 24bfc8e: held-Enter auto-repeat must not re-submit ----
def test_keydown_handler_guards_keyboard_repeat():
    body = _keydown_body()
    assert "&& !ev.repeat" in body, (
        "scene-act keydown handler must ignore auto-repeat events "
        f"(no && !ev.repeat guard in: {body!r})"
    )


# ---- green on base: the guard must not over-block a plain Enter ----
def test_keydown_handler_still_submits_plain_enter():
    body = _keydown_body()
    assert "onSubmit()" in body, (
        f"scene-act keydown handler must still submit on Enter: {body!r}"
    )


# ---- preservation pins: green on base 24bfc8e, must stay green ----
def test_scene_act_input_id_preserved():
    assert 'id: "scene-act-input"' in _scene_act_bar_body()


def test_draft_on_keystroke_literal_exact_once():
    literal = "S.sceneActDraft = ev.target.value;"
    n = APP_JS.count(literal)
    assert n == 1, f"draft-on-keystroke literal expected once, found {n}"


def test_send_button_type_button_preserved():
    btn = _balanced(_scene_act_bar_body(), 'el("button", { id: "scene-act-go"',
                    "(", ")")
    assert 'id: "scene-act-go"' in btn
    assert 'type: "button"' in btn, f"send button must stay type button: {btn!r}"


def test_latch_literals_in_submit_scene_act():
    body = _submit_scene_act_body()
    assert "const token = beginActionSubmission();" in body, (
        f"#304 latch acquisition drifted: {body[:400]!r}"
    )
    assert "if (!token) return;" in body


def test_latch_acquired_before_first_await():
    body = _submit_scene_act_body()
    acquire = body.index("beginActionSubmission()")
    first_await = body.index("await api(")
    assert acquire < first_await, (
        "beginActionSubmission() must be acquired before the first await "
        f"(latch at {acquire}, await at {first_await})"
    )
