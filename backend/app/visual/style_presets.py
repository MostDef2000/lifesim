"""020 P3 stage 2: server-side style preset constants (visual pipeline).

Single source of truth for the prompt fragments this stage composes
server-side (no user free-text in any of them):

- OIL_PORTRAIT / WATERCOLOR_PORTRAIT — PREFIXES prepended to the portrait
  positive prompt when POST /visual/portraits carries style="oil" /
  style="watercolor" (composed as "<preset>, <prompt>").
- NUDE_FRAMING — the dedicated fine-art nude framing that REPLACES every
  clothing/outerwear fragment (descriptor.build_prompt nude_framing hook).
  Deliberately free of garment vocabulary: the stage-2 contract bans the
  clothing terms ("wearing", "dress", "coat", "overalls") from a nude
  positive — the descriptor's own outfit_mode="nude" branch ("wearing
  nothing") is NOT usable for this.
- STORYBOOK_SCENE — the server-side suffix appended to EVERY scene positive
  prompt (no user-facing style parameter on scenes this wave).
- FULL_BODY_FRAMING — appended AFTER the outfit chain for the W2 preview
  full-body modes (full_body_clothed / full_body_outfit): the framing asks
  for the whole figure in frame without touching what is worn.
- BASE_BODY_FRAMING — the neutral proportion-study framing that REPLACES
  the whole clothing chain for the W2 preview base_body mode (descriptor
  build_prompt preview_mode hook, same mechanics as NUDE_FRAMING).
  Deliberately free of BOTH garment and nudity vocabulary — a neutral
  anatomy/proportion validation frame, non-sexual wording, 18+ gated at
  the route by the endpoint's existing 18-99 age rule.

Pure constants, no imports — safe to import from descriptor.py and the
routes without import-cycle risk.
"""

OIL_PORTRAIT = "oil painting"
WATERCOLOR_PORTRAIT = "watercolor painting"
NUDE_FRAMING = "artistic nude figure study, tasteful, soft studio light"
STORYBOOK_SCENE = (
    "semi-realistic digital painting, warm palette, storybook realism"
)
FULL_BODY_FRAMING = (
    "full body shot, head to toe visible, simple neutral background"
)
BASE_BODY_FRAMING = (
    "full body proportion study, neutral standing pose, "
    "plain studio backdrop, even lighting"
)
