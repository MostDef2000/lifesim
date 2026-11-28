# 026 — Plan: Living Personality / Presence-driven initiative

**Scope #397:** документационный контракт. Код, schema, runtime, deployment не входят в этот PR. База: `4a85bc337755b4b3e96f33538f4119860eae8de0`; evidence статического чтения исходников — spec.md §5. [Конституция](../../.specify/memory/constitution.md) и [AGENTS](../../AGENTS.md) обязательны.

## Delivery sequence and boundaries

| Work order | Inputs | Output and gate |
|---|---|---|
| #398 personality | #397 | versioned personality record, trait projection, presets and draft interpretation; P01–P03 |
| #399 presence | #397 | lease/epoch, unified admission and completion gate, task compatibility, migration of legacy control; A01–A09, E01/E02, R01 |
| #400 motivation | #398; integration #399 | separate player goals and personal aspirations/wishes, TTL/dedup, accept path; M02/M03 |
| #401 planner | #398, #399, #400 | shared deterministic candidate ranking + bounded local LLM; M01/M04, A03/A07 |
| #385, #378, #363, #364, #402, #403 | approved semantics in #397; interfaces coordinated with #398–#400 | selected desktop/mobile designs, state matrices, interaction fixtures; U01/U02 design evidence |
| #365, #390 | respective design inputs | update prototypes and atlas without old mode selectors; no production claims |
| Separate bounded UI implementation issues | chosen designs + respective APIs | real creator/preset/profile, presence integration, thoughts, goals, return; browser evidence |
| #405 | backend and actual UI implementations | matrix across all acceptance IDs, full CI and migration/rollback evidence |
| #404 | #401 | activity catalogue expansion, separate later scope, not a gate for existing-action MVP |

#398 and #399 may plan in parallel. Each issue requires its own scoped implementation work order, RED-first tests, approval, fresh branch and PR. This umbrella plan does not authorize a monolithic implementation or permit workers to commit. The orchestrator owns lifecycle. Do not cut over old clients independently of compatible server/UI. 

## Architecture decisions and expected touchpoints (not patches)

- Model/API #398: inspect `db/models.py`, `characters/player.py`, `characters/creator.py`, `api/creator_routes.py`, `api/app.py`. Existing character traits/profile and creator draft semantics remain. Choose exact schema and migrations in bounded plan; one canonical personality record, derived legacy traits.
- #399: inventory all task writers and mutations via repo search, including lifecycle/activate/IDLE, `ai/worker.py` direct STARTED write, build, market/wear/dialogue, external tasks, system/admin cancellations and health. Transaction-level fencing and exactly-once behavior for SQLite; prove concurrent tick/join/command order. No direct task creation bypass. No holding DB write lock during LLM inference.
- #400: preserve existing player-origin desire history and goal queue; separate own aspirations and current intention records. Define read model, durable dedup, revision and transaction outcome. UI does not infer actual effect from LLM text.
- #401: extend utility with personality, preferences and memory; hard constraints before score, deterministic fallback, bounded local LLM; calibrated performance and repeated behavior runs. LLM planning only for constitution-permitted goal conflicts, unusual events, dialogue, interpretation; routine scheduling deterministic. No changes to constitutional principles.
- Migration: additive schema, explicit feature/cutover guard; backup/restore trial and old/new compatibility. Existing tasks, events, balances, inventory and facts preserved. No runtime mode selector after cutover. Live tick unchanged for downtime in MVP. DB/infra lane B per AGENTS where applicable.

## Design decisions clarified during transcription

- A revoked/blocked owner is distinct from a dead or system-unavailable character: a living available character without authorized presence is ABSENT. No phantom lease.
- Nonphysical operations may coexist with a physical task only with existing domain checks and no conflict; unknown compatibility fails closed. Physical CONSTRUCT remains single-active.
- LLM use follows constitution tier restrictions; no general periodic unconstrained autonomous LLM scheduler. Rate cap is an upper bound, not a mandatory call schedule.
- The main architecture spec describes the target; 005-player is historical until cutover. Do not replace old tests before new coverage exists.

## Verification for this docs PR

Check exact path and links, diff only planned markdown, section and acceptance-ID coverage, consistency with constitution and existing 005-player historical notes. No production tests run by this author; CI results are separate. Implementation gates: inspect `scripts/check.sh` and current workflows, run focused RED/GREEN and `bash scripts/check.sh`, required lint/test/build/browser tests per applicable bounded work order. Deployment is explicitly excluded; separate owner permission and green CI per AGENTS.
