# 019 — plan

## Current behavior
- Portrait prompt: hardcoded one-liner `descriptor.py:167-179` (name/age/sex +
  "clean background, detailed face"); guardrail "adult fictional character"
  static (`:171-173`); sex rendered raw (`M`/`F`).
- Transport: `POST {base}/generate` `{prompt, seed, size, model, lora}`
  (`transports.py:73-87`); adapter owns workflow/model routing
  (`deploy/comfy_adapter.py:4-14`); prod config `config/production.yaml:263-272`.
- VisualAsset persists descriptor/prompt/seed/model/lora/canonical
  (`models.py:564-580`).

## Design (Phase 1)
- New resolver in the visual package: character state → frozen profile schema
  (spec). Deterministic; traits→style keywords, looks→face/body seeds,
  clothing from worn objects when present.
- `build_portrait_prompt` / scene builders consume the profile: sections in
  fixed order (guardrail phrase first, then face, body, style, scene
  preset) — implemented order, spec R3 leaves the order free.
- Sex label mapping: `M→man`, `F→woman` (upstream pools are #197).
- No DB, no config change, no transport change in Phase 1.

## Test strategy
- New module tests (m119): resolver determinism; prompt sections present;
  guardrail contains real age; sex-label mapping; scene prompt unchanged
  contract; existing visual tests (m6/m156-adjacent visual pins, m189)
  stay green with honest pin updates only where prompt text is pinned.

## Rollout / rollback
- Pure code, deploy lane A with the next release train; rollback = revert
  commit; adapter untouched.
