"""#307 — playable-journey E2E: the journey contract.

Behavioral tests for the canonical six-stage playable journey in the REAL
production ``backend/app/web/app.js`` (boot included), run in the shared
``journey_harness`` vm context. The canonical journey (deterministic
identities: player=1, start location=1, destination=2, destination NPC=2,
MOVE task=30701, scene task=30702, dialogue session=30703, feed event=30704,
DIRECT mode):

  1. World (loc 1, empty tasks)
  2. select POI 2 + preview ``#/location/2`` + return World
  3. explicit MOVE (accepted → active → completed; authoritative read reports
     loc 2)
  4. enter current Place 2, inspect scene, submit a task-producing scene act
  5. Chat: select NPC 2, start the conversation, send one message
  6. Tasks shows 30702, return World at loc 2

Every assertion is on an observable fact (the fetch log, the rendered DOM, the
``S`` / ``gameUiContext`` / task-presentation state). The harness does NOT mock
any journey logic.
"""

import json

import pytest

from tests.m307.journey_harness import (
    CHAR1_AT_2,
    FEED_EVENT_30704,
    TASK_30701,
    TASK_30702,
    _journey_responses,
    _tasks_resp,
    facts,
    run_scenarios,
    unhandled,
)

# Fixtures embedded into the driver strings (the driver is a JS string; the
# authoritative-read updates are applied via __setResponse with inline JSON).
CHAR1_AT_2_JSON = json.dumps(CHAR1_AT_2)
TASKS_30701_DONE_JSON = json.dumps(_tasks_resp(recent=[TASK_30701]))
TASKS_30702_ACTIVE_JSON = json.dumps(_tasks_resp(active=[TASK_30702]))
FEED_EVENT_30704_JSON = json.dumps(FEED_EVENT_30704)


def _full_journey_driver():
    """The canonical six-stage journey, capturing facts at every stage."""
    return (
        "await __settle(); "
        "const s1 = __facts(); "
        # Stage 2: select POI 2, preview the remote place, return to World.
        "__clickButton('Магазин'); await __settle(); "
        "const s2a = __facts(); "
        "__clickAnchor('Посмотреть место'); await __settle(); "
        "const s2b = __facts(); "
        "__clickButton('← Остров'); await __settle(); "
        "const s2c = __facts(); "
        # Stage 3: the explicit MOVE (the ONE move path).
        "__clickButton('Магазин'); await __settle(); "
        "__clickButton('Пойти сюда'); await __settle(); "
        "const s3a = __facts(); "
        # The server completes the move: the authoritative read reports loc 2.
        "__setResponse('/characters/1', {status:200, body: " + CHAR1_AT_2_JSON + "}); "
        "__setResponse('/characters/1/tasks', " + TASKS_30701_DONE_JSON + "); "
        "await __call('pollEvents'); await __settle(); "
        "const s3b = __facts(); "
        # Stage 4: enter the current Place 2, inspect the scene, submit a
        # task-producing scene act.
        "__setHash('#/location/2'); await __settle(); "
        "const s4a = __facts(); "
        "__stageButton(); await __settle(); "
        "const s4b = __facts(); "
        "__type('scene-act-input', 'Поговорить с Фаром'); "
        "__clickButton('Сделать'); await __settle(); "
        "const s4c = __facts(); "
        # Stage 5: Chat — select NPC 2, start the conversation, send one
        # message.
        "__clickButton('Поговорить'); await __settle(); "
        "const s5a = __facts(); "
        "__type('chat-msg-input', 'Привет, Фар'); "
        "__clickButton('→'); await __settle(); "
        "const s5b = __facts(); "
        # Stage 6: Tasks shows 30702, return to World at loc 2.
        "__setResponse('/characters/1/tasks', " + TASKS_30702_ACTIVE_JSON + "); "
        "__setHash('#/tasks'); await __settle(); "
        "const s6a = __facts(); "
        "__clickButton('Мир'); await __settle(); "
        "const s6b = __facts(); "
        "globalThis.__result = { s1, s2a, s2b, s2c, s3a, s3b, s4a, s4b, "
        "s4c, s5a, s5b, s6a, s6b };"
    )


