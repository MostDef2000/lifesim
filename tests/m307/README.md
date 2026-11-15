# #307 — playable-journey E2E suite

Behavioral + manual-acceptance tests for the **canonical six-stage playable
journey** in the REAL production `backend/app/web/app.js` (boot included). The
suite is split into a CI pytest truth (the vm harness) and a manual browser
acceptance truth (the Playwright companion), mirroring the m313 split.

## What the journey is

Deterministic identities: **player=1, start location=1, destination=2,
destination NPC=2, MOVE task=30701, scene task=30702, dialogue session=30703,
feed event=30704, DIRECT mode.**

The canonical six stages:

1. **World** (loc 1, empty tasks)
2. **select POI 2** + preview `#/location/2` + return World
3. **explicit MOVE** (accepted → active → completed; the authoritative read
   reports loc 2)
4. **enter current Place 2**, inspect the scene, submit a task-producing scene
   act
5. **Chat**: select NPC 2, start the conversation, send one message
6. **Tasks** shows 30702, return World at loc 2

## Files

| File | Role |
|---|---|
| `journey_harness.py` | the shared vm-harness: an OWN copy of the m311 shim (minimal DOM, fake timers, controlled network, rich WS fake, blob machinery) extended with the journey surface (`__type`, `__stageButton`, the journey `__facts()`, the `taskSubmitFeedback` accessor). It does NOT mock any journey logic. |
| `test_journey_contract.py` | 6 tests — the journey contract (identity preserved at every stage, the remote preview commits no travel, the MOVE handoff, the scene-task handoff, the destination roster, the feed event survives the route chain once). |
| `test_network_contract.py` | 11 tests — the network contract (exactly 5 POSTs, exact order, exact bodies, no non-GET/HEAD beyond the 5 POSTs, reads unpinned) + the #307 correction's harder contract (zero-mutation navigation prefix, no scene-image regeneration on later routes, read-only task revisits do not replay accepted intents, feed transport ownership bounded until logout, no hidden geolocation entrypoints in the source). |
| `test_failure_matrix.py` | 14 tests — one per state case (Normal/NoTasks/Remote/Current/NoNpc/FailedImage/Busy/Offline/Autonomous/Critical) + the #307 correction's 3 (VisualUnavailable, FailedDialogue roundtrip, OldPlaceImage) + the design-original RefusedSceneRetry (the refused scene draft survives a navigation roundtrip and an explicit retry with corrected text); each injects one failure, asserts it is handled gracefully, and asserts the journey continues into a later stage. The 6 extended cases (NoTasks/NoNpc/FailedImage/Busy/Offline/Autonomous) also assert the correction's retry / no-replay / zero-POST contracts. |
| `journey.browser.js` | the MANUAL Playwright companion (13 cases × {1440×900, 390×844}); NOT collected by pytest, NOT run by CI. |
| `acceptance-matrix.md` | the per-test / per-case evidence + status matrix. |

## Commands

### CI pytest truth (the vm harness)

```bash
# the whole #307 suite (31 tests)
.venv/bin/python -m pytest tests/m307 -q

# focused
.venv/bin/python -m pytest tests/m307/test_journey_contract.py -q
.venv/bin/python -m pytest tests/m307/test_network_contract.py -q
.venv/bin/python -m pytest tests/m307/test_failure_matrix.py -q
```

The harness writes the Node runner + scenarios to
`/tmp/opencode/m307-testing/` and runs `node` against the real
`backend/app/web/app.js`. No server, no network, no browser.

### Manual browser acceptance (the companion)

The companion is a Playwright `async (page) => {}` function run by the PARENT
via the native browser MCP / run_code. It is NOT run by CI.

**Fixture mode (default):** start the local static server, then run the
companion with `BASE = http://127.0.0.1:8772`:

```bash
python3 -m http.server 8772 --bind 127.0.0.1 --directory backend/app/web
```

**Probe-server mode:** the parent sets `window.__M307_PROBE_BACKEND` (a real
backend origin) via `page.addInitScript` BEFORE calling the companion. In probe
mode no route interception is installed (the real backend serves the APIs) and
the request/WS capture is passive. The two modes are **never silently
substituted**: if probe mode is requested but the backend is unreachable the
companion throws (fail-closed).

Screenshots land under `/tmp/opencode/playwright-dumps/lifesim-307/`.

## Fixtures

The endpoint map is built by `journey_harness._journey_responses()` (boot + all
five mutations). The deterministic fixtures:

