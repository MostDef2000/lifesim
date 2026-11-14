"""#307 — playable-journey E2E: the failure matrix.

Behavioral tests for the canonical playable journey in the REAL production
``backend/app/web/app.js`` (boot included), run in the shared
``journey_harness`` vm context. Each case injects ONE failure/condition, drives
the journey to the point where it manifests, asserts the failure is handled
gracefully (the specific observable), and asserts the journey CONTINUES into a
later stage (the app does not crash, no unhandled error escapes).

The ten state cases (deterministic identities: player=1, start location=1,
destination=2, destination NPC=2, MOVE task=30701, scene task=30702, dialogue
session=30703, DIRECT mode unless noted):

  1. NORMAL        — control: the scene act succeeds (task 30702), continues to chat.
  2. NO_TASKS      — the Tasks screen shows the empty states, continues to World.
  3. REMOTE        — a remote place shows the move CTA, no scene-act bar.
  4. CURRENT       — the current place shows the scene-act bar.
  5. NO_NPC        — the destination has no NPC; the nearby roster is empty.
  6. FAILED_IMAGE  — the scene asset file blob rejects; the journey continues.
  7. BUSY          — the MOVE is rejected 409-busy; the player stays at the origin.
  8. OFFLINE       — the MOVE network-fails; the offline caption, player stays.
  9. AUTONOMOUS    — the character is AUTONOMOUS; the move is gated (no POST).
  10. CRITICAL     — the scene act returns ok:false; the draft is kept, no task.

Every assertion is on an observable fact. The harness does NOT mock any journey
logic.
"""

import json

import pytest

from tests.m307.journey_harness import (
    CATALOG_NO_NPC,
    CHAR1,
    CHAR1_AT_2,
    CHAR1_AUTONOMOUS,
    DIALOGUE_MESSAGE_OK,
    SCENE_ASSET_1,
    TASK_30701,
    _journey_responses,
    _resp,
    _tasks_resp,
    facts,
    run_scenarios,
    unhandled,
)

# Fixtures embedded into the driver strings (the authoritative-read updates are
# applied via __setResponse with inline JSON).
CHAR1_AT_2_JSON = json.dumps(CHAR1_AT_2)
CHAR1_JSON = json.dumps(CHAR1)
TASKS_30701_DONE_JSON = json.dumps(_tasks_resp(recent=[TASK_30701]))
# A successful /actions MOVE acceptance (the retry responses).
MOVE_OK_JSON = json.dumps({"task_id": 30701, "task_type": "MOVE",
                           "task_label": "Перемещение", "status": "planned"})
DIALOGUE_MESSAGE_OK_JSON = json.dumps(DIALOGUE_MESSAGE_OK)
SCENE_ASSET_1_JSON = json.dumps(SCENE_ASSET_1)
# A successful /scene/act acceptance (registers task 30702) — the retry
# response for the refused-scene roundtrip.
SCENE_ACT_OK_JSON = json.dumps({"ok": True, "task_id": 30702,
                                "task_type": "SOCIALIZE",
                                "task_label": "Пообщаться"})

# The shared "arrive at loc 2" prefix (the authoritative read reports loc 2).
_ARRIVE = (
    "__setResponse('/characters/1', {status:200, body: " + CHAR1_AT_2_JSON + "}); "
    "__setResponse('/characters/1/tasks', " + TASKS_30701_DONE_JSON + "); "
    "await __call('pollEvents'); await __settle(); "
)


def _busy_responses():
    d = _journey_responses()
    d["/actions"] = _resp(409, {"detail": "character already has an active task"})
    return d


def _offline_responses():
    d = _journey_responses()
    d["/actions"] = _resp(200, None, __networkFail=True)
    return d


def _autonomous_responses():
    return _journey_responses(char=CHAR1_AUTONOMOUS)


def _no_npc_responses():
    return _journey_responses(catalog=CATALOG_NO_NPC)


def _failed_image_responses():
    d = _journey_responses()
    d["/visual/assets/sc2/file"] = _resp(200, None, __blobReject="blob failure")
    return d


def _critical_responses():
    d = _journey_responses()
    d["/scene/act"] = _resp(200, {"ok": False, "detail": "critical failure"})
    return d


def _visual_unavailable_responses():
    d = _journey_responses()
    d["/visual/scenes"] = _resp(503, {"detail": "visual unavailable"})
    return d


def _failed_dialogue_responses():
    d = _journey_responses()
    d["/dialogue/30703/message"] = _resp(200, None, __networkFail=True)
    return d


