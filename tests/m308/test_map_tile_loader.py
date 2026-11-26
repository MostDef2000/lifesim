"""m308 tile loader — pure manifest + geometry helpers (#308 §2).

The tile loader is deliberately DOM-free: ``app.js`` exposes
``validateTileManifest`` / ``tileWorldRect`` / ``tileScreenRect`` /
``wantedTiles`` / ``tileBleedStyle`` between ``rerenderLivingMap`` and
``renderWorld``. These tests extract the projection + loader source text
verbatim (same Node ``vm`` pattern as ``test_map_projection.py``) and evaluate
it against the REAL shipped manifest — no reimplementation is trusted as the
oracle for the production output, only as an independent cross-check.
"""
import copy
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS_PATH = os.path.join(ROOT, "backend/app/web/app.js")
MANIFEST_PATH = os.path.join(ROOT, "backend/app/web/static/map/tiles/manifest.json")
SCRATCH = "/tmp/opencode/m308-loader"

with open(APP_JS_PATH, encoding="utf-8") as _f:
    APP_JS = _f.read()
with open(MANIFEST_PATH, encoding="utf-8") as _f:
    MANIFEST = json.load(_f)

WORLD_W, WORLD_H = 1600, 900
AUTHOR_CARD = (742, 616)
LOD_LAYER = {"island": "lod1", "region": "lod2", "local": "lod3"}

NODE_RUNNER = r"""
import fs from "fs";
import vm from "vm";

const block = fs.readFileSync(process.argv[2], "utf-8");
const inputs = JSON.parse(fs.readFileSync(process.argv[3], "utf-8"));
const ctx = vm.createContext({});
vm.runInContext(
  block
    + "\n;globalThis.__m308 = { validateTileManifest, tileWorldRect,"
    + " tileScreenRect, wantedTiles, tileBleedStyle };",
  ctx,
);
const api = ctx.__m308;
const out = { validations: [], world: [], tileRects: [], bleed: [], wanted: [] };
for (const manifest of inputs.manifests) {
  out.validations.push(api.validateTileManifest(manifest));
}
for (const q of inputs.world) {
  const layer = q.manifest.layers[q.layer];
  const tile = layer.tiles.find((t) => t.row === q.row && t.col === q.col);
  out.world.push(api.tileWorldRect(layer, tile));
}
for (const q of inputs.tileRects) {
  const layer = q.manifest.layers[q.layer];
  const tile = layer.tiles.find((t) => t.row === q.row && t.col === q.col);
  out.tileRects.push(api.tileScreenRect(layer, tile, q.cardW, q.cardH));
}
for (const q of inputs.bleed) {
  const layer = q.manifest.layers[q.layer];
  const tile = layer.tiles.find((t) => t.row === q.row && t.col === q.col);
  out.bleed.push(api.tileBleedStyle(tile));
}
for (const q of inputs.wanted) {
  out.wanted.push(api.wantedTiles(
    q.manifest, q.lod, q.focusWorld, q.cardW, q.cardH, q.stageScale, q.clipRect,
  ));
}
process.stdout.write(JSON.stringify(out));
"""


def _loader_block():
    """The verbatim projection + loader source range (MAP_WORLD .. renderWorld)."""
    start = APP_JS.find("const MAP_WORLD")
    assert start != -1, "app.js must declare MAP_WORLD"
    end = APP_JS.find("function renderWorld(", start)
    assert end != -1, "loader block must precede renderWorld"
    block = APP_JS[start:end]
    for name in (
        "function worldToScreen",
        "function validateTileManifest",
        "function tileWorldRect",
        "function tileScreenRect",
        "function wantedTiles",
        "function tileBleedStyle",
    ):
        assert name in block, f"loader block missing {name!r}"
    return block


def _pure_loader_slice():
    start = APP_JS.find("/* ---------- #308 tile loader:")
    assert start != -1
    end = APP_JS.find("/* ---------- #310", start)
    assert end != -1
    return APP_JS[start:end]


# ---- independent Python reference (cross-check only, never the oracle) ----
def _ref_world_to_screen(wx, wy, cw, ch):
    card_aspect = cw / ch
    image_aspect = WORLD_W / WORLD_H
    crop_x = crop_y = 0.0
    if card_aspect < image_aspect:
        crop_x = (1 - card_aspect / image_aspect) / 2
    else:
        crop_y = (1 - image_aspect / card_aspect) / 2
    return (
        ((wx / WORLD_W) - crop_x) / (1 - 2 * crop_x) * 100,
        ((wy / WORLD_H) - crop_y) / (1 - 2 * crop_y) * 100,
    )


