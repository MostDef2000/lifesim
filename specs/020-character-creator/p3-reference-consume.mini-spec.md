# 020 P3 — reference consumption (stage 2: kontext + style presets + nude)

Owner decision record for stage 2: uploaded references are CONSUMED by
generation (FLUX.1 Kontext img2img), alongside server-side style presets
and the per-request nude flag. Stage-1 storage/CRUD semantics are
unchanged; nothing in this stage mutates the simulation.

This spec fulfills the stage-1 deferral (p3-reference-upload.mini-spec.md,
«Explicit deferrals»: consumption is a separate stage with its own
spec/plan/tasks wave).

## What ships

| Decision | Value |
|---|---|
| Consumption path | Both `POST /visual/portraits/{cid}` and `POST /visual/scenes` accept an optional `reference_id` → base64 of the stored file rides the transport call as `reference_b64`; the adapter builds the FLUX.1 Kontext Dev graph (UNETLoader + DualCLIPLoader, no EmptyLatentImage — the reference latent seeds the sample). Reference ingestion is ComfyUI-native: decoded bytes are POSTed to `/upload/image` (multipart, field "image", random unique filename, `overwrite=true` as a multipart form field) and LoadImage receives the returned file name |
| Reference gate | Owner-scoped: missing AND foreign `reference_id` answer the SAME stage-1 404 (no existence leak, #193 convention), resolved BEFORE any generation — a rejected reference never reaches the transport |
| Style presets (portraits) | Server-side enum only: `style: "" (default) \| "oil" \| "watercolor"`, anything else → 422 before generation; the preset constant (OIL_PORTRAIT / WATERCOLOR_PORTRAIT) PREFIXES the positive prompt |
| Scene style | No user-facing scene style parameter this wave — a server-side STORYBOOK_SCENE suffix is appended to EVERY scene positive prompt |
| Render params vs content | Style presets and the storybook suffix are TRANSPORT-level only — the asset row / response keeps the RAW `build_prompt` bytes (m205 TestSceneRouteGuard pin); the nude framing, by contrast, IS content and lives inside `build_prompt` |
| Nude flag | Portrait-only `nude: bool` with the П4 18+ gate: requires the requesting owner's `age_confirmed`, else 422 (route is owner-scoped, NPCs can never be pushed into nude rendering). Positive takes the dedicated NUDE_FRAMING fragment (no clothing term in a nude positive); the anti-crop composition negative ("bare midriff, exposed stomach, navel") is DROPPED — a nude render must not fight its own negative — and the canonical guardrail floor is KEPT; non-nude negative bytes unchanged (m227 pins) |
| Canonical reuse | §70 fast-path (return existing canonical, no generation) stays for PLAIN requests only; a customized request (reference/style/nude) would render different output than the stored canonical, so it ALWAYS generates |
| Adapter contract | `reference_b64` OPTIONAL on `POST /generate`: absent/empty → legacy txt2img graph byte-identical; non-empty + `flux` → kontext graph; `pony` + reference → 400 (kontext is flux-only); malformed base64 → 400 — all before anything reaches ComfyUI |

## Explicit deferrals

- **Pony img2img**: out of scope — `pony` + reference answers 400.
- **style-lora / IP-adapter**: not this wave — kontext is the single
  consumption mechanism.
- **Preview-route parity** (`POST /visual/preview`): known gap — the
  preview path gains no reference/style/nude parameters this wave and
  keeps pre-stage-2 bytes.
- **LLM prompt-writer**: deferred — no LLM-side composition of
  references/styles in this stage.

## Rollback

- Adapter: the worker keeps the pre-stage-2 `comfy_adapter.py` as `.bak`
  (sha256 сверяется при обновлении, deploy/README.md §11) — restoring it
  reverts the transport; conversely, old adapters ignore the unknown
  `reference_b64` key (`data.get` pattern), so app-forward/adapter-back
  mixes degrade to legacy txt2img instead of failing.
- App: `git revert` of the stage-2 app commits removes the request-body
  fields; no data migration — stored assets/prompts and stage-1
  references stay valid.

## Verification

- Offline: `tests/m234/` (adapter kontext graph, transport reference_b64
  compat, API reference/style/nude paths) plus regression suites
  `tests/m205/ tests/m227/ tests/m233/ tests/m6/` — all green.
- Live lane-B probe checklist (sea-speed-worker, real ComfyUI):
  - [ ] kontext graph validity — a real `POST /prompt` run returns PNG
        (worker's ComfyUI build accepts UNETLoader/DualCLIPLoader/
        ReferenceLatent node ids of the kontext graph);
  - [ ] kontext + `flux1-uncensored` LoRA combination renders
        (LoraLoader branch inside the kontext graph);
  - [ ] VRAM headroom on the 12GB worker — kontext fp8 is the same
        ~12GB class as the already-running flux1-dev-fp8; the first live
        run is the real headroom test;
  - [ ] steps-20 latency measured on the kontext path; KSampler steps may
        tune down after the probe (one-line adapter constant).

  Worker-side reference retention: every reference request uploads a fresh
  copy into the ComfyUI container's input directory (valery-comfyui-1,
  mounted input dir); files persist after job completion (including failed
  jobs) — they are server-side artifacts not covered by source-reference
  deletion. Cleanup is a periodic operator action on the box (input files
  are small, ≤5MiB each); disk monitoring covers the ComfyUI data volume.