SCENARIOS = [
    {
        "name": "NORMAL",
        "initialHash": "#/world",
        "responses": _journey_responses(),
        "driver": (
            "await __settle(); " + _ARRIVE
            + "__setHash('#/location/2'); await __settle(); "
            "__stageButton(); await __settle(); "
            "__type('scene-act-input', 'Поговорить с Фаром'); "
            "__clickButton('Сделать'); await __settle(); "
            "const atPlace = __facts(); "
            "__clickButton('Поговорить'); await __settle(); "
            "const atChat = __facts(); "
            "globalThis.__result = { atPlace, atChat };"
        ),
    },
    {
        "name": "NO_TASKS",
        "initialHash": "#/world",
        "responses": _journey_responses(),
        "driver": (
            "await __settle(); "
            # the full read-only chain: World -> remote Place -> Chat -> Tasks
            "__setHash('#/location/2'); await __settle(); "
            "__setHash('#/chat'); await __settle(); "
            "__setHash('#/tasks'); await __settle(); "
            "const atTasks = __facts(); "
            "__setHash('#/world'); await __settle(); "
            "const back = __facts(); "
            "globalThis.__result = { atTasks, back };"
        ),
    },
    {
        "name": "REMOTE",
        "initialHash": "#/world",
        "responses": _journey_responses(),
        "driver": (
            "await __settle(); "
            "__setHash('#/location/2'); await __settle(); "
            "const atPlace = __facts(); "
            "__clickButton('← Остров'); await __settle(); "
            "const back = __facts(); "
            "globalThis.__result = { atPlace, back };"
        ),
    },
    {
        "name": "CURRENT",
        "initialHash": "#/world",
        "responses": _journey_responses(),
        "driver": (
            "await __settle(); " + _ARRIVE
            + "__setHash('#/location/2'); await __settle(); "
            "const atPlace = __facts(); "
            "globalThis.__result = { atPlace };"
        ),
    },
    {
        "name": "NO_NPC",
        "initialHash": "#/world",
        "responses": _no_npc_responses(),
        "driver": (
            "await __settle(); " + _ARRIVE
            + "__setHash('#/location/2'); await __settle(); "
            "const atPlace = __facts(); "
            # submit a scene act successfully (task 30702)
            "__stageButton(); await __settle(); "
            "__type('scene-act-input', 'Поговорить с Фаром'); "
            "__clickButton('Сделать'); await __settle(); "
            "const atScene = __facts(); "
            # open Tasks and assert 30702 present
            "__setHash('#/tasks'); await __settle(); "
            "const atTasks = __facts(); "
            "globalThis.__result = { atPlace, atScene, atTasks };"
        ),
    },
    {
        "name": "FAILED_IMAGE",
        "initialHash": "#/world",
        "responses": _failed_image_responses(),
        "driver": (
            "await __settle(); " + _ARRIVE
            + "__setHash('#/location/2'); await __settle(); "
            "__stageButton(); await __settle(); "
            "const atPlace = __facts(); "
            # submit a scene act successfully (task 30702)
            "__type('scene-act-input', 'Поговорить с Фаром'); "
            "__clickButton('Сделать'); await __settle(); "
            "const atScene = __facts(); "
            # continue to chat (the journey continues)
            "__clickButton('Поговорить'); await __settle(); "
            "const atChat = __facts(); "
            # open Tasks and assert 30702 present
            "__setHash('#/tasks'); await __settle(); "
            "const atTasks = __facts(); "
            "globalThis.__result = { atPlace, atScene, atChat, atTasks };"
        ),
    },
    {
        "name": "BUSY",
        "initialHash": "#/world",
        "responses": _busy_responses(),
        "driver": (
            "await __settle(); "
            "__clickButton('Магазин'); await __settle(); "
            "__clickButton('Пойти сюда'); await __settle(); "
            "const rejected = __facts(); "
            "__clickButton('Мир'); await __settle(); "
            "const back = __facts(); "
            # retry with a SUCCESSFUL /actions response
            "__setResponse('/actions', {status:200, body: " + MOVE_OK_JSON + "}); "
            "__clickButton('Магазин'); await __settle(); "
            "__clickButton('Пойти сюда'); await __settle(); "
            # the authoritative read reports loc 2
            "__setResponse('/characters/1', {status:200, body: "
            + CHAR1_AT_2_JSON + "}); "
            "__setResponse('/characters/1/tasks', " + TASKS_30701_DONE_JSON + "); "
            "await __call('pollEvents'); await __settle(); "
            "const retried = __facts(); "
            "globalThis.__result = { rejected, back, retried };"
        ),
    },
    {
        "name": "OFFLINE",
        "initialHash": "#/world",
        "responses": _offline_responses(),
        "driver": (
            "await __settle(); "
            "__clickButton('Магазин'); await __settle(); "
            "__clickButton('Пойти сюда'); await __settle(); "
            "const failed = __facts(); "
            "__clickButton('Мир'); await __settle(); "
            "const back = __facts(); "
            # explicit retry with a SUCCESSFUL /actions response
            "__setResponse('/actions', {status:200, body: " + MOVE_OK_JSON + "}); "
            "__clickButton('Магазин'); await __settle(); "
            "__clickButton('Пойти сюда'); await __settle(); "
            "__setResponse('/characters/1', {status:200, body: "
            + CHAR1_AT_2_JSON + "}); "
            "__setResponse('/characters/1/tasks', " + TASKS_30701_DONE_JSON + "); "
            "await __call('pollEvents'); await __settle(); "
            "const retried = __facts(); "
            "globalThis.__result = { failed, back, retried };"
        ),
    },
    {
        "name": "AUTONOMOUS",
        "initialHash": "#/world",
        "responses": _autonomous_responses(),
        "driver": (
            "await __settle(); "
            "__clickButton('Магазин'); await __settle(); "
            "__clickButton('Пойти сюда'); await __settle(); "
            "const gated = __facts(); "
            "__clickButton('Мир'); await __settle(); "
            "const back = __facts(); "
            # transition to DIRECT: the authoritative read reports DIRECT
            "__setResponse('/characters/1', {status:200, body: " + CHAR1_JSON + "}); "
            "await __call('pollEvents'); await __settle(); "
            # the same destination click now submits EXACTLY ONE MOVE POST
            "__clickButton('Магазин'); await __settle(); "
            "__clickButton('Пойти сюда'); await __settle(); "
            "const retried = __facts(); "
            "globalThis.__result = { gated, back, retried };"
        ),
    },
    {
        "name": "CRITICAL",
        "initialHash": "#/world",
        "responses": _critical_responses(),
        "driver": (
            "await __settle(); " + _ARRIVE
            + "__setHash('#/location/2'); await __settle(); "
            "__stageButton(); await __settle(); "
            "__type('scene-act-input', 'Поговорить с Фаром'); "
            "__clickButton('Сделать'); await __settle(); "
            "const atPlace = __facts(); "
            "__clickButton('Поговорить'); await __settle(); "
            "const atChat = __facts(); "
            "globalThis.__result = { atPlace, atChat };"
        ),
    },
    {
        # The design's test_refused_scene_draft_survives_navigation_and_
        # explicit_retry: the scene act is refused (ok:false), the draft
        # survives a navigation roundtrip, and an explicit retry with
        # CORRECTED text succeeds (only the successful acceptance registers
        # 30702). Total /scene/act POSTs == 2; /visual/scenes == 1.
        "name": "REFUSED_SCENE_RETRY",
        "initialHash": "#/world",
        "responses": _critical_responses(),
        "driver": (
            "await __settle(); " + _ARRIVE
            + "__setHash('#/location/2'); await __settle(); "
            "__stageButton(); await __settle(); "
            "__type('scene-act-input', 'Поговорить с Фаром'); "
            "__clickButton('Сделать'); await __settle(); "
            "const refused = __facts(); "
            # navigate away (World) and back to Place 2
            "__setHash('#/world'); await __settle(); "
            "__setHash('#/location/2'); await __settle(); "
            "const roundtrip = __facts(); "
            # explicit retry with CORRECTED text + a successful response
            "__setResponse('/scene/act', {status:200, body: "
            + SCENE_ACT_OK_JSON + "}); "
            "__type('scene-act-input', 'Поговорить с Фаром у костра'); "
            "__clickButton('Сделать'); await __settle(); "
            "const retried = __facts(); "
            "globalThis.__result = { refused, roundtrip, retried };"
        ),
    },
    {
        # /visual/scenes returns 503: no ready image, but the dialogue and
        # task reads still work; exactly one (failed) visual POST, no retry.
        "name": "VISUAL_UNAVAILABLE",
        "initialHash": "#/world",
        "responses": _visual_unavailable_responses(),
        "driver": (
            "await __settle(); " + _ARRIVE
            + "__setHash('#/location/2'); await __settle(); "
            "__stageButton(); await __settle(); "
            "const atPlace = __facts(); "
            # continue to Chat (start + message)
            "__clickButton('Поговорить'); await __settle(); "
            "__type('chat-msg-input', 'Привет, Фар'); "
            "__clickButton('→'); await __settle(); "
            "const atChat = __facts(); "
            # open Tasks
            "__setHash('#/tasks'); await __settle(); "
            "const atTasks = __facts(); "
            "globalThis.__result = { atPlace, atChat, atTasks };"
        ),
    },
    {
        # /dialogue/30703/message network-fails: the exact typed draft is
        # retained through a Tasks->Chat roundtrip; the explicit retry sends
        # the ORIGINAL content; total message POST attempts == 2.
        "name": "FAILED_DIALOGUE",
        "initialHash": "#/world",
        "responses": _failed_dialogue_responses(),
        "driver": (
            "await __settle(); " + _ARRIVE
            + "__setHash('#/location/2'); await __settle(); "
            # open Chat (select NPC 2)
            "__clickButton('Поговорить'); await __settle(); "
            # type a message and send (network-fails)
            "__type('chat-msg-input', 'Привет, Фар'); "
            "__clickButton('→'); await __settle(); "
            "const failed = __facts(); "
            # navigate Tasks -> Chat (same NPC 2)
            "__setHash('#/tasks'); await __settle(); "
            "__setHash('#/chat'); await __settle(); "
            "const roundtrip = __facts(); "
            # explicit retry with a successful response
            "__setResponse('/dialogue/30703/message', {status:200, body: "
            + DIALOGUE_MESSAGE_OK_JSON + "}); "
            "__clickButton('→'); await __settle(); "
            "const retried = __facts(); "
            "globalThis.__result = { failed, roundtrip, retried };"
        ),
    },
    {
        # A stale location-1 scene response is held, the player moves to 2,
        # then the held OLD response is released: the location-1 image must
        # NOT be installed into the place stage (the flight is invalidated).
        "name": "OLD_PLACE_IMAGE",
        "initialHash": "#/world",
        "responses": _journey_responses(),
        "driver": (
            "await __settle(); "
            # hold the location-1 scene response (the blob fetch only starts
            # after this POST resolves, so it needs no separate hold)
            "__hold('/visual/scenes'); "
            # navigate to Place 1 (location 1), trigger a scene inspection
            "__setHash('#/location/1'); await __settle(); "
            "__stageButton(); await __settle(); "
            # complete the MOVE to location 2 (authoritative poll)
            "__setResponse('/characters/1', {status:200, body: "
            + CHAR1_AT_2_JSON + "}); "
            "__setResponse('/characters/1/tasks', " + TASKS_30701_DONE_JSON + "); "
            "await __call('pollEvents'); await __settle(); "
            # enter Place 2 (no auto-inspection: inspect is click-only)
            "__setHash('#/location/2'); await __settle(); "
            # release the held OLD (location-1) response: the flight
            # continues, then the token check discards it (location changed)
            "__release('/visual/scenes', {status:200, body: "
            + SCENE_ASSET_1_JSON + "}); "
            "await __settle(); "
            # bounded microtask flush so the completion is recorded
            "await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); "
            # staleDelivered: the released stale response actually reached the
            # app (a /visual/scenes completion with status 200 in the log)
            "const _f = __facts(); "
            "const atPlace = Object.assign({}, _f, { staleDelivered: _f.fetchCompletions.some("
            "(c) => c.url === '/visual/scenes' && c.status === 200) }); "
            # dialogue still targets NPC 2
            "__clickButton('Поговорить'); await __settle(); "
            "const atChat = __facts(); "
            "globalThis.__result = { atPlace, atChat };"
        ),
    },
]