def _ref_tile_world_rect(layer, tile):
    px, py, pw, ph = tile["pixelRect"]
    bx, by = layer["bounds"][0], layer["bounds"][1]
    p = layer["pixelsPerWorldUnit"]
    return (bx + px / p, by + py / p, bx + (px + pw) / p, by + (py + ph) / p)


def _ref_tile_screen_rect(layer, tile, cw, ch):
    ax, ay, bx, by = _ref_tile_world_rect(layer, tile)
    a = _ref_world_to_screen(ax, ay, cw, ch)
    b = _ref_world_to_screen(bx, by, cw, ch)
    return {"left": a[0], "top": a[1], "width": b[0] - a[0], "height": b[1] - a[1]}


def _ref_wanted(manifest, lod, focus, cw, ch, scale, clip):
    layer_id = LOD_LAYER.get(lod)
    if not layer_id:
        return []
    layer = (manifest.get("layers") or {}).get(layer_id)
    if not layer or not layer.get("available"):
        return []
    fs = _ref_world_to_screen(focus[0], focus[1], cw, ch)
    fx = fs[0] / 100 * cw
    fy = fs[1] / 100 * ch
    ccx = (clip["left"] + clip["right"]) / 2
    ccy = (clip["top"] + clip["bottom"]) / 2
    hits = []
    for tile in layer["tiles"]:
        r = _ref_tile_screen_rect(layer, tile, cw, ch)
        left = fx + (r["left"] / 100 * cw - fx) * scale
        top = fy + (r["top"] / 100 * ch - fy) * scale
        right = fx + ((r["left"] + r["width"]) / 100 * cw - fx) * scale
        bottom = fy + ((r["top"] + r["height"]) / 100 * ch - fy) * scale
        ix0 = max(left, clip["left"])
        iy0 = max(top, clip["top"])
        ix1 = min(right, clip["right"])
        iy1 = min(bottom, clip["bottom"])
        if ix1 > ix0 and iy1 > iy0:
            dx = (ix0 + ix1) / 2 - ccx
            dy = (iy0 + iy1) / 2 - ccy
            hits.append((dx * dx + dy * dy, f"{layer_id}/{tile['row']}-{tile['col']}"))
    hits.sort()
    return [key for _, key in hits]


def _manifest():
    return copy.deepcopy(MANIFEST)


def _tile(manifest, layer, row, col):
    return next(t for t in manifest["layers"][layer]["tiles"]
                if t["row"] == row and t["col"] == col)


def _wanted_query(manifest, lod, focus, cw, ch, scale, clip):
    return {
        "manifest": manifest,
        "lod": lod,
        "focusWorld": {"x": focus[0], "y": focus[1]},
        "cardW": cw,
        "cardH": ch,
        "stageScale": scale,
        "clipRect": clip,
    }


def _full_clip(cw, ch):
    return {"left": 0, "top": 0, "right": cw, "bottom": ch}