SCENARIOS = [
    {
        "name": "SIX_STAGE_JOURNEY",
        "initialHash": "#/world",
        "responses": _journey_responses(),
        "driver": _full_journey_driver(),
    },
    {
        "name": "REMOTE_PREVIEW_NO_TRAVEL",
        "initialHash": "#/world",
        "responses": _journey_responses(),
        "driver": (
            "await __settle(); "
            "const boot = __facts(); "
            "__clickButton('Магазин'); await __settle(); "
            "const selected = __facts(); "
            "__clickAnchor('Посмотреть место'); await __settle(); "
            "const preview = __facts(); "
            "__clickButton('← Остров'); await __settle(); "
            "const back = __facts(); "
            "globalThis.__result = { boot, selected, preview, back };"
        ),
    },
    {
        "name": "MOVE_HANDOFF",
        "initialHash": "#/world",
        "responses": _journey_responses(),
        "driver": (
            "await __settle(); "
            "__clickButton('Магазин'); await __settle(); "
            "__clickButton('Пойти сюда'); await __settle(); "
            "const accepted = __facts(); "
            "__setResponse('/characters/1', {status:200, body: "
            + CHAR1_AT_2_JSON + "}); "
            "__setResponse('/characters/1/tasks', " + TASKS_30701_DONE_JSON + "); "
            "await __call('pollEvents'); await __settle(); "
            "const arrived = __facts(); "
            "globalThis.__result = { accepted, arrived };"
        ),
    },
    {
        "name": "SCENE_TASK_HANDOFF",
        "initialHash": "#/world",
        "responses": _journey_responses(),
        "driver": (
            "await __settle(); "
            # arrive at loc 2 (the authoritative read) so Place 2 is current
            "__setResponse('/characters/1', {status:200, body: "
            + CHAR1_AT_2_JSON + "}); "
            "__setResponse('/characters/1/tasks', " + TASKS_30701_DONE_JSON + "); "
            "await __call('pollEvents'); await __settle(); "
            "__setHash('#/location/2'); await __settle(); "
            "__stageButton(); await __settle(); "
            "__type('scene-act-input', 'Поговорить с Фаром'); "
            "__clickButton('Сделать'); await __settle(); "
            "const atPlace = __facts(); "
            "__clickButton('Поговорить'); await __settle(); "
            "__type('chat-msg-input', 'Привет, Фар'); "
            "__clickButton('→'); await __settle(); "
            "const atChat = __facts(); "
            "__setResponse('/characters/1/tasks', " + TASKS_30702_ACTIVE_JSON + "); "
            "__setHash('#/tasks'); await __settle(); "
            "const atTasks = __facts(); "
            "globalThis.__result = { atPlace, atChat, atTasks };"
        ),
    },
    {
        "name": "DESTINATION_ROSTER",
        "initialHash": "#/world",
        "responses": _journey_responses(),
        "driver": (
            "await __settle(); "
            # the origin roster (loc 1) before the move
            "__setHash('#/chat'); await __settle(); "
            "const originRoster = __facts(); "
            # arrive at loc 2
            "__setResponse('/characters/1', {status:200, body: "
            + CHAR1_AT_2_JSON + "}); "
            "__setResponse('/characters/1/tasks', " + TASKS_30701_DONE_JSON + "); "
            "await __call('pollEvents'); await __settle(); "
            # the destination roster (loc 2) after the move
            "__setHash('#/world'); await __settle(); "
            "__setHash('#/chat'); await __settle(); "
            "const destRoster = __facts(); "
            "globalThis.__result = { originRoster, destRoster };"
        ),
    },
    {
        "name": "FEED_EVENT_ONCE",
        "initialHash": "#/world",
        "responses": _journey_responses(),
        "driver": (
            "await __settle(); "
            "__wsOpen(); __wsMessage({type:'hello', cursor: 500}); "
            "await __settle(); "
            "__wsMessage({type:'events', events:[" + FEED_EVENT_30704_JSON + "]}); "
            "await __settle(); "
            "const w0 = __facts(); "
            "__setHash('#/location/1'); await __settle(); "
            "const p0 = __facts(); "
            "__setHash('#/chat'); await __settle(); "
            "const c0 = __facts(); "
            "__setHash('#/tasks'); await __settle(); "
            "const t0 = __facts(); "
            "__setHash('#/world'); await __settle(); "
            "const w1 = __facts(); "
            "globalThis.__result = { w0, p0, c0, t0, w1 };"
        ),
    },
]


@pytest.fixture(scope="module")
def results():
    return run_scenarios(SCENARIOS)


# ---------- 1. six_stage_journey_preserves_authoritative_identity ----------

