"""#307 — playable-journey E2E: the network contract.

Behavioral tests for the canonical six-stage playable journey in the REAL
production ``backend/app/web/app.js`` (boot included), run in the shared
``journey_harness`` vm context. The network contract pins the MUTATION surface
of the canonical journey (deterministic identities: player="1", start
location=1, destination=2, destination NPC="2", MOVE task=30701, scene
task=30702, dialogue session="30703", DIRECT mode — character/session ids are
STRINGS, task/location ids are INTEGERS):

  * exactly FIVE POSTs, in this order, with these exact bodies:
      1. ``/actions``                ``{character_id:"1", action_type:"MOVE",
                                      params:{location_id:2}}``
      2. ``/visual/scenes``          ``{location_id:2}``
      3. ``/scene/act``              ``{text:"Поговорить с Фаром"}``
      4. ``/dialogue/start``         ``{npc_id:"2"}``
      5. ``/dialogue/30703/message`` ``{content:"Привет, Фар"}``
  * no other non-GET/HEAD request (no PUT/DELETE/PATCH/…);
  * the contract deliberately does NOT pin an exact GET total — the journey is
    read-heavy and the GET count is implementation detail, so the tests assert
    that reads occur (``methods.GET > 0``) without over-constraining the total.

Every assertion is on an observable fact (the fetch census collected by the
harness). The harness does NOT mock any journey logic.
"""

import json

import pytest

from tests.m307.journey_harness import (
    APP_JS,
    CHAR1_AT_2,
    TASK_30701,
    _journey_responses,
    _tasks_resp,
    facts,
    run_scenarios,
    unhandled,
)

# Fixtures embedded into the driver string (the authoritative-read updates are
# applied via __setResponse with inline JSON).
CHAR1_AT_2_JSON = json.dumps(CHAR1_AT_2)
TASKS_30701_DONE_JSON = json.dumps(_tasks_resp(recent=[TASK_30701]))

# The canonical journey's five mutations, in order, with their exact bodies.
# Character/session ids are STRINGS (production shape); task/location ids are
# integers.
EXPECTED_POSTS = [
    ("/actions", {"character_id": "1", "action_type": "MOVE",
                  "params": {"location_id": 2}}),
    ("/visual/scenes", {"location_id": 2}),
    ("/scene/act", {"text": "Поговорить с Фаром"}),
    ("/dialogue/start", {"npc_id": "2"}),
    ("/dialogue/30703/message", {"content": "Привет, Фар"}),
]


def _canonical_journey_driver():
    """Run the full canonical journey; capture the final (cumulative) facts."""
    return (
        "await __settle(); "
        # Stage 2: select POI 2, preview the remote place, return to World.
        "__clickButton('Магазин'); await __settle(); "
        "__clickAnchor('Посмотреть место'); await __settle(); "
        "__clickButton('← Остров'); await __settle(); "
        # Stage 3: the explicit MOVE (the ONE move path).
        "__clickButton('Магазин'); await __settle(); "
        "__clickButton('Пойти сюда'); await __settle(); "
        # The server completes the move: the authoritative read reports loc 2.
        "__setResponse('/characters/1', {status:200, body: "
        + CHAR1_AT_2_JSON + "}); "
        "__setResponse('/characters/1/tasks', " + TASKS_30701_DONE_JSON + "); "
        "await __call('pollEvents'); await __settle(); "
        # Stage 4: enter the current Place 2, inspect the scene, submit a
        # task-producing scene act.
        "__setHash('#/location/2'); await __settle(); "
        "__stageButton(); await __settle(); "
        "__type('scene-act-input', 'Поговорить с Фаром'); "
        "__clickButton('Сделать'); await __settle(); "
        # Stage 5: Chat — select NPC 2, start the conversation, send one
        # message.
        "__clickButton('Поговорить'); await __settle(); "
        "__type('chat-msg-input', 'Привет, Фар'); "
        "__clickButton('→'); await __settle(); "
        "const final = __facts(); "
        "globalThis.__result = { final };"
    )