@pytest.fixture(scope="module")
def results():
    return run_scenarios(SCENARIOS)


def _f(results, name, key):
    return facts(results, name)[key]


def _uh(results, name):
    uh = unhandled(results, name)
    assert uh == [], f"{name}: no unhandled errors may escape: {uh}"


# ---------- 1. NORMAL — the control: the scene act succeeds ----------

def test_normal_scene_act_succeeds_and_continues_to_chat(results):
    at_place = _f(results, "NORMAL", "atPlace")
    at_chat = _f(results, "NORMAL", "atChat")
    assert at_place["sceneStatus"] == "ready"
    assert "30702" in at_place["taskRegistry"], (
        "the successful scene act must register task 30702")
    assert at_place["sceneActDraft"] is None, (
        "a successful scene act clears the draft")
    # The journey continues into the chat stage.
    assert at_chat["hash"] == "#/chat"
    assert at_chat["chatNpcSelectValue"] == "2"
    _uh(results, "NORMAL")


# ---------- 2. NO_TASKS — the Tasks screen shows the empty states ----------

def test_no_tasks_shows_empty_states_and_continues(results):
    at_tasks = _f(results, "NO_TASKS", "atTasks")
    back = _f(results, "NO_TASKS", "back")
    assert "Нет активных задач" in (at_tasks["tasksActiveText"] or ""), (
        f"the Tasks screen must show the empty active state, "
        f"got {at_tasks['tasksActiveText']!r}")
    assert "История пуста" in (at_tasks["tasksHistoryText"] or ""), (
        f"the Tasks screen must show the empty history state, "
        f"got {at_tasks['tasksHistoryText']!r}")
    # The full read-only chain (World -> remote Place -> Chat -> Tasks ->
    # World) commits zero POSTs across the ENTIRE chain.
    assert back["postCount"] == 0, (
        f"the read-only chain must commit zero POSTs, got {back['postCount']}: "
        f"{[p['url'] for p in back['posts']]}")
    # No fabricated task ids in any task text.
    for text in (at_tasks["tasksActiveText"], at_tasks["tasksHistoryText"]):
        assert "30701" not in (text or ""), (
            f"no fabricated task id 30701 in task text, got {text!r}")
        assert "30702" not in (text or ""), (
            f"no fabricated task id 30702 in task text, got {text!r}")
    # The journey continues back to World.
    assert back["hash"] == "#/world"
    assert back["hasWorldShell"] is True
    _uh(results, "NO_TASKS")