def _build_payload():
    real = _manifest()
    unavailable = _manifest()
    unavailable["layers"]["lod2"] = {"available": False}
    missing = _manifest()
    del missing["layers"]["lod3"]

    valid_manifests = [real, unavailable]
    invalid_manifests = []
    labels = []

    def invalid(label, mutate):
        m = _manifest()
        mutate(m)
        labels.append(label)
        invalid_manifests.append(m)

    def dup_key(m):
        m["layers"]["lod1"]["tiles"][1]["row"] = 0
        m["layers"]["lod1"]["tiles"][1]["col"] = 0

    invalid("schemaVersion", lambda m: m.update(schemaVersion=2))
    invalid("format", lambda m: m.update(format="png"))
    invalid("worldSize", lambda m: m.update(worldSize=[1600, 901]))
    invalid("tileSize", lambda m: m.update(tileSize=256))
    invalid("bleedPx", lambda m: m.update(bleedPx=1))
    invalid("duplicate_key", dup_key)
    invalid("escape_url", lambda m: m["layers"]["lod1"]["tiles"][0].update(
        url="../lod1/0-0.webp"))
    invalid("absolute_url", lambda m: m["layers"]["lod1"]["tiles"][0].update(
        url="/lod1/0-0.webp"))
    invalid("scheme_url", lambda m: m["layers"]["lod1"]["tiles"][0].update(
        url="https://evil.example/lod1/0-0.webp"))
    invalid("source_pixels", lambda m: m["layers"]["lod2"].update(
        sourcePixels=[1919, 1080]))
    invalid("pixel_rect", lambda m: m["layers"]["lod1"]["tiles"][0].update(
        pixelRect=[0, 0, 511, 512]))
    invalid("bad_bleed", lambda m: m["layers"]["lod1"]["tiles"][0].update(
        bleedLTRB=[1, 0, 2, 2]))
    invalid("bounds_outside", lambda m: m["layers"]["lod3"].update(
        bounds=[480, 330, 1200, 240]))
    invalid("grid", lambda m: m["layers"]["lod1"].update(grid=[4, 3]))
    invalid("tile_count", lambda m: m["layers"]["lod2"]["tiles"].pop())
    invalid("missing_layer", lambda m: m.pop("layers"))
    invalid("missing_lod3", lambda m: m["layers"].pop("lod3"))
    invalid("missing_lod2", lambda m: m["layers"].pop("lod2"))
    invalid("missing_lod1", lambda m: m["layers"].pop("lod1"))
    invalid("tile_row", lambda m: m["layers"]["lod1"]["tiles"][0].update(row=2))

    manifests = valid_manifests + invalid_manifests
    validations_expected = [True, True] + [False] * len(invalid_manifests)

    # tileWorldRect queries: hand-computed corners in the assertions.
    world_queries = [
        {"manifest": real, "layer": "lod1", "row": 0, "col": 0},
        {"manifest": real, "layer": "lod2", "row": 1, "col": 1},
        {"manifest": real, "layer": "lod3", "row": 0, "col": 3},
    ]

    rect_queries = [
        {"manifest": real, "layer": "lod1", "row": 0, "col": 0, "cardW": 742, "cardH": 616},
        {"manifest": real, "layer": "lod1", "row": 0, "col": 0, "cardW": 390, "cardH": 844},
        {"manifest": real, "layer": "lod1", "row": 0, "col": 0, "cardW": 1440, "cardH": 900},
        {"manifest": real, "layer": "lod2", "row": 1, "col": 1, "cardW": 742, "cardH": 616},
        {"manifest": real, "layer": "lod2", "row": 1, "col": 1, "cardW": 390, "cardH": 844},
        {"manifest": real, "layer": "lod2", "row": 1, "col": 1, "cardW": 1440, "cardH": 900},
        {"manifest": real, "layer": "lod3", "row": 0, "col": 3, "cardW": 742, "cardH": 616},
    ]

    bleed_queries = [
        {"manifest": real, "layer": "lod2", "row": 1, "col": 1},
        {"manifest": real, "layer": "lod1", "row": 0, "col": 3},
    ]

    scenarios = [
        ("author island 1x", real, "island", (800, 450), *AUTHOR_CARD, 1,
         _full_clip(*AUTHOR_CARD)),
        ("author island off-centre", real, "island", (500, 300), *AUTHOR_CARD, 1,
         _full_clip(*AUTHOR_CARD)),
        ("narrow region 1.65x", real, "region", (800, 450), 390, 844, 1.65,
         _full_clip(390, 844)),
        ("wide region 1.65x off-centre", real, "region", (900, 500), 1440, 900, 1.65,
         _full_clip(1440, 900)),
        ("author local 2.75x", real, "local", (720, 420), *AUTHOR_CARD, 2.75,
         _full_clip(*AUTHOR_CARD)),
        ("narrow local 2.75x off-centre", real, "local", (640, 380), 390, 844, 2.75,
         _full_clip(390, 844)),
        ("wide island 1x", real, "island", (800, 450), 1440, 900, 1,
         _full_clip(1440, 900)),
        ("author region inset clip", real, "region", (800, 450), *AUTHOR_CARD, 1,
         {"left": 120, "top": 80, "right": 620, "bottom": 540}),
        ("unavailable region", unavailable, "region", (800, 450), *AUTHOR_CARD, 1.65,
         _full_clip(*AUTHOR_CARD)),
        ("missing lod3 local", missing, "local", (800, 450), *AUTHOR_CARD, 2.75,
         _full_clip(*AUTHOR_CARD)),
        ("unknown lod", real, "galaxy", (800, 450), *AUTHOR_CARD, 1,
         _full_clip(*AUTHOR_CARD)),
    ]
    wanted_queries = [_wanted_query(*s[1:]) for s in scenarios]
    wanted_expected = [
        _ref_wanted(manifest, lod, focus, cw, ch, scale, clip)
        for _, manifest, lod, focus, cw, ch, scale, clip in scenarios
    ]

    # Edge-touch: at a square 1600x900 card the cover crop is zero, so the
    # world->stage map is an exact identity scaling and clip left can sit
    # precisely on a tile boundary.
    edge_layer = real["layers"]["lod1"]
    tile00 = _tile(real, "lod1", 0, 0)
    edge_rect = _ref_tile_screen_rect(edge_layer, tile00, 1600, 900)
    edge_x = (edge_rect["left"] + edge_rect["width"]) / 100 * 1600
    edge_query = _wanted_query(real, "island", (0, 0), 1600, 900, 1,
                               {"left": edge_x, "top": 0, "right": 1600, "bottom": 900})

    inputs = {
        "manifests": manifests,
        "world": world_queries,
        "tileRects": rect_queries,
        "bleed": bleed_queries,
        "wanted": wanted_queries + [edge_query],
    }
    return {
        "inputs": inputs,
        "labels": labels,
        "validations_expected": validations_expected,
        "scenario_names": [s[0] for s in scenarios],
        "wanted_expected": wanted_expected,
    }