def test_six_stage_journey_preserves_authoritative_identity(results):
    r = facts(results, "SIX_STAGE_JOURNEY")
    stages = [r[k] for k in (
        "s1", "s2a", "s2b", "s2c", "s3a", "s3b", "s4a", "s4b",
        "s4c", "s5a", "s5b", "s6a", "s6b")]
    # The authoritative identity is preserved at EVERY stage.
    for i, s in enumerate(stages):
        assert s["userId"] == 1, f"stage {i}: user identity lost, got {s['userId']}"
        assert s["charId"] == 1, f"stage {i}: character identity lost, got {s['charId']}"
        assert s["charControl"] == "DIRECT", (
            f"stage {i}: control mode changed, got {s['charControl']!r}")
    # Stage 1: World at the origin, empty tasks.
    assert r["s1"]["hash"] == "#/world"
    assert r["s1"]["hasWorldShell"] is True
    assert r["s1"]["charLocId"] == 1
    # Stage 2: the preview is a remote place; the player did NOT move.
    assert r["s2a"]["guc"]["selectedPoiId"] == 2
    assert r["s2a"]["postCount"] == 0, "selecting a POI must not POST"
    assert r["s2b"]["hash"] == "#/location/2"
    assert r["s2b"]["hasPlaceShell"] is True
    assert r["s2b"]["placeBadgeText"] == "Другое место"
    assert r["s2b"]["charLocId"] == 1, "the preview must not move the player"
    assert r["s2c"]["hash"] == "#/world"
    assert r["s2c"]["charLocId"] == 1
    # Stage 3: the MOVE is accepted (one POST), then the authoritative read
    # reports loc 2.
    assert r["s3a"]["postCount"] == 1
    assert r["s3a"]["posts"][0]["url"] == "/actions"
    assert r["s3a"]["posts"][0]["body"] == {
        "character_id": 1, "action_type": "MOVE", "params": {"location_id": 2}}
    assert "30701" in r["s3a"]["taskRegistry"], (
        "the MOVE acceptance must register task 30701")
    assert r["s3a"]["charLocId"] == 1, "the player has not arrived yet"
    assert r["s3b"]["charLocId"] == 2, (
        "the authoritative read must report the arrival at loc 2")
    assert r["s3b"]["taskRegistry"]["30701"]["serverStatus"] == "completed"
    # Stage 4: the current Place 2, the scene is ready, the scene act is
    # accepted (task 30702).
    assert r["s4a"]["hash"] == "#/location/2"
    assert r["s4a"]["placeBadgeText"] == "Вы здесь"
    assert r["s4b"]["sceneStatus"] == "ready"
    assert r["s4b"]["sceneLocId"] == 2
    assert r["s4c"]["postCount"] == 3, (
        f"MOVE + /visual/scenes + /scene/act = 3 POSTs, got {r['s4c']['postCount']}")
    assert "30702" in r["s4c"]["taskRegistry"], (
        "the scene act must register task 30702")
    # Stage 5: the chat with NPC 2, one message sent (session 30703).
    assert r["s5a"]["hash"] == "#/chat"
    assert r["s5a"]["hasChatShell"] is True
    assert r["s5a"]["chatNpcSelectValue"] == "2"
    assert r["s5b"]["postCount"] == 5, (
        f"the full journey is exactly 5 POSTs, got {r['s5b']['postCount']}")
    assert r["s5b"]["chatSessionId"] == 30703
    # Stage 6: Tasks shows 30702; the return lands on World at loc 2.
    assert r["s6a"]["hash"] == "#/tasks"
    assert r["s6a"]["hasTasksShell"] is True
    assert "Нет активных задач" not in (r["s6a"]["tasksActiveText"] or ""), (
        "the Tasks screen must show the active scene task 30702")
    assert r["s6b"]["hash"] == "#/world"
    assert r["s6b"]["hasWorldShell"] is True
    assert r["s6b"]["charLocId"] == 2
    uh = unhandled(results, "SIX_STAGE_JOURNEY")
    assert uh == [], f"no unhandled errors may escape: {uh}"


# ---------- 2. remote_preview_returns_to_world_without_committing_travel ----------

def test_remote_preview_returns_to_world_without_committing_travel(results):
    r = facts(results, "REMOTE_PREVIEW_NO_TRAVEL")
    assert r["boot"]["charLocId"] == 1
    assert r["selected"]["guc"]["selectedPoiId"] == 2
    assert r["selected"]["postCount"] == 0, "a POI selection must not POST"
    # The preview lands on the remote place without moving the player.
    assert r["preview"]["hash"] == "#/location/2"
    assert r["preview"]["hasPlaceShell"] is True
    assert r["preview"]["placeBadgeText"] == "Другое место"
    assert r["preview"]["charLocId"] == 1, "the preview must not move the player"
    assert r["preview"]["postCount"] == 0, "viewing a place is GET-only"
    # The return lands on World, still at the origin, with zero mutations.
    assert r["back"]["hash"] == "#/world"
    assert r["back"]["hasWorldShell"] is True
    assert r["back"]["charLocId"] == 1
    assert r["back"]["postCount"] == 0, (
        "the selection + preview + return prefix must commit ZERO mutations")
    uh = unhandled(results, "REMOTE_PREVIEW_NO_TRAVEL")
    assert uh == [], f"no unhandled errors may escape: {uh}"


# ---------- 3. move_acceptance_hands_off_to_tasks_before_arrival ----------

