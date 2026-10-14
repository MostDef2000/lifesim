"""#175 — Loading / Errors / Offline states (design #80, panel 12) + #/external route fix.

The issue body is the spec of record (gh issue view 175; design of record:
gh issue view 80, panel 12). Bounded frontend scope A–F, FINAL.

Byte-pins over backend/app/web/app.js + backend/app/web/style.css, in the
m157/m158/m170 house style (exact literal counts — update deliberately,
never weaken):

- A. #/external route: registered in the router registry exactly once —
  cold-load/re-route no longer falls back to viewWorld (hash rewrite +
  render race, feed stopped for the whole visit). Its catch joins the #93
  loadRetryNode pattern (site #7) instead of the dead «Загрузка…» with a
  toast-only catch.
- B. Connection banner: the liveness oracle is CONSECUTIVE pollEvents
  failures (>= 2 → S.connLost + persistent top banner); any full refresh
  success clears the streak and, if the banner was up, drops it and toasts
  «Связь восстановлена» once. The banner node is created lazily by
  ensureConnBanner() and appended to document.body exactly once (index.html
  must not grow a second static node — #toast stays the only one). WS close
  is routine on navigation (stopFeed detaches + closes) and must NEVER drive
  the banner — negative-pinned by slicing the ws.onclose body and asserting
  the banner state is absent from it.
- C. viewAdmin: toast-only catch replaced by loadRetryNode (site #8) —
  no dead «Загрузка…» after a failed /admin/overview fetch.
- D. Input preservation: S.chatDraft — the chat composer seeds its value
  from S.chatDraft, keeps it in sync on every keystroke, and clears it only
  on the true success path (the mid-flight NPC-switch guard must NOT clear
  it — a stale closure would clobber the freshly rendered composer's
  draft). Scene-act draft is additionally captured oninput (the post-submit
  pattern alone loses text to a re-inspect while typing). Travel keeps its
  by-construction survival (no draft state — out of scope).
- E. Portrait 404: non-503 failures show the honest non-error note
  «Портрет ещё не готов»; 503 keeps its S.visualDisabled negative cache and
  the m132-pinned keep-button comment stays byte-exact.
- F. CSS: append-only .conn-banner at the file tail (warm-warn family on
  the dark panel bg, fixed top-center, .hidden toggle like .toast);
  --w-line token count stays == 4 (no new token mirror).
"""

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
APP_CSS = os.path.join(ROOT, "backend/app/web/style.css")

BANNER_TEXT = "Нет связи с островом — переподключение…"
RECOVERY_TEXT = "Связь восстановлена"
PORTRAIT_404_TEXT = "Портрет ещё не готов"


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------- A + C: route fix + retry affordance on both load screens ----------

def test_external_route_registered():
    js = _read(APP_JS)
    # The route exists exactly once: cold-load and re-route of #/external
    # resolve the real view instead of silently falling back to viewWorld.
    assert js.count('"#/external": viewExternal') == 1
    assert js.count("async function viewExternal()") == 1
    # The view still pins its own hash on entry (unchanged behaviour).
    assert js.count('location.hash = "#/external";') == 1


def test_load_retry_sites_9_external_and_admin():
    js = _read(APP_JS)
    # #175 raised the #93 transient-failure pattern from 6 to 8
    # alpha-critical sites: viewExternal (site #7) and viewAdmin (site #8)
    # replaced their toast-only catches — a transient fetch failure now
    # explains itself and offers «Повторить» instead of a dead «Загрузка…».
    assert js.count("loadRetryNode(loading, () => view") == 9
    assert js.count("loadRetryNode(loading, () => viewExternal());") == 1
    assert js.count("loadRetryNode(loading, () => viewAdmin());") == 1
    # Both views keep a named loading node in scope for the retry rewrite;
    # 9 views carry the pattern (6 pre-#175 + 2 from #175 + profile from #67).
    assert js.count("const loading = el(\"div\", { class: \"muted\" }, \"Загрузка…\");") == 10