@pytest.fixture(scope="module")
def api():
    node = shutil.which("node")
    assert node, "node is required to evaluate the production loader block"
    os.makedirs(SCRATCH, exist_ok=True)
    payload = _build_payload()
    block_path = os.path.join(SCRATCH, "loader_block.js")
    runner_path = os.path.join(SCRATCH, "loader_runner.mjs")
    inputs_path = os.path.join(SCRATCH, "loader_inputs.json")
    with open(block_path, "w", encoding="utf-8") as f:
        f.write(_loader_block())
    with open(runner_path, "w", encoding="utf-8") as f:
        f.write(NODE_RUNNER)
    with open(inputs_path, "w", encoding="utf-8") as f:
        json.dump(payload["inputs"], f)
    res = subprocess.run(
        [node, runner_path, block_path, inputs_path],
        capture_output=True, text=True, timeout=90,
    )
    assert res.returncode == 0, f"node loader run failed: {res.stderr.strip()}"
    out = json.loads(res.stdout)
    out["payload"] = payload
    return out


# ---- source placement / purity pins ----
def test_loader_block_sits_between_rerender_and_render_world():
    rerender = APP_JS.find("function rerenderLivingMap(")
    loader = APP_JS.find("function validateTileManifest(")
    render = APP_JS.find("function renderWorld(")
    assert -1 < rerender < loader < render


def test_loader_slice_is_dom_free_and_tile_space():
    block = _pure_loader_slice()
    assert "document" not in block
    assert "window" not in block
    assert "locationToWorld(" not in block


# ---- real manifest + validation ----
def test_real_manifest_validates(api):
    assert api["validations"][0] == {"ok": True, "error": None}


def test_real_manifest_counts_and_scale():
    for layer_id, count, ppwu in (("lod1", 8, 1.0), ("lod2", 12, 2.0), ("lod3", 8, 4.0)):
        layer = MANIFEST["layers"][layer_id]
        assert layer["available"] is True
        assert len(layer["tiles"]) == count
        assert layer["grid"] == [len({t["col"] for t in layer["tiles"]}),
                                 len({t["row"] for t in layer["tiles"]})]
        assert abs(layer["pixelsPerWorldUnit"] - ppwu) < 1e-9


@pytest.mark.parametrize("index", range(2, 22))
def test_invalid_manifests_are_rejected(api, index):
    label = api["payload"]["labels"][index - 2]
    result = api["validations"][index]
    assert result["ok"] is False, f"{label} must be rejected"
    assert result["error"], f"{label} must explain the rejection"


def test_available_false_layer_is_valid_and_schedules_nothing(api):
    payload = api["payload"]
    unavailable_index = 1
    assert api["validations"][unavailable_index]["ok"] is True
    scenario_index = payload["scenario_names"].index("unavailable region")
    assert api["wanted"][scenario_index] == []


# ---- world corners ----
def test_tile_world_rect_known_tiles(api):
    world = api["world"]
    assert world[0]["ax"] == pytest.approx(0, abs=1e-12)
    assert world[0]["ay"] == pytest.approx(0, abs=1e-12)
    assert world[0]["bx"] == pytest.approx(512, abs=1e-12)
    assert world[0]["by"] == pytest.approx(512, abs=1e-12)

    # lod2 row1 col1: bounds (320,240), ppwu 2, pixelRect (512,512,512,512)
    assert world[1]["ax"] == pytest.approx(576, abs=1e-12)
    assert world[1]["ay"] == pytest.approx(496, abs=1e-12)
    assert world[1]["bx"] == pytest.approx(832, abs=1e-12)
    assert world[1]["by"] == pytest.approx(752, abs=1e-12)

    # lod3 row0 col3: bounds (480,330), ppwu 4, pixelRect (1536,0,384,512)
    assert world[2]["ax"] == pytest.approx(864, abs=1e-12)
    assert world[2]["ay"] == pytest.approx(330, abs=1e-12)
    assert world[2]["bx"] == pytest.approx(960, abs=1e-12)
    assert world[2]["by"] == pytest.approx(458, abs=1e-12)