# ---------- 3. REMOTE — a remote place shows the move CTA, no scene-act bar ----------

def test_remote_place_shows_move_cta_not_scene_act(results):
    at_place = _f(results, "REMOTE", "atPlace")
    back = _f(results, "REMOTE", "back")
    assert at_place["hash"] == "#/location/2"
    assert at_place["placeBadgeText"] == "Другое место"
    assert at_place["placeSceneActInputValue"] is None, (
        "a remote place must not render the scene-act bar")
    # The journey continues back to World.
    assert back["hash"] == "#/world"
    _uh(results, "REMOTE")


# ---------- 4. CURRENT — the current place shows the scene-act bar ----------

def test_current_place_shows_scene_act_bar(results):
    at_place = _f(results, "CURRENT", "atPlace")
    assert at_place["hash"] == "#/location/2"
    assert at_place["placeBadgeText"] == "Вы здесь"
    assert at_place["placeSceneActInputValue"] is not None, (
        "the current place must render the scene-act bar")
    _uh(results, "CURRENT")


# ---------- 5. NO_NPC — the destination has no NPC; the nearby roster is empty ----------

def test_no_npc_destination_shows_empty_roster(results):
    at_place = _f(results, "NO_NPC", "atPlace")
    at_scene = _f(results, "NO_NPC", "atScene")
    at_tasks = _f(results, "NO_NPC", "atTasks")
    assert at_place["hash"] == "#/location/2"
    assert at_place["placeNearbyEmpty"] is True, (
        f"the no-NPC destination must show an empty nearby roster, "
        f"got rows {at_place['placeNearbyRows']!r}")
    assert at_place["placeNearbyRows"] == []
    # The scene act succeeds (task 30702) and the Tasks screen shows it.
    assert "30702" in at_scene["taskRegistry"], (
        "the successful scene act must register task 30702")
    assert "30702" in at_tasks["taskRegistry"], (
        "the Tasks screen must show task 30702")
    # Zero dialogue POSTs throughout (there is no NPC to talk to).
    dialogue_posts = [p for p in at_tasks["posts"]
                      if p["url"].startswith("/dialogue/")]
    assert dialogue_posts == [], (
        f"the no-NPC journey must issue zero dialogue POSTs, "
        f"got {dialogue_posts}")
    _uh(results, "NO_NPC")