SCENARIOS = [
    {
        "name": "CANONICAL_JOURNEY_NET",
        "initialHash": "#/world",
        "responses": _journey_responses(),
        "driver": _canonical_journey_driver(),
    },
    {
        # The navigation + selection prefix, BEFORE any explicit intent:
        # boot -> select POI 2 -> preview #/location/2 -> back World ->
        # Tasks -> World. No move, no scene act, no dialogue.
        "name": "ZERO_MUTATION_PREFIX",
        "initialHash": "#/world",
        "responses": _journey_responses(),
        "driver": (
            "await __settle(); "
            "__clickButton('Магазин'); await __settle(); "
            "__clickAnchor('Посмотреть место'); await __settle(); "
            "__clickButton('← Остров'); await __settle(); "
            "__setHash('#/tasks'); await __settle(); "
            "__setHash('#/world'); await __settle(); "
            "const final = __facts(); "
            "globalThis.__result = { final };"
        ),
    },
    {
        # The canonical journey through the scene act, then the later routes
        # (Chat -> Tasks -> World -> Place). The scene image must NOT be
        # regenerated on the later routes.
        "name": "SCENE_NO_REGENERATE",
        "initialHash": "#/world",
        "responses": _journey_responses(),
        "driver": (
            "await __settle(); "
            "__setResponse('/characters/1', {status:200, body: "
            + CHAR1_AT_2_JSON + "}); "
            "__setResponse('/characters/1/tasks', " + TASKS_30701_DONE_JSON + "); "
            "await __call('pollEvents'); await __settle(); "
            "__setHash('#/location/2'); await __settle(); "
            "__stageButton(); await __settle(); "
            "__type('scene-act-input', 'Поговорить с Фаром'); "
            "__clickButton('Сделать'); await __settle(); "
            "__clickButton('Поговорить'); await __settle(); "
            "__setHash('#/tasks'); await __settle(); "
            "__setHash('#/world'); await __settle(); "
            "__setHash('#/location/2'); await __settle(); "
            "const final = __facts(); "
            "globalThis.__result = { final };"
        ),
    },
    {
        # After BOTH accepted tasks (30701 MOVE + 30702 scene), traverse
        # Tasks -> World -> Place -> Tasks twice. The read-only revisits must
        # not replay the accepted intents.
        "name": "READ_ONLY_REVISITS",
        "initialHash": "#/world",
        "responses": _journey_responses(),
        "driver": (
            "await __settle(); "
            "__setResponse('/characters/1', {status:200, body: "
            + CHAR1_AT_2_JSON + "}); "
            "__setResponse('/characters/1/tasks', " + TASKS_30701_DONE_JSON + "); "
            "await __call('pollEvents'); await __settle(); "
            "__setHash('#/location/2'); await __settle(); "
            "__stageButton(); await __settle(); "
            "__type('scene-act-input', 'Поговорить с Фаром'); "
            "__clickButton('Сделать'); await __settle(); "
            "const before = __facts(); "
            "__setHash('#/tasks'); await __settle(); "
            "__setHash('#/world'); await __settle(); "
            "__setHash('#/location/2'); await __settle(); "
            "__setHash('#/tasks'); await __settle(); "
            "__setHash('#/world'); await __settle(); "
            "__setHash('#/location/2'); await __settle(); "
            "__setHash('#/tasks'); await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { before, after };"
        ),
    },
    {
        # The canonical route chain, then the app's logout path, then advance
        # the pending feed timers. The feed transport ownership must be
        # bounded (<= 1 open socket) and torn down on logout.
        "name": "TRANSPORT_OWNERSHIP",
        "initialHash": "#/world",
        "responses": _journey_responses(),
        "driver": (
            "await __settle(); "
            "__clickButton('Магазин'); await __settle(); "
            "__clickAnchor('Посмотреть место'); await __settle(); "
            "__clickButton('← Остров'); await __settle(); "
            "__clickButton('Магазин'); await __settle(); "
            "__clickButton('Пойти сюда'); await __settle(); "
            "__setResponse('/characters/1', {status:200, body: "
            + CHAR1_AT_2_JSON + "}); "
            "__setResponse('/characters/1/tasks', " + TASKS_30701_DONE_JSON + "); "
            "await __call('pollEvents'); await __settle(); "
            "__setHash('#/location/2'); await __settle(); "
            "__clickButton('Поговорить'); await __settle(); "
            "__setHash('#/tasks'); await __settle(); "
            "__setHash('#/world'); await __settle(); "
            "const midChain = __facts(); "
            "__setHash('#/profile'); await __settle(); "
            "__clickButton('Выйти'); await __settle(); "
            "const afterLogout = __facts(); "
            "__advance(10000); await __settle(); "
            "const afterAdvance = __facts(); "
            "globalThis.__result = { midChain, afterLogout, afterAdvance };"
        ),
    },
]


@pytest.fixture(scope="module")
def results():
    return run_scenarios(SCENARIOS)


def _final(results):
    return facts(results, "CANONICAL_JOURNEY_NET")["final"]


# ---------- 1. exactly five POSTs ----------