# ---- screen rects are viewport-aware ----
def test_tile_screen_rect_matches_reference_at_three_viewports(api):
    queries = api["payload"]["inputs"]["tileRects"]
    for query, got in zip(queries, api["tileRects"]):
        layer = query["manifest"]["layers"][query["layer"]]
        tile = _tile(query["manifest"], query["layer"], query["row"], query["col"])
        want = _ref_tile_screen_rect(layer, tile, query["cardW"], query["cardH"])
        for field in ("left", "top", "width", "height"):
            assert got[field] == pytest.approx(want[field], abs=1e-9)


def test_tile_screen_rect_differs_between_author_and_live_boxes(api):
    # lod1/0-0 at the author 742x616 card vs 390x844 vs 1440x900.
    author, narrow, wide = api["tileRects"][0], api["tileRects"][1], api["tileRects"][2]
    assert author["left"] == pytest.approx(-23.7945, abs=1e-3)
    assert author["width"] == pytest.approx(47.2285, abs=1e-3)
    assert narrow["left"] < author["left"] - 100  # stronger cover crop on portrait
    assert narrow["width"] > author["width"] + 70
    assert wide["left"] > author["left"]
    assert wide["width"] < author["width"]


# ---- culling ----
def test_wanted_tiles_match_independent_positive_intersections(api):
    payload = api["payload"]
    names = payload["scenario_names"]
    expected = payload["wanted_expected"]
    for index, name in enumerate(names):
        assert api["wanted"][index] == expected[index], name


def test_wanted_tiles_known_sets(api):
    payload = api["payload"]
    names = payload["scenario_names"]
    by_name = {name: api["wanted"][i] for i, name in enumerate(names)}
    assert set(by_name["author island 1x"]) == {
        "lod1/0-0", "lod1/0-1", "lod1/0-2", "lod1/1-0", "lod1/1-1", "lod1/1-2",
    }
    # 390x844: strong horizontal crop leaves only the middle two columns.
    assert set(by_name["narrow region 1.65x"]) == {
        "lod2/0-1", "lod2/1-1", "lod2/0-2", "lod2/1-2",
    }
    assert by_name["unknown lod"] == []
    assert by_name["missing lod3 local"] == []


def test_wanted_tiles_are_nearest_first(api):
    payload = api["payload"]
    names = payload["scenario_names"]
    for index, name in enumerate(names):
        ordered = api["wanted"][index]
        # The reference list is distance-sorted nearest-first; equality proves
        # set and ordering together, uniqueness rules out double-scheduling.
        assert ordered == payload["wanted_expected"][index], name
        assert len(set(ordered)) == len(ordered)


def test_edge_touch_is_excluded(api):
    result = api["wanted"][-1]
    # clip.left sits exactly on the col0 right edge: zero-area contact only.
    assert "lod1/0-0" not in result
    assert "lod1/1-0" not in result
    assert set(result) == {
        "lod1/0-1", "lod1/0-2", "lod1/0-3", "lod1/1-1", "lod1/1-2", "lod1/1-3",
    }


# ---- bleed style ----
def _style_percentages(style):
    return {
        name: float(value.rstrip("%"))
        for name, value in re.findall(r"([a-z-]+):(-?[0-9.]+%);", style)
    }


def test_tile_bleed_style_for_2px_bleed(api):
    inner, edge = api["bleed"]
    assert "object-fit" not in inner and "object-fit" not in edge

    # lod2/1-1 is a full interior 512x512 core with 2px bleed on all sides.
    values = _style_percentages(inner)
    assert values["left"] == pytest.approx(-100 * 2 / 512, abs=1e-9)
    assert values["top"] == pytest.approx(-100 * 2 / 512, abs=1e-9)
    assert values["width"] == pytest.approx(100 * (512 + 2 + 2) / 512, abs=1e-9)
    assert values["height"] == pytest.approx(100 * (512 + 2 + 2) / 512, abs=1e-9)

    # lod1/0-3: 64x512 core, bleed left 2 / right 0 / top 0 / bottom 2.
    values = _style_percentages(edge)
    assert values["left"] == pytest.approx(-100 * 2 / 64, abs=1e-9)
    assert values["top"] == pytest.approx(0, abs=1e-9)
    assert values["width"] == pytest.approx(100 * (64 + 2 + 0) / 64, abs=1e-9)
    assert values["height"] == pytest.approx(100 * (512 + 0 + 2) / 512, abs=1e-9)