**Id types (issue #338):** character ids and dialogue session ids are STRINGS
(`"1"`, `"101"`, `"2"`, `"30703"` — the production shape); task ids (30701,
30702), the feed event id (30704), and location ids (1–7) are INTEGERS. The
#307 fixtures originally carried numeric character/session ids; they now model
the production types.

- `CHAR1` (DIRECT, loc 1) / `CHAR1_AT_2` (loc 2) / `CHAR1_AUTONOMOUS`.
- `CATALOG` (7 keyed POI types; loc 1 = origin NPC Иван/101, loc 2 =
  destination NPC Фар/2) / `CATALOG_NO_NPC` (loc 2 has no occupant).
- `SCENE_ASSET_2` — passes BOTH `inspectSurroundings` validations
  (`asset.location_id === 2` AND `asset.scene_descriptor.location.id === 2`).
- `TASK_30701` (MOVE, completed) / `TASK_30702` (SOCIALIZE, active).
- `DIALOGUE_START` (`{session_id: "30703"}`) / `DIALOGUE_MESSAGE_OK` /
  `DIALOGUE_HISTORY`.
- `FEED_EVENT_30704` (a live event beyond the boot cursor).

The five canonical mutations and their exact POST bodies:

| # | URL | body |
|---|---|---|
| 1 | `/actions` | `{character_id:"1", action_type:"MOVE", params:{location_id:2}}` |
| 2 | `/visual/scenes` | `{location_id:2}` |
| 3 | `/scene/act` | `{text:"Поговорить с Фаром"}` |
| 4 | `/dialogue/start` | `{npc_id:"2"}` |
| 5 | `/dialogue/30703/message` | `{content:"Привет, Фар"}` |

## Harness limits

The harness provides the **browser surface only** (a minimal DOM, fake timers,
a controlled network boundary, a rich WS fake, the blob machinery). It does NOT
mock or reimplement `route()`, `api()`, `viewWorld()`, `viewLocation()`,
`viewChat()`, `viewTasks()`, `moveTo()`, `inspectSurroundings()`,
`submitSceneAct()`, `acceptTask()`, or any other journey logic. Every assertion
is on an observable fact (the fetch census, the rendered DOM, the `S` /
`gameUiContext` / task-presentation state, the `taskSubmitFeedback` caption).

Known shim artifacts (faithful to browser semantics where it matters):

- `select.value` carries DOMString semantics for SELECT nodes only: since the
  #338 harness upgrade, the fake DOM coerces every value assigned to a SELECT
  with `String(v)` (a real browser always stringifies `select.value`). The
  coercion is select-only — no option/selectedIndex semantics are implemented
  (the harness does not model options; the app assigns and reads `.value`
  directly). Non-select elements keep the plain `.value` property. Before the
  #338 fix the fake DOM stored `.value` as a plain property WITHOUT coercion,
  which is why the vm suite could not see the DOMString-vs-numeric id
  comparison bug the real-Chromium companion caught (GAP-2, issue #338).
- The `taskSubmitLine` DOM host is only present when mounted; the
  `taskSubmitFeedback` module variable is the source of truth for the
  busy/offline captions.
- The MOVE lifecycle is simulated in-driver: the authoritative read
  (`/characters/1` → loc 2, `/characters/1/tasks` → 30701 completed) is applied
  via `__setResponse` + a `pollEvents()` fire.

## RED policy

- **Genuine product defect** → `@pytest.mark.xfail(strict=True,
  reason="pending issue: …")` + a BLOCKER flag in the report.
- **Harness / fixture fault** → fix the harness (never weaken the assertion).
- **Never** weaken an assertion to make a change pass.
- The geolocation source pin must **never** be xfailed.

No genuine product defect was found in this wave: all 31 pytest tests pass
green. The failure-matrix "failures" during development were harness
artifacts (reading an unmounted DOM host instead of the source-of-truth
variable; a missing `/auth/logout` fixture that left the feed socket open),
fixed in the harness. The #307 correction's harder contract (8 new test
functions + 6 extended assertions) and the design-original RefusedSceneRetry
are green: the app preserves the chat draft across a Tasks→Chat roundtrip,
preserves the refused scene-act draft across a navigation roundtrip (registering
30702 only on the successful retry), discards a stale location-1 scene flight
after the move (token check), does not auto-retry a failed visual request, and
does not fire a message POST by navigation.
