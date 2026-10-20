# 019 — tasks

- [x] T0 Live contract captured (owner Model Manager: /api/status, /api/models, /api/prompt-profile; 2026-10-14) — input only.
- [x] T1 Resolver: profile schema + deterministic builder from character state.
- [x] T2 Descriptor integration: portrait prompt consumes profile; sex-label fix; guardrail from real age.
- [x] T3 Scene builder keeps contract; embeds profile where useful.
- [x] T4 tests/m119: determinism, sections, guardrail, mapping, existing pins honest.
- [ ] T5 Full not-slow green; ruff clean.
- [x] (Phase 2b, #227) Negative-prompt transport contract, lane A: compose_negative (canonical + composition), transport "negative" param (always-present payload key), routes pass it via the compat dispatch, repo adapter appends to its floor. Lane B (host adapter activation) — separate sysadmin step, handoff pending.
- [ ] (Phase 2, separate spec amendment) CharacterVisualProfile table + editing + LoRA.