# ---------- 6. FAILED_IMAGE — the scene asset file blob rejects ----------

def test_failed_scene_image_is_handled_and_journey_continues(results):
    at_place = _f(results, "FAILED_IMAGE", "atPlace")
    at_scene = _f(results, "FAILED_IMAGE", "atScene")
    at_chat = _f(results, "FAILED_IMAGE", "atChat")
    at_tasks = _f(results, "FAILED_IMAGE", "atTasks")
    # The failed image fetch must not leave the scene in the ready state.
    assert at_place["sceneStatus"] != "ready", (
        f"a failed scene image must not report the scene ready, "
        f"got {at_place['sceneStatus']!r}")
    # The scene act succeeds (task 30702) and the Tasks screen shows it.
    assert "30702" in at_scene["taskRegistry"], (
        "the successful scene act must register task 30702")
    assert "30702" in at_tasks["taskRegistry"], (
        "the Tasks screen must show task 30702")
    # The failed visual request is NOT automatically retried: exactly one
    # /visual/scenes POST.
    visual_posts = [p for p in at_tasks["posts"] if p["url"] == "/visual/scenes"]
    assert len(visual_posts) == 1, (
        f"the failed visual request must not be auto-retried, "
        f"got {len(visual_posts)} /visual/scenes POSTs")
    # The journey continues into the chat stage (no crash).
    assert at_chat["hash"] == "#/chat"
    _uh(results, "FAILED_IMAGE")