# ---------- B: connection banner (poll failures are the liveness oracle) ----------

def test_connection_banner_state_pins():
    js = _read(APP_JS)
    # State rides on S: init field + the three transition sites.
    # S init: one line, after visualDisabled (m132 substring intact).
    assert js.count("connLost: false, pollFails: 0,") == 1
    assert js.count("S.connLost = true;") == 1   # armed on the 2nd consecutive failure
    assert js.count("S.connLost = false;") == 1  # cleared on the first full-refresh success
    assert js.count("S.pollFails += 1;") == 1    # failure streak in the poll catch
    assert js.count("S.pollFails = 0;") == 1     # streak reset on success
    # The banner arms on the SECOND consecutive failure (one blip stays quiet).
    assert js.count("S.pollFails >= 2") == 1
    # The old silent-catch byte literal is gone on purpose (m162 pins the
    # new shape); the finally chain still releases the in-flight guard.
    assert js.count(
        "} catch (e) { /* silent */ } finally { S.pollBusy = false; }") == 0
    assert js.count("finally { S.pollBusy = false; }") == 1


def test_connection_banner_node_pins():
    js = _read(APP_JS)
    # One lazy renderer + one hider, both keyed on the single node id.
    assert js.count("function ensureConnBanner()") == 1
    assert js.count("function hideConnBanner()") == 1
    assert js.count("document.getElementById(\"conn-banner\")") == 2
    # role="status" so screen readers announce the state change; created via
    # el() (textContent-only — no innerHTML anywhere, see hygiene test).
    assert js.count("id: \"conn-banner\", class: \"conn-banner\", role: \"status\"") == 1
    # The fixed wording, exactly once.
    assert js.count(BANNER_TEXT) == 1
    # Recovery feedback is a one-shot toast (house toast mechanics), not a
    # second persistent surface.
    assert js.count(RECOVERY_TEXT) == 1
    assert js.count("hideConnBanner();") == 1
    assert js.count("ensureConnBanner();") == 1


def test_ws_close_never_drives_the_banner():
    """Negative pin (B): WS close is routine — every navigation detaches and
    closes the old socket, so reconnect churn must not flicker a «Нет связи»
    banner. The liveness oracle is pollEvents failures ONLY: the onclose
    body must not touch the banner state at all (checked by block slice, so
    a future edit inside onclose cannot silently couple it)."""
    js = _read(APP_JS)
    assert js.count("ws.onclose = () => {") == 1
    start = js.index("ws.onclose = () => {")
    end = js.index("\n  };", start)
    onclose_body = js[start:end]
    assert "connLost" not in onclose_body
    assert "ConnBanner" not in onclose_body
    assert "pollFails" not in onclose_body


# ---------- D: typed input survives re-renders ----------

def test_chat_draft_survival_pins():
    js = _read(APP_JS)
    # The draft state exists exactly once in the S initializer.
    # #177 appended bootCursor as the LAST property after chatDraft — the
    # init tail moved; literal updated to the new truthful bytes.
    assert js.count("chatDraft: \"\",\n  bootCursor: Infinity };") == 1
    # Seeded on every composer render (NPC switch / re-render keeps text).
    assert js.count("input.value = S.chatDraft || \"\";") == 1
    # Kept in sync on every keystroke (el()-created node, addEventListener —
    # the m156-pinned el("input", …) literal itself stays untouched).
    assert js.count(
        "input.addEventListener(\"input\", () => { S.chatDraft = input.value; });") == 1
    # Cleared ONLY on the true success path — the mid-flight NPC-switch
    # guard intentionally does NOT clear it (a stale closure would clobber
    # the freshly re-rendered composer's draft).
    assert js.count("S.chatDraft = \"\";") == 1
    # m156 gold literals untouched (mirrored guard; m156 pins them too):
    # exactly two post-POST clears, one error-path restore.
    assert js.count("input.value = \"\";") == 2
    assert js.count("input.value = draft;") == 1