def test_canonical_journey_makes_exactly_five_posts(results):
    f = _final(results)
    assert f["postCount"] == 5, (
        f"the canonical journey is exactly 5 POSTs, got {f['postCount']}: "
        f"{[p['url'] for p in f['posts']]}")
    uh = unhandled(results, "CANONICAL_JOURNEY_NET")
    assert uh == [], f"no unhandled errors may escape: {uh}"


# ---------- 2. the five POST URLs in the exact canonical order ----------

def test_canonical_journey_post_urls_in_order(results):
    f = _final(results)
    urls = [p["url"] for p in f["posts"]]
    expected = [url for url, _ in EXPECTED_POSTS]
    assert urls == expected, (
        f"the five POSTs must fire in the canonical order, got {urls}")
    uh = unhandled(results, "CANONICAL_JOURNEY_NET")
    assert uh == [], f"no unhandled errors may escape: {uh}"


# ---------- 3. the MOVE /actions body is exact ----------

def test_move_post_body_is_exact(results):
    f = _final(results)
    move = next(p for p in f["posts"] if p["url"] == "/actions")
    assert move["body"] == {"character_id": "1", "action_type": "MOVE",
                            "params": {"location_id": 2}}, (
        f"the MOVE /actions body must be exact, got {move['body']!r}")
    uh = unhandled(results, "CANONICAL_JOURNEY_NET")
    assert uh == [], f"no unhandled errors may escape: {uh}"


# ---------- 4. the scene POST bodies are exact ----------

def test_scene_post_bodies_are_exact(results):
    f = _final(results)
    scenes = next(p for p in f["posts"] if p["url"] == "/visual/scenes")
    act = next(p for p in f["posts"] if p["url"] == "/scene/act")
    assert scenes["body"] == {"location_id": 2}, (
        f"the /visual/scenes body must be exact, got {scenes['body']!r}")
    assert act["body"] == {"text": "Поговорить с Фаром"}, (
        f"the /scene/act body must be exact, got {act['body']!r}")
    uh = unhandled(results, "CANONICAL_JOURNEY_NET")
    assert uh == [], f"no unhandled errors may escape: {uh}"


# ---------- 5. the dialogue POST bodies are exact ----------

def test_dialogue_post_bodies_are_exact(results):
    f = _final(results)
    start = next(p for p in f["posts"] if p["url"] == "/dialogue/start")
    msg = next(p for p in f["posts"] if p["url"] == "/dialogue/30703/message")
    assert start["body"] == {"npc_id": "2"}, (
        f"the /dialogue/start body must be exact, got {start['body']!r}")
    assert msg["body"] == {"content": "Привет, Фар"}, (
        f"the /dialogue/30703/message body must be exact, got {msg['body']!r}")
    uh = unhandled(results, "CANONICAL_JOURNEY_NET")
    assert uh == [], f"no unhandled errors may escape: {uh}"


# ---------- 6. no non-GET/HEAD beyond the five POSTs; reads unpinned ----------

def test_only_posts_are_mutations_and_reads_are_unpinned(results):
    f = _final(results)
    # The only non-GET/HEAD requests are the five POSTs — no PUT/DELETE/
    # PATCH or any other mutation verb.
    non_get_head = f["nonGetHead"]
    assert len(non_get_head) == 5, (
        f"exactly 5 non-GET/HEAD requests, got {len(non_get_head)}: "
        f"{non_get_head}")
    assert all(e["method"] == "POST" for e in non_get_head), (
        f"every non-GET/HEAD request must be a POST, got {non_get_head}")
    # The journey is read-heavy: GETs occur. The contract deliberately does
    # NOT pin an exact GET total (implementation detail) — only that reads
    # happen.
    assert f["methods"].get("GET", 0) > 0, (
        f"the journey must perform GET reads, got methods {f['methods']}")
    uh = unhandled(results, "CANONICAL_JOURNEY_NET")
    assert uh == [], f"no unhandled errors may escape: {uh}"


# ---------- 7. the navigation + selection prefix commits zero mutations ----------

def test_navigation_and_selection_prefix_has_zero_mutations(results):
    f = facts(results, "ZERO_MUTATION_PREFIX")["final"]
    # The whole navigation + selection prefix (select POI 2, preview, back,
    # Tasks, World) commits ZERO mutations — no POST/PUT/PATCH/DELETE across
    # ANY endpoint, not only /actions.
    assert f["postCount"] == 0, (
        f"the selection prefix must commit zero POSTs, got {f['postCount']}: "
        f"{[p['url'] for p in f['posts']]}")
    assert f["nonGetHead"] == [], (
        f"the selection prefix must commit zero non-GET/HEAD requests, "
        f"got {f['nonGetHead']}")
    # Reads are unpinned (the prefix is read-heavy; no exact GET total).
    assert f["methods"].get("GET", 0) > 0, (
        f"the prefix must perform GET reads, got methods {f['methods']}")
    uh = unhandled(results, "ZERO_MUTATION_PREFIX")
    assert uh == [], f"no unhandled errors may escape: {uh}"


