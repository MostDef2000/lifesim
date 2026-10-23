# 020 P3 — visual reference upload (stage 1: storage + CRUD)

Owner decision record for user-uploaded visual references. Stage 1 is
storage + owner CRUD only; nothing consumes references yet.

## Decisions

| Decision | Value |
|---|---|
| Formats | PNG / JPEG only, decided by magic bytes (`89 50 4E 47 0D 0A 1A 0A`, `FF D8 FF`), then full decode via Pillow (`Image.open` → `verify()` → reopen `load()`) — garbage with valid magic is rejected |
| Conversion | None — original bytes stored unconverted, never re-encoded |
| Size cap | 5 MiB/file (`reference_max_bytes`), enforced on the chunked body read (spooled then capped — the single size gate; multipart Content-Length includes boundary overhead and is not trusted) → 422 "reference too large" |
| Per-user cap | 5 active references (`max_references_per_user`) → 422 "reference quota exceeded". Quota is check-then-insert; under concurrent uploads it is soft (bounded by max + in-flight); accepted, size cap remains the hard bound. |
| Decompression bomb | Decoded canvas hard-capped at 24 MP before `load()` (`_REFERENCE_MAX_PIXELS`); PIL's global `MAX_IMAGE_PIXELS` untouched |
| Ingress bounding | FastAPI parses multipart before dependency resolution (`request.form()` in `fastapi/routing.py` precedes dep solving) — pre-auth body spooling is a framework property, not eliminable at route level. Bounded at the edge: Caddy `request_body { max_size 25MB }` scoped to `/visual/references*` on the prod edge (deployed 2026-10-23, reload verified); the app cap (5 MiB) is the second layer. Site-wide body cap for all lf endpoints: follow-up hardening item (needs xhttp-tunnel impact assessment). |
| Retention | No TTL — owner-managed DELETE (`204`); DELETE removes file + row |
| Ownership | User-scoped (`user_id` FK, indexed); foreign/missing ids answer the same 404 on GET file and DELETE (no existence leak, #193 no-disclosure convention) |
| Storage | `data/visual_references/` via `VisualReferenceStore` (mirrors AssetStore incl. traversal guard); server-derived name `{ref_id}.{png\|jpg}` — the client filename is never used |
| Semantics | References are guidance only: never simulation facts, never fed to the LLM |
| API gate | Reference routes sit behind the same `visual.enabled` 503 gate as the rest of /visual; the `current_user` dependency answers 401 before the route body runs (after the framework-level multipart parse — see Ingress bounding) |

## Explicit deferrals

- **Consumption (img2img / IP-adapter): separate stage, own spec.** The
  adapter graph is txt2img-only (node "4" is `EmptyLatentImage`;
  `deploy/comfy_adapter.py:79-125`) and the box is lane B — feeding
  references into generation requires an adapter change plus a new
  spec/plan/tasks wave.
- **UI wiring**: deferred to the consumption stage (upload UI only makes
  sense once something renders/consumes the reference).