def test_move_acceptance_hands_off_to_tasks_before_arrival(results):
    r = facts(results, "MOVE_HANDOFF")
    accepted, arrived = r["accepted"], r["arrived"]
    # The acceptance: exactly one POST /actions with the MOVE body.
    assert accepted["postCount"] == 1
    assert accepted["posts"][0]["url"] == "/actions"
    assert accepted["posts"][0]["body"] == {
        "character_id": 1, "action_type": "MOVE", "params": {"location_id": 2}}
    # The acceptance hands off to the task registry BEFORE arrival.
    assert "30701" in accepted["taskRegistry"], (
        "the MOVE acceptance must register task 30701 before arrival")
    assert accepted["taskRegistry"]["30701"]["serverStatus"] in (
        "planned", "active"), (
        f"the accepted move is nonterminal before arrival, "
        f"got {accepted['taskRegistry']['30701']['serverStatus']!r}")
    assert accepted["currentTaskId"] == 30701
    assert accepted["toastText"] == "Запрос на перемещение принят"
    assert accepted["charLocId"] == 1, "the player has not arrived yet"
    # The authoritative read reports the arrival; the task completes.
    assert arrived["charLocId"] == 2
    assert arrived["taskRegistry"]["30701"]["serverStatus"] == "completed"
    uh = unhandled(results, "MOVE_HANDOFF")
    assert uh == [], f"no unhandled errors may escape: {uh}"


# ---------- 4. scene_task_hands_off_from_place_through_chat_to_tasks ----------

def test_scene_task_hands_off_from_place_through_chat_to_tasks(results):
    r = facts(results, "SCENE_TASK_HANDOFF")
    at_place, at_chat, at_tasks = r["atPlace"], r["atChat"], r["atTasks"]
    # The scene act is accepted at the current Place 2 (task 30702).
    assert at_place["hash"] == "#/location/2"
    assert at_place["placeBadgeText"] == "Вы здесь"
    assert at_place["sceneStatus"] == "ready"
    assert "30702" in at_place["taskRegistry"], (
        "the scene act must register task 30702")
    assert at_place["sceneActDraft"] is None, (
        "a successful scene act clears the draft")
    # The handoff continues through the chat (NPC 2, session 30703).
    assert at_chat["hash"] == "#/chat"
    assert at_chat["chatNpcSelectValue"] == "2"
    assert at_chat["chatSessionId"] == 30703
    # ...and lands on the Tasks screen showing 30702.
    assert at_tasks["hash"] == "#/tasks"
    assert at_tasks["hasTasksShell"] is True
    assert "Нет активных задач" not in (at_tasks["tasksActiveText"] or ""), (
        "the Tasks screen must show the active scene task 30702")
    assert "30702" in at_tasks["taskRegistry"]
    uh = unhandled(results, "SCENE_TASK_HANDOFF")
    assert uh == [], f"no unhandled errors may escape: {uh}"


# ---------- 5. destination_roster_replaces_origin_roster_after_arrival ----------

def test_destination_roster_replaces_origin_roster_after_arrival(results):
    r = facts(results, "DESTINATION_ROSTER")
    origin, dest = r["originRoster"], r["destRoster"]
    # Before the move, the chat roster is the origin's NPC (Иван, id 101).
    assert origin["hash"] == "#/chat"
    assert origin["chatNpcSelectValue"] == "101", (
        f"the origin roster selects the origin NPC, got {origin['chatNpcSelectValue']!r}")
    # After the move, the chat roster is the destination's NPC (Фар, id 2).
    assert dest["hash"] == "#/chat"
    assert dest["chatNpcSelectValue"] == "2", (
        f"the destination roster replaces the origin roster, "
        f"got {dest['chatNpcSelectValue']!r}")
    assert dest["charLocId"] == 2
    uh = unhandled(results, "DESTINATION_ROSTER")
    assert uh == [], f"no unhandled errors may escape: {uh}"


# ---------- 6. feed_event_survives_journey_route_chain_once ----------

def test_feed_event_survives_journey_route_chain_once(results):
    r = facts(results, "FEED_EVENT_ONCE")
    w0, p0, c0, t0, w1 = r["w0"], r["p0"], r["c0"], r["t0"], r["w1"]
    # The live event is delivered once on the World view.
    assert w0["feedIds"] == [30704], (
        f"the feed event 30704 is delivered once, got {w0['feedIds']}")
    assert w0["feedRows"] == 1
    # It survives the route chain (World → Place → Chat → Tasks → World)
    # without duplication.
    assert p0["feedIds"] == [30704]
    assert c0["feedIds"] == [30704]
    assert t0["feedIds"] == [30704]
    assert w1["feedIds"] == [30704], (
        f"the feed event must not duplicate across the route chain, "
        f"got {w1['feedIds']}")
    assert w1["feedRows"] == 1, (
        f"the World feed shows the event exactly once, got {w1['feedRows']} rows")
    uh = unhandled(results, "FEED_EVENT_ONCE")
    assert uh == [], f"no unhandled errors may escape: {uh}"