# ---------- 7. BUSY — the MOVE is rejected 409-busy ----------

def test_busy_move_is_rejected_and_journey_continues(results):
    rejected = _f(results, "BUSY", "rejected")
    back = _f(results, "BUSY", "back")
    retried = _f(results, "BUSY", "retried")
    # The move POST was made but rejected: no task registered, player stays.
    assert rejected["postCount"] == 1
    assert "30701" not in rejected["taskRegistry"], (
        "a rejected (409-busy) move must not register task 30701")
    assert rejected["charLocId"] == 1, "the player must stay at the origin"
    assert rejected["taskSubmitFeedback"] == "Персонаж уже занят другим действием", (
        f"the busy caption must be set, got {rejected['taskSubmitFeedback']!r}")
    # The journey continues back to World.
    assert back["hash"] == "#/world"
    # The explicit retry (with a successful /actions response) succeeds:
    # task 30701 is registered, the player moves to location 2 after the
    # authoritative poll, and the total MOVE POST attempts == 2.
    assert "30701" in retried["taskRegistry"], (
        "the retried move must register task 30701")
    assert retried["charLocId"] == 2, (
        f"the retried move must move the player to location 2, "
        f"got {retried['charLocId']!r}")
    move_posts = [p for p in retried["posts"] if p["url"] == "/actions"]
    assert len(move_posts) == 2, (
        f"the total MOVE POST attempts must be 2, got {len(move_posts)}")
    _uh(results, "BUSY")


# ---------- 8. OFFLINE — the MOVE network-fails ----------

def test_offline_move_fails_and_journey_continues(results):
    failed = _f(results, "OFFLINE", "failed")
    back = _f(results, "OFFLINE", "back")
    retried = _f(results, "OFFLINE", "retried")
    # The move POST was made but network-failed: no task, player stays.
    assert failed["postCount"] == 1
    assert "30701" not in failed["taskRegistry"], (
        "a network-failed move must not register task 30701")
    assert failed["charLocId"] == 1, "the player must stay at the origin"
    assert failed["taskSubmitFeedback"] == (
        "Не удалось подтвердить принятие запроса. Проверьте связь"), (
        f"the offline caption must be set, got {failed['taskSubmitFeedback']!r}")
    # The journey continues back to World, and the World visit issues NO
    # retry POST (the post count is still 1).
    assert back["hash"] == "#/world"
    assert back["postCount"] == 1, (
        f"the World visit must not issue a retry POST, got {back['postCount']}")
    # The explicit retry (with a successful /actions response) succeeds:
    # the total MOVE POST attempts == 2.
    move_posts = [p for p in retried["posts"] if p["url"] == "/actions"]
    assert len(move_posts) == 2, (
        f"the total MOVE POST attempts must be 2, got {len(move_posts)}")
    _uh(results, "OFFLINE")


# ---------- 9. AUTONOMOUS — the move is gated client-side (no POST) ----------

