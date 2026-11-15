# #307 — playable-journey acceptance matrix

Per-test / per-case evidence + status. Gap-row format:

`ID | requirement | layer | reproduction | expected | observed | status | issue URL | evidence | closure condition`

Status legend: **pass** (green), **fail** (red, genuine defect), **xfail**
(strict, pending issue), **unverified** (manual/browser, not run by CI).

## CI pytest truth (vm harness) — 31 tests

Design provenance: **original** = present in the first delivery (22 tests);
**extension** = added/extended by the #307 correction (8 new test functions +
6 extended assertions); **design-original** = a test prescribed by the binding
design that was omitted from the first delivery and the correction, now added
(FM-14). The 6 extended tests keep every original assertion and only ADD the
correction's harder contract.

Peer-review hardening (no status changes): the transport-ownership test now
asserts a ≥1-connect precondition (the ≤1 bound is non-vacuous); the
stale-image test asserts the released response was actually delivered to the
app (non-trivial guard); the browser companion reports probe-mode
server-driven checks as SKIPPED (never as passes).

| ID | requirement | layer | reproduction | expected | observed | status | issue URL | evidence | closure condition |
|---|---|---|---|---|---|---|---|---|---|
| JC-1 | identity preserved at every stage | vm-harness | `test_six_stage_journey_preserves_authoritative_identity` | userId=1, charId=1, DIRECT at all 13 stage facts | all 13 stages report userId=1, charId=1, DIRECT | pass | — | `pytest tests/m307/test_journey_contract.py::test_six_stage_journey_preserves_authoritative_identity` | n/a (green) |
| JC-2 | remote preview commits no travel | vm-harness | `test_remote_preview_returns_to_world_without_committing_travel` | preview `#/location/2` badge «Другое место», charLocId stays 1, 0 POSTs, return to World | badge «Другое место», charLocId=1, postCount=0, back `#/world` | pass | — | `pytest tests/m307/test_journey_contract.py::test_remote_preview_returns_to_world_without_committing_travel` | n/a (green) |
| JC-3 | MOVE handoff to tasks before arrival | vm-harness | `test_move_acceptance_hands_off_to_tasks_before_arrival` | 1 POST `/actions` exact body, 30701 registered nonterminal, currentTaskId=30701, toast, then authoritative read loc 2 + completed | all observed | pass | — | `pytest tests/m307/test_journey_contract.py::test_move_acceptance_hands_off_to_tasks_before_arrival` | n/a (green) |
| JC-4 | scene-task handoff place→chat→tasks | vm-harness | `test_scene_task_hands_off_from_place_through_chat_to_tasks` | scene ready, 30702 registered, draft cleared, chat NPC 2 session 30703, Tasks shows 30702 | all observed | pass | — | `pytest tests/m307/test_journey_contract.py::test_scene_task_hands_off_from_place_through_chat_to_tasks` | n/a (green) |
| JC-5 | destination roster replaces origin | vm-harness | `test_destination_roster_replaces_origin_roster_after_arrival` | origin chat NPC 101, destination chat NPC 2 after arrival | origin=101, dest=2, charLocId=2 | pass | — | `pytest tests/m307/test_journey_contract.py::test_destination_roster_replaces_origin_roster_after_arrival` | n/a (green) |
| JC-6 | feed event survives route chain once | vm-harness | `test_feed_event_survives_journey_route_chain_once` | feedIds=[30704] at World/Place/Chat/Tasks/World, 1 row, no duplication | feedIds=[30704] at all 5, feedRows=1 | pass | — | `pytest tests/m307/test_journey_contract.py::test_feed_event_survives_journey_route_chain_once` | n/a (green) |
| NC-1 | exactly 5 POSTs | vm-harness | `test_canonical_journey_makes_exactly_five_posts` | postCount=5 | postCount=5 | pass | — | `pytest tests/m307/test_network_contract.py::test_canonical_journey_makes_exactly_five_posts` | n/a (green) |
| NC-2 | 5 POSTs in canonical order | vm-harness | `test_canonical_journey_post_urls_in_order` | `/actions`,`/visual/scenes`,`/scene/act`,`/dialogue/start`,`/dialogue/30703/message` | exact order observed | pass | — | `pytest tests/m307/test_network_contract.py::test_canonical_journey_post_urls_in_order` | n/a (green) |
| NC-3 | MOVE body exact | vm-harness | `test_move_post_body_is_exact` | `{character_id:1,action_type:"MOVE",params:{location_id:2}}` | exact body observed | pass | — | `pytest tests/m307/test_network_contract.py::test_move_post_body_is_exact` | n/a (green) |
| NC-4 | scene POST bodies exact | vm-harness | `test_scene_post_bodies_are_exact` | `/visual/scenes` `{location_id:2}`, `/scene/act` `{text:"Поговорить с Фаром"}` | exact bodies observed | pass | — | `pytest tests/m307/test_network_contract.py::test_scene_post_bodies_are_exact` | n/a (green) |
| NC-5 | dialogue POST bodies exact | vm-harness | `test_dialogue_post_bodies_are_exact` | `/dialogue/start` `{npc_id:2}`, `/dialogue/30703/message` `{content:"Привет, Фар"}` | exact bodies observed | pass | — | `pytest tests/m307/test_network_contract.py::test_dialogue_post_bodies_are_exact` | n/a (green) |
| NC-6 | no non-GET/HEAD beyond 5 POSTs; reads unpinned | vm-harness | `test_only_posts_are_mutations_and_reads_are_unpinned` | 5 non-GET/HEAD all POST, GET>0 (no exact GET total) | 5 POST, GET>0 | pass | — | `pytest tests/m307/test_network_contract.py::test_only_posts_are_mutations_and_reads_are_unpinned` | n/a (green) |
| NC-7 | **extension** — navigation+selection prefix commits zero mutations | vm-harness | `test_navigation_and_selection_prefix_has_zero_mutations` | boot→select POI 2→preview→back World→Tasks→World: 0 POST/PUT/PATCH/DELETE across ALL endpoints, reads unpinned | postCount=0, nonGetHead=[], GET>0 | pass | — | `pytest tests/m307/test_network_contract.py::test_navigation_and_selection_prefix_has_zero_mutations` | n/a (green) |
| NC-8 | **extension** — scene acceptance does not regenerate image on later routes | vm-harness | `test_scene_acceptance_does_not_regenerate_image_on_later_routes` | journey through scene act, then Chat→Tasks→World→Place: /visual/scenes total==1 AND /scene/act total==1 | 1 /visual/scenes, 1 /scene/act | pass | — | `pytest tests/m307/test_network_contract.py::test_scene_acceptance_does_not_regenerate_image_on_later_routes` | n/a (green) |
| NC-9 | **extension** — read-only task revisits do not replay accepted intents | vm-harness | `test_read_only_task_revisits_do_not_replay_accepted_intents` | after both accepted tasks, Tasks→World→Place→Tasks twice: POST ledger byte-for-byte identical; GET /actions/… reference only 30701/30702 | ledger identical, detail GETs ⊆ {30701,30702} | pass | — | `pytest tests/m307/test_network_contract.py::test_read_only_task_revisits_do_not_replay_accepted_intents` | n/a (green) |
| NC-10 | **extension** — feed transport ownership bounded until logout | vm-harness | `test_journey_transport_ownership_is_bounded_until_logout` | route chain: max concurrent feed sockets ≤1; after logout 0 open; advancing timers creates no new socket | wsMaxConcurrent≤1, wsOpenNow=0 after logout, no new connect after advance | pass | — | `pytest tests/m307/test_network_contract.py::test_journey_transport_ownership_is_bounded_until_logout` | n/a (green) |
| NC-11 | **extension** — app source has no hidden geolocation entrypoints (NEVER xfail) | source-only | `test_app_source_has_no_hidden_geolocation_entrypoints` | "navigator.geolocation"/"getCurrentPosition"/"watchPosition"/"permissions.query" all ABSENT from app.js | all 4 tokens absent | pass | — | `pytest tests/m307/test_network_contract.py::test_app_source_has_no_hidden_geolocation_entrypoints` | n/a (green) |
| FM-1 | Normal: scene act succeeds, continues to chat | vm-harness | `test_normal_scene_act_succeeds_and_continues_to_chat` | scene ready, 30702 registered, draft cleared, chat NPC 2 | all observed | pass | — | `pytest tests/m307/test_failure_matrix.py::test_normal_scene_act_succeeds_and_continues_to_chat` | n/a (green) |
| FM-2 | NoTasks: empty states, continues to World; **extension** — full read-only chain commits zero POSTs | vm-harness | `test_no_tasks_shows_empty_states_and_continues` | «Нет активных задач» + «История пуста», back `#/world`; **ext:** World→remote Place→Chat→Tasks→World postCount==0, no fabricated 30701/30702 in task text | both empty states, back World, postCount=0, no fabricated ids | pass | — | `pytest tests/m307/test_failure_matrix.py::test_no_tasks_shows_empty_states_and_continues` | n/a (green) |
| FM-3 | Remote: move CTA, no scene-act bar | vm-harness | `test_remote_place_shows_move_cta_not_scene_act` | badge «Другое место», scene-act input absent, back World | observed | pass | — | `pytest tests/m307/test_failure_matrix.py::test_remote_place_shows_move_cta_not_scene_act` | n/a (green) |
| FM-4 | Current: scene-act bar present | vm-harness | `test_current_place_shows_scene_act_bar` | badge «Вы здесь», scene-act input present | observed | pass | — | `pytest tests/m307/test_failure_matrix.py::test_current_place_shows_scene_act_bar` | n/a (green) |
| FM-5 | NoNpc: empty nearby roster; **extension** — scene act succeeds + zero dialogue POSTs | vm-harness | `test_no_npc_destination_shows_empty_roster` | nearby empty, rows=[]; **ext:** scene act → 30702 in atScene+atTasks registries, zero /dialogue/* POSTs | empty roster, 30702 registered+shown, 0 dialogue POSTs | pass | — | `pytest tests/m307/test_failure_matrix.py::test_no_npc_destination_shows_empty_roster` | n/a (green) |
| FM-6 | FailedImage: handled, journey continues; **extension** — scene act succeeds + no visual auto-retry | vm-harness | `test_failed_scene_image_is_handled_and_journey_continues` | scene not ready, continues to chat, no unhandled; **ext:** scene act → 30702 in atScene+atTasks, /visual/scenes POST total==1 (no auto-retry) | sceneStatus≠ready, chat reached, 30702 registered+shown, 1 /visual/scenes | pass | — | `pytest tests/m307/test_failure_matrix.py::test_failed_scene_image_is_handled_and_journey_continues` | n/a (green) |
| FM-7 | Busy: MOVE 409 rejected, stays at origin; **extension** — retry succeeds | vm-harness | `test_busy_move_is_rejected_and_journey_continues` | 1 POST, no 30701, charLocId=1, caption «Персонаж уже занят другим действием», back World; **ext:** retry with successful /actions → 30701 registered, charLocId→2, total MOVE POSTs==2 | all observed, retry: 30701 registered, charLocId=2, 2 MOVE POSTs | pass | — | `pytest tests/m307/test_failure_matrix.py::test_busy_move_is_rejected_and_journey_continues` | n/a (green) |
| FM-8 | Offline: MOVE network-fail, stays at origin; **extension** — no replay + retry | vm-harness | `test_offline_move_fails_and_journey_continues` | 1 POST, no 30701, charLocId=1, caption «Не удалось подтвердить принятие запроса. Проверьте связь», back World; **ext:** World visit issues no retry POST (postCount still 1 at back), explicit retry → total MOVE POSTs==2 | all observed, back postCount=1, retry: 2 MOVE POSTs | pass | — | `pytest tests/m307/test_failure_matrix.py::test_offline_move_fails_and_journey_continues` | n/a (green) |
| FM-9 | Autonomous: move gated, no POST; **extension** — DIRECT transition submits exactly one | vm-harness | `test_autonomous_move_is_gated_with_no_post` | 0 POSTs, no 30701, charLocId=1, back World; **ext:** after gated click, __setResponse DIRECT + pollEvents → same destination click submits EXACTLY ONE MOVE POST | postCount=0, after DIRECT: 1 MOVE POST | pass | — | `pytest tests/m307/test_failure_matrix.py::test_autonomous_move_is_gated_with_no_post` | n/a (green) |
| FM-10 | Critical: scene act ok:false, draft kept, no task | vm-harness | `test_critical_scene_act_keeps_draft_and_registers_no_task` | no 30702, draft kept, sceneAct.ok=false, continues to chat | all observed | pass | — | `pytest tests/m307/test_failure_matrix.py::test_critical_scene_act_keeps_draft_and_registers_no_task` | n/a (green) |
| FM-11 | **extension** — 503 /visual/scenes still allows dialogue + task reads | vm-harness | `test_visual_unavailable_still_allows_dialogue_and_task_reads` | sceneStatus≠ready; chat start+message NPC 2; Tasks reads OK; exactly one (failed) /visual/scenes POST, no auto-retry | sceneStatus≠ready, chat NPC 2, tasks `#/tasks`, 1 /visual/scenes | pass | — | `pytest tests/m307/test_failure_matrix.py::test_visual_unavailable_still_allows_dialogue_and_task_reads` | n/a (green) |
| FM-12 | **extension** — failed dialogue message survives Tasks→Chat roundtrip | vm-harness | `test_failed_dialogue_message_survives_tasks_roundtrip` | message network-fail; exact draft retained through Tasks→Chat same NPC 2; retry sends ORIGINAL content; 2 message POST attempts; no message POST by navigation | draft «Привет, Фар» at failed+roundtrip, NPC 2, 1 msg POST at roundtrip / 2 at retried, last body {content:"Привет, Фар"} | pass | — | `pytest tests/m307/test_failure_matrix.py::test_failed_dialogue_message_survives_tasks_roundtrip` | n/a (green) |
| FM-13 | **extension** — old place image cannot replace arrival context | vm-harness | `test_old_place_image_cannot_replace_arrival_context` | hold loc-1 scene response; MOVE to 2; enter Place 2; release old; place remains 2, loc-1 image NOT installed (placeStageHasImage), dialogue targets NPC 2 | hash `#/location/2`, badge «Вы здесь», placeStageHasImage=false, chat NPC 2 | pass | — | `pytest tests/m307/test_failure_matrix.py::test_old_place_image_cannot_replace_arrival_context` | n/a (green) |
| FM-14 | **design-original** — refused scene draft survives navigation + explicit retry | vm-harness | `test_refused_scene_draft_survives_navigation_and_explicit_retry` | scene act ok:false → draft == original, no 30702; navigate World→Place 2 → draft STILL original; explicit retry with CORRECTED text succeeds → 30702 registered+shown; /scene/act POSTs==2 (1st original, 2nd corrected), /visual/scenes==1, 30702 absent before retry | draft «Поговорить с Фаром» at refused+roundtrip, 30702 only after retry, 2 /scene/act (original then corrected), 1 /visual/scenes | pass | — | `pytest tests/m307/test_failure_matrix.py::test_refused_scene_draft_survives_navigation_and_explicit_retry` | n/a (green) |

## Manual browser acceptance (companion) — 13 cases × {1440×900, 390×844}

These are NOT run by CI; they are the manual-acceptance truth for REAL rendered
geometry + viewport states. Status **unverified** until the parent runs the
companion via the browser MCP. The overflow invariant (no horizontal overflow)
is asserted on every screen of every case.

> **Run status (first fixture run):** the companion executed against local
> candidate bytes and deterministically blocked at `caseJourneySixSteps` stage 5
> (chat send) — see the correction below and GAP-1 / #338 for the causal chain.
> **Correction (#338, 2026-11-15):** the blocked send was caused by the
> suite's NUMERIC character/session fixtures interacting with real-Chromium
> DOMString `select.value` semantics, which exposed a defensive-normalization
> gap in app.js — it is NOT evidence that production emits numeric character
> ids (production character ids are strings). See the superseded-claim note
> in «Gaps / open items» below.
> **Run status (GAP-1 CLOSED, 2026-11-15):** the companion ran green in fixture
> mode against the candidate bytes — allPass=true, 26 case instances (13 ×
> {1440×900, 390×844}), 96/96 checks, 0 pageErrors, 0 SKIPPED, staticSha
> matching the worktree bytes. BR-1..BR-13 are verified below. The m313 companion
> (`tests/m313/composition.browser.js`, repaired in this change: 3 missing GET
> fixtures + caseZoom header check aligned to the shipped fixed-56px design)
> completed its fixture run green: allPass=true, 53/53 checks, 10/10 cases,
> 0 pageErrors, 0 unexpectedAPI, staticSha matching the candidate bytes.

| ID | requirement | layer | reproduction | expected | observed | status | issue URL | evidence | closure condition |
|---|---|---|---|---|---|---|---|---|---|
| BR-1 | six-stage journey geometry | browser companion | `caseJourneySixSteps` @1440+390 | shells present at each stage, overflow=false everywhere, m313 thresholds on their screens | shells present at each stage, overflow=false everywhere | pass | — | `report-m307-fixture8.json (96/96 checks)` | met (fixture run green) |
| BR-2 | Normal place stage band | browser companion | `caseStateNormal` @1440+390 | badge «Вы здесь», stage 32–40% @390 (≥32 floor @1440), overflow=false | badge «Вы здесь», stage 36% @390 (band [32,40]) | pass | — | `report-m307-fixture8.json (96/96 checks)` | met (fixture run green) |
| BR-3 | NoTasks empty states | browser companion | `caseStateNoTasks` @1440+390 | tasks shell, both empty states, overflow=false | tasks shell + both empty states | pass | — | `report-m307-fixture8.json (96/96 checks)` | met (fixture run green) |
| BR-4 | Remote place (no scene-act bar) | browser companion | `caseStateRemote` @1440+390 | badge «Другое место», scene-act input absent, overflow=false | badge «Другое место», scene-act input absent | pass | — | `report-m307-fixture8.json (96/96 checks)` | met (fixture run green) |
| BR-5 | Current place (scene-act bar) | browser companion | `caseStateCurrent` @1440+390 | badge «Вы здесь», scene-act input present, overflow=false | badge «Вы здесь», scene-act input present | pass | — | `report-m307-fixture8.json (96/96 checks)` | met (fixture run green) |
| BR-6 | NoNpc empty roster | browser companion | `caseStateNoNpc` @1440+390 | nearby empty, overflow=false | nearby empty | pass | — | `report-m307-fixture8.json (96/96 checks)` | met (fixture run green) |
| BR-7 | FailedImage handled | browser companion | `caseStateFailedImage` @1440+390 | scene-status set, overflow=false | scene-status set | pass | — | `report-m307-fixture8.json (96/96 checks)` | met (fixture run green) |
| BR-8 | Busy move rejected | browser companion | `caseStateBusy` @1440+390 | stays at origin, overflow=false | stays at origin (marker loc 1) | pass | — | `report-m307-fixture8.json (96/96 checks)` | met (fixture run green) |
| BR-9 | Offline move fails | browser companion | `caseStateOffline` @1440+390 | stays at origin, overflow=false | stays at origin (marker loc 1) | pass | — | `report-m307-fixture8.json (96/96 checks)` | met (fixture run green) |
| BR-10 | Autonomous move gated | browser companion | `caseStateAutonomous` @1440+390 | no POST /actions, overflow=false | no POST /actions (count unchanged) | pass | — | `report-m307-fixture8.json (96/96 checks)` | met (fixture run green) |
| BR-11 | Critical scene act (draft kept) | browser companion | `caseStateCritical` @1440+390 | draft kept, overflow=false | draft kept | pass | — | `report-m307-fixture8.json (96/96 checks)` | met (fixture run green) |
| BR-12 | keyboard journey (Tab traversal) | browser companion | `caseKeyboardJourney` @1440+390 | focus records on interactive elements, overflow=false | focus records on interactive elements | pass | — | `report-m307-fixture8.json (96/96 checks)` | met (fixture run green) |
| BR-13 | reduced-motion journey | browser companion | `caseReducedMotionJourney` @1440+390 | place shell + stage present, overflow=false | place shell + stage present | pass | — | `report-m307-fixture8.json (96/96 checks)` | met (fixture run green) |

## Gaps / open items

| ID | requirement | layer | reproduction | expected | observed | status | issue URL | evidence | closure condition |
|---|---|---|---|---|---|---|---|---|---|
| GAP-1 | browser companion executed — blocked by a real product bug | browser companion | parent ran `journey.browser.js` fixture mode (real Chromium, local candidate bytes) | allPass=true, 26 case instances, 0 pageErrors | allPass=true, 26 case instances, 96/96 checks, 0 pageErrors, 0 SKIPPED, staticSha match | pass (closed 2026-11-15) | #338 | `report-m307-fixture8.json` | met |
| GAP-2 | harness fidelity: fake DOM select does not reproduce DOMString coercion | vm harness | `journey_harness.py` `makeElement` stores `.value` as a plain property; real `HTMLSelectElement.value` is always a string | harness `select.value` semantics === real DOM (string on set/read) | harness SELECT .value coerces to string (test_chat_id_normalization.py test_shim_select_coercion + the numeric-roster tests) | pass (closed 2026-11-15) | #338 | same evidence chain as GAP-1 | met |

> **SUPERSEDED (causal claim in GAP-1/GAP-2 only — rows kept as history,
> correction 2026-11-15, #338):** the two rows above framed the companion
> block as «a REAL product bug» caught in production-shaped data. The
> corrected causal chain: the #307 fixtures carried NUMERIC character/session
> ids, which production never emits (production character ids are STRINGS,
> e.g. `npc_0001`/`plr_0001`; task/event/location ids are the integers). The
> numeric fixtures were incompatible with the real browser's DOMString
> `select.value` semantics, and THAT incompatibility exposed a real
> defensive-normalization gap in app.js (strict `includes`/`===` roster and
> relationship comparisons that silently required every producer to agree on
> one type). The numeric fixtures are NOT evidence about production's id
> types. The #338 fix: fixtures model the production types (character/session
> ids → strings; task/event/location ids stay integers), the vm harness
> coerces SELECT `.value` to string (select-only, no option-selection
> semantics), and app.js normalizes the six affected comparisons with
> `String(...)`. GAP closure is NOT claimed here: the companion re-run to
> allPass is still pending, and BR-1..BR-13 remain unverified.
> **Closure (2026-11-15):** both gaps are closed — the companion run is green and the harness coercion ships with the #338 fix.