def test_scene_act_draft_oninput_pins():
    js = _read(APP_JS)
    # The draft is captured on every keystroke, not only on submit — a
    # re-inspect while typing no longer destroys the text.
    assert js.count("oninput: (ev) => { S.sceneActDraft = ev.target.value; }") == 1
    # The seeded value read stays single-site.
    assert js.count("value: S.sceneActDraft || \"\"") == 1
    # The #175 edit left the post-submit pattern intact (m158 pins these).
    assert js.count("S.sceneActDraft = text;") == 2   # 200 ok:false AND catch
    assert js.count("S.sceneActDraft = null;") == 1   # only a real success clears


# ---------- E: portrait 404 honest note ----------

def test_portrait_404_honest_note_pins():
    js = _read(APP_JS)
    # Non-503 failures (404 no-canonical-yet, transient network) now say so
    # instead of failing silently; the keep-button comment stays byte-exact
    # (m132 NO_PORTRAIT_COMMENT) and the 503 → S.visualDisabled negative
    # cache is unchanged.
    assert js.count(PORTRAIT_404_TEXT) == 1
    assert js.count(f"else status.textContent = \"{PORTRAIT_404_TEXT}\";") == 1
    # Order: the 503 branch, then the honest note, then the pinned comment.
    assert js.index("if (e && e.status === 503) S.visualDisabled = true;") \
        < js.index(f"else status.textContent = \"{PORTRAIT_404_TEXT}\";") \
        < js.index("/* no portrait yet — keep button */")


# ---------- F: CSS + file-wide hygiene ----------

def test_css_conn_banner_appended_at_tail():
    css = _read(APP_CSS)
    # Append-only tail convention: the banner block is the last rule.
    assert css.count(".conn-banner {") == 1
    assert css.count(".conn-banner.hidden { display: none; }") == 1
    assert css.rstrip().endswith(".conn-banner.hidden { display: none; }")
    # Warm-warn family on the dark panel bg (same tokens as .toast.err).
    body = css[css.index(".conn-banner {"):css.index("}", css.index(".conn-banner {"))]
    assert "var(--warn)" in body
    assert "var(--panel)" in body
    assert "position: fixed" in body
    # No new token mirror: the --w-line count stays == 4 (m170/m173 pin).
    assert css.count("--w-line:") == 4


def test_hygiene_and_neighbour_pins_untouched():
    js = _read(APP_JS)
    # XSS hygiene, file-wide (pinned by every m-suite): textContent-only.
    assert js.count("innerHTML") == 0
    # m132 S-init substring intact — the #175 flags ride AFTER visualDisabled.
    assert "authProbed: false, visualDisabled: false," in js
    # m132 visual-disabled cache unchanged (single set site, single clear).
    assert js.count("if (!S.visualDisabled) {") == 1
    assert js.count("S.visualDisabled = true") == 1
    assert js.count("S.visualDisabled = false") == 1
    # m162 WS/navigation hard pins survive the #175 edits.
    assert js.count('location.hash = "#/world";') == 4
    assert js.count("gotoWorld();") == 4
    assert js.count("S.ws = null;") <= 2
    assert js.count("if (S.ws === ws) S.ws = null;") == 1
    # m156/m162 negative guards: no new occurrences of the pinned singletons.
    assert js.count('location.hash === "#/world"') == 1
    assert js.count(
        "toast(isAutonomousRefusal(e) ? AUTONOMOUS_HINT : e.message, true);") == 1
    assert js.count("} else { toast(e.message, true); }") == 2
    # Shared retry helper unchanged: one const, one def, hint reused 4×.
    assert js.count("const RETRY_HINT =") == 1
    assert js.count("function loadRetryNode(") == 1
    assert js.count("RETRY_HINT") == 4