def test_autonomous_move_is_gated_with_no_post(results):
    gated = _f(results, "AUTONOMOUS", "gated")
    back = _f(results, "AUTONOMOUS", "back")
    retried = _f(results, "AUTONOMOUS", "retried")
    # The AUTONOMOUS gate refuses the move client-side: zero POSTs.
    assert gated["postCount"] == 0, (
        f"an AUTONOMOUS move must not POST, got {gated['postCount']}")
    assert "30701" not in gated["taskRegistry"]
    assert gated["charLocId"] == 1, "the player must stay at the origin"
    # The journey continues back to World.
    assert back["hash"] == "#/world"
    # After the DIRECT transition (the authoritative read reports DIRECT),
    # the same destination click submits EXACTLY ONE MOVE POST.
    move_posts = [p for p in retried["posts"] if p["url"] == "/actions"]
    assert len(move_posts) == 1, (
        f"the DIRECT move must submit exactly one MOVE POST, "
        f"got {len(move_posts)}")
    _uh(results, "AUTONOMOUS")


# ---------- 10. CRITICAL — the scene act returns ok:false; the draft is kept ----------

def test_critical_scene_act_keeps_draft_and_registers_no_task(results):
    at_place = _f(results, "CRITICAL", "atPlace")
    at_chat = _f(results, "CRITICAL", "atChat")
    # The ok:false rejection registers no task and keeps the typed draft.
    assert "30702" not in at_place["taskRegistry"], (
        "an ok:false scene act must not register task 30702")
    assert at_place["sceneActDraft"] == "Поговорить с Фаром", (
        f"an ok:false scene act must keep the typed draft, "
        f"got {at_place['sceneActDraft']!r}")
    assert at_place["sceneAct"] is not None
    assert at_place["sceneAct"]["ok"] is False
    # The journey continues into the chat stage (no crash).
    assert at_chat["hash"] == "#/chat"
    _uh(results, "CRITICAL")


# ---------- 11. VISUAL_UNAVAILABLE — 503 /visual/scenes; dialogue + reads work ----------

def test_visual_unavailable_still_allows_dialogue_and_task_reads(results):
    at_place = _f(results, "VISUAL_UNAVAILABLE", "atPlace")
    at_chat = _f(results, "VISUAL_UNAVAILABLE", "atChat")
    at_tasks = _f(results, "VISUAL_UNAVAILABLE", "atTasks")
    # The 503 /visual/scenes must not present a ready image.
    assert at_place["sceneStatus"] != "ready", (
        f"a 503 /visual/scenes must not report the scene ready, "
        f"got {at_place['sceneStatus']!r}")
    # The dialogue still works (start + message) and targets NPC 2.
    assert at_chat["hash"] == "#/chat"
    assert at_chat["chatNpcSelectValue"] == "2", (
        f"the dialogue must target NPC 2, got {at_chat['chatNpcSelectValue']!r}")
    # The task reads still succeed.
    assert at_tasks["hash"] == "#/tasks"
    # Exactly one (failed) /visual/scenes POST, no automatic retry.
    visual_posts = [p for p in at_tasks["posts"] if p["url"] == "/visual/scenes"]
    assert len(visual_posts) == 1, (
        f"exactly one (failed) /visual/scenes POST, no auto-retry, "
        f"got {len(visual_posts)}")
    _uh(results, "VISUAL_UNAVAILABLE")


# ---------- 12. FAILED_DIALOGUE — failed message draft survives roundtrip ----------

def test_failed_dialogue_message_survives_tasks_roundtrip(results):
    failed = _f(results, "FAILED_DIALOGUE", "failed")
    roundtrip = _f(results, "FAILED_DIALOGUE", "roundtrip")
    retried = _f(results, "FAILED_DIALOGUE", "retried")
    # The network-failed message send keeps the exact typed draft.
    assert failed["chatDraft"] == "Привет, Фар", (
        f"the failed message send must keep the typed draft, "
        f"got {failed['chatDraft']!r}")
    # The draft survives the Tasks -> Chat roundtrip (same NPC 2).
    assert roundtrip["chatDraft"] == "Привет, Фар", (
        f"the draft must survive the Tasks -> Chat roundtrip, "
        f"got {roundtrip['chatDraft']!r}")
    assert roundtrip["chatNpcSelectValue"] == "2", (
        f"the roundtrip must return to the same NPC 2, "
        f"got {roundtrip['chatNpcSelectValue']!r}")
    # Navigation (Tasks -> Chat) fires NO message POST: at the roundtrip there
    # is exactly one message POST (the failed one).
    roundtrip_msg_posts = [p for p in roundtrip["posts"]
                           if p["url"] == "/dialogue/30703/message"]
    assert len(roundtrip_msg_posts) == 1, (
        f"navigation must not fire a message POST, got "
        f"{len(roundtrip_msg_posts)} message POSTs at the roundtrip")
    # The explicit retry (with a successful response) sends the ORIGINAL
    # content; the total message POST attempts == 2.
    msg_posts = [p for p in retried["posts"]
                 if p["url"] == "/dialogue/30703/message"]
    assert len(msg_posts) == 2, (
        f"the total message POST attempts must be 2, got {len(msg_posts)}")
    assert msg_posts[-1]["body"] == {"content": "Привет, Фар"}, (
        f"the retry must send the original content, "
        f"got {msg_posts[-1]['body']!r}")
    _uh(results, "FAILED_DIALOGUE")


