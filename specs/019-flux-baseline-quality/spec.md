# 019 — Flux baseline quality (#119)

## Outcome

Raise production visual quality toward the owner's Flux test baseline by
adopting its **canonical profile structure** (structured face/body/style/scenes
canon + guardrail) inside the existing visual architecture. Flux stays a
presentation layer over committed authoritative state.

Owner-side tooling (Model Manager at 3d.mostdef.ru) is a **capability
reference only** — a production dependency on it is explicitly out of scope.

## Scope

Included (Phase 1, repo-only):
- Structured canonical profile **resolver**: pure function from committed
  character state (name, age, sex, looks, traits, worn clothing) to the
  frozen profile schema (below).
- `descriptor.py` consumes the structured profile instead of the hardcoded
  portrait one-liner (`descriptor.py:167-179`); portrait prompt gains
  face/body/style sections and a casting-scene preset; scene prompts keep
  environment/time/location composition.
- Adult/fictional guardrail derived from real character age; sex label
  rendered as `man/woman` (not raw `M/F`; #197 pools upstream).
- Prompt-profile structure embedded into the descriptor dict persisted on
  generated VisualAssets (no schema change to VisualAsset itself).

Excluded:
- Per-character LoRA use (Phase 2; VRAM on the 12GB box already peaks
  ~93.5% with base flux — KB 1489-1491; FLUX LoRA training on this stack is
  recorded non-viable — KB 1611-1616).
- `CharacterVisualProfile` table (Phase 2, together with #118 editing).
- Negative-prompt transport contract — LANDED as Phase 2b (#227), delivered
  split: lane A (app + repo adapter copy, backward-compatible — pre-update
  host adapters ignore the unknown JSON key via the data.get pattern) is in
  production; lane B (host adapter activation) is a separate sysadmin step,
  handoff pending. Normative source: `compose_negative` in
  `backend/app/visual/descriptor.py` — canonical safety negative first,
  composition second ("bare midriff, exposed stomach, navel"), appended to
  (never replaces) the adapter technical floor; payload key "negative"
  always present, empty-able.
- Any network dependency on 3d.mostdef.ru from prod; any A1111/Forge
  compat layer; any new auth surface; sizes above 512x512.

## Frozen profile schema (from live prompt-profile, 2026-10-14)

```
profile: {face, body, style, negative?, scenes: {casting, full_body?, scene?}}
guardrail: {phrase, canonical_negative}
lora: {available: bool, filename?, engine?}   # Phase 2; always unavailable in Phase 1
```

Reference: owner prompt-profile shape (face "oval face, defined cheekbones…",
scenes.casting "Chest-up portrait, neutral warm-gray studio…", guardrail
canonical_negative list). lifesim generates this per-character; nothing is
imported from owner tooling in Phase 1.

## Requirements

- R1 resolver is deterministic (same character state → same profile; no RNG
  in Phase 1).
- R2 resolver lives in `backend/app/visual/descriptor.py` (or a sibling
  module), no DB writes, no config dependency beyond existing VisualConfig.
- R3 portrait endpoint output: structured prompt with face/body/style +
  casting preset + guardrail; scene endpoint unchanged in contract.
- R4 existing endpoint contracts (paths, request/response shapes, canonical
  reuse, single-flight, negative cache) byte-compatible.
- R5 tests: resolver determinism, prompt contains structured sections and
  guardrail, sex-label mapping, no network (stub path stays deterministic).
- R6 privacy/ownership unchanged (player visual identity rules from #193 R1
  are not touched).

## Acceptance evidence

- Full not-slow suite green (baseline 760 + new tests).
- `uv run ruff check backend/ tests/` clean.
- Manual/acceptance note: one prod portrait generated via adapter shows the
  structured prompt in the VisualAsset record (deferred to deploy check).

## Risks

- Prompt bytes change → tests pinning M6 prompt behavior must be updated
  honestly (no weakened assertions; new pin values = real new content).
- VRAM unchanged (Phase 1 adds no model/size change).