# ---------- 8. the scene acceptance does not regenerate the image ----------

def test_scene_acceptance_does_not_regenerate_image_on_later_routes(results):
    f = facts(results, "SCENE_NO_REGENERATE")["final"]
    scenes = [p for p in f["posts"] if p["url"] == "/visual/scenes"]
    acts = [p for p in f["posts"] if p["url"] == "/scene/act"]
    assert len(scenes) == 1, (
        f"the scene image is generated exactly once, got {len(scenes)} "
        f"/visual/scenes POSTs")
    assert len(acts) == 1, (
        f"the scene act is submitted exactly once, got {len(acts)} "
        f"/scene/act POSTs")
    uh = unhandled(results, "SCENE_NO_REGENERATE")
    assert uh == [], f"no unhandled errors may escape: {uh}"


# ---------- 9. read-only task revisits do not replay accepted intents ----------

def test_read_only_task_revisits_do_not_replay_accepted_intents(results):
    r = facts(results, "READ_ONLY_REVISITS")
    before, after = r["before"], r["after"]
    # The POST ledger is byte-for-byte identical across the read-only revisits
    # (same count, same urls, same bodies).
    assert before["postCount"] == after["postCount"], (
        f"the revisits must not add POSTs, before {before['postCount']} "
        f"after {after['postCount']}")
    assert before["posts"] == after["posts"], (
        f"the POST ledger must be byte-for-byte identical across the "
        f"revisits, before {before['posts']} after {after['posts']}")
    # The task-detail reads reference only the accepted task ids (30701/30702),
    # never an invented one.
    detail_gets = after["actionsDetailGets"]
    assert detail_gets, "the accepted tasks must have detail reads"
    for url in detail_gets:
        assert url in ("/actions/30701", "/actions/30702"), (
            f"task-detail reads must reference only 30701/30702, got {url}")
    uh = unhandled(results, "READ_ONLY_REVISITS")
    assert uh == [], f"no unhandled errors may escape: {uh}"


# ---------- 10. the feed transport ownership is bounded until logout ----------

def test_journey_transport_ownership_is_bounded_until_logout(results):
    r = facts(results, "TRANSPORT_OWNERSHIP")
    mid, after_logout, after_advance = (
        r["midChain"], r["afterLogout"], r["afterAdvance"])
    # The bound is meaningful only if the feed actually opened a socket at
    # least once during the chain (otherwise <=1 is vacuous).
    assert mid["wsConnects"] >= 1, (
        f"the feed must open at least one socket during the chain, "
        f"got connects={mid['wsConnects']}")
    # Across the canonical route chain, at most ONE feed socket is open at a
    # time (the feed controller is idempotent: 1-WS).
    assert mid["wsMaxConcurrent"] <= 1, (
        f"at most 1 concurrently-open feed socket, got "
        f"{mid['wsMaxConcurrent']}")
    # After logout, zero open sockets (the feed controller is torn down).
    assert after_logout["wsOpenNow"] == 0, (
        f"logout must leave zero open sockets, got {after_logout['wsOpenNow']}")
    # Advancing the pending feed timers must not create a new socket.
    assert after_advance["wsConnects"] == after_logout["wsConnects"], (
        f"no new socket after advancing timers, connects "
        f"{after_logout['wsConnects']} -> {after_advance['wsConnects']}")
    assert after_advance["wsOpenNow"] == 0, (
        f"still zero open sockets after advancing timers, got "
        f"{after_advance['wsOpenNow']}")
    uh = unhandled(results, "TRANSPORT_OWNERSHIP")
    assert uh == [], f"no unhandled errors may escape: {uh}"


# ---------- 11. the app source has no hidden geolocation entrypoints ----------

def test_app_source_has_no_hidden_geolocation_entrypoints():
    # Source-only (no vm): the app must not expose any hidden geolocation
    # entrypoint. This pin is NEVER xfailed.
    with open(APP_JS, encoding="utf-8") as fh:
        src = fh.read()
    for token in ("navigator.geolocation", "getCurrentPosition",
                  "watchPosition", "permissions.query"):
        assert token not in src, (
            f"the app source must not contain a hidden geolocation "
            f"entrypoint, found {token!r}")