# ---------- 13. OLD_PLACE_IMAGE — stale loc-1 image cannot replace arrival ----------

def test_old_place_image_cannot_replace_arrival_context(results):
    at_place = _f(results, "OLD_PLACE_IMAGE", "atPlace")
    at_chat = _f(results, "OLD_PLACE_IMAGE", "atChat")
    # The current place remains location 2 (the arrival context).
    assert at_place["hash"] == "#/location/2"
    assert at_place["placeBadgeText"] == "Вы здесь", (
        f"the current place must show the arrival badge, "
        f"got {at_place['placeBadgeText']!r}")
    # The stale location-1 image is NOT installed into the place stage.
    assert at_place["placeStageHasImage"] is False, (
        "the stale location-1 image must not be installed into the place stage")
    # The stale response WAS delivered to the app after release — the guard
    # assertion above is therefore non-trivial.
    assert at_place["staleDelivered"] is True, (
        "the released stale response must actually reach the app")
    # Dialogue still targets NPC 2.
    assert at_chat["chatNpcSelectValue"] == "2", (
        f"dialogue must still target NPC 2, "
        f"got {at_chat['chatNpcSelectValue']!r}")
    _uh(results, "OLD_PLACE_IMAGE")


# ---------- 14. REFUSED_SCENE_RETRY — draft survives nav + explicit retry ----------

def test_refused_scene_draft_survives_navigation_and_explicit_retry(results):
    refused = _f(results, "REFUSED_SCENE_RETRY", "refused")
    roundtrip = _f(results, "REFUSED_SCENE_RETRY", "roundtrip")
    retried = _f(results, "REFUSED_SCENE_RETRY", "retried")
    # After the refused act: the draft equals the original submitted text,
    # and no 30702 is registered.
    assert refused["sceneActDraft"] == "Поговорить с Фаром", (
        f"the refused scene act must keep the original draft, "
        f"got {refused['sceneActDraft']!r}")
    assert "30702" not in refused["taskRegistry"], (
        "a refused (ok:false) scene act must not register task 30702")
    # The draft survives the navigation roundtrip (World -> Place 2): it
    # STILL equals the original text, and 30702 is still absent.
    assert roundtrip["sceneActDraft"] == "Поговорить с Фаром", (
        f"the draft must survive the navigation roundtrip, "
        f"got {roundtrip['sceneActDraft']!r}")
    assert "30702" not in roundtrip["taskRegistry"], (
        "30702 must be absent before the successful retry")
    # The explicit retry with CORRECTED text succeeds: 30702 is registered
    # and the draft is cleared.
    assert "30702" in retried["taskRegistry"], (
        "the successful retry must register task 30702")
    assert retried["sceneActDraft"] is None, (
        f"the successful retry must clear the draft, "
        f"got {retried['sceneActDraft']!r}")
    # /scene/act POST total == 2; first body text == original, second body
    # text == corrected.
    act_posts = [p for p in retried["posts"] if p["url"] == "/scene/act"]
    assert len(act_posts) == 2, (
        f"the total /scene/act POSTs must be 2, got {len(act_posts)}")
    assert act_posts[0]["body"] == {"text": "Поговорить с Фаром"}, (
        f"the first /scene/act body must be the original text, "
        f"got {act_posts[0]['body']!r}")
    assert act_posts[1]["body"] == {"text": "Поговорить с Фаром у костра"}, (
        f"the second /scene/act body must be the corrected text, "
        f"got {act_posts[1]['body']!r}")
    # /visual/scenes POST total == 1 (no regeneration on the roundtrip).
    visual_posts = [p for p in retried["posts"] if p["url"] == "/visual/scenes"]
    assert len(visual_posts) == 1, (
        f"the /visual/scenes POST total must be 1 (no regeneration), "
        f"got {len(visual_posts)}")
    _uh(results, "REFUSED_SCENE_RETRY")
