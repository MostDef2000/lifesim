"""m308 map projection — one authoritative map-space transform (#308 §2).

The LIVING MAP has two coordinate spaces (coords.json):
- ``image_space`` — percentages of the ORIGINAL master 1600x900 image;
- ``locations``    — percentages of the VISIBLE container after CSS
  ``object-fit: cover`` horizontally crops the 16:9 master (``.map-card``
  ``aspect-ratio: 742 / 616``).

``app.js`` must expose ONE projection (``MAP_WORLD`` + ``MAP_COVER_CROP_X`` +
``locationToWorld`` / ``worldToScreen``) and the marker sites inside
``buildLivingMap`` must go through it, so future tile rendering and the
dynamic actors cannot drift apart.

Evidence split: the constants and the transform are SOURCE PINS plus a real
evaluation of the production block in a Node ``vm`` context (no
reimplementation — the source text is extracted from app.js verbatim). The
cross-space check ``locations[k] -> image_space[k]`` is the real proof the
transform is correct; the world->screen round trip only proves self-consistency.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS_PATH = os.path.join(ROOT, "backend/app/web/app.js")
COORDS_PATH = os.path.join(ROOT, "backend/app/web/static/map/coords.json")
SCRATCH = "/tmp/opencode/m308-projection"

with open(APP_JS_PATH, encoding="utf-8") as _f:
    APP_JS = _f.read()
with open(COORDS_PATH, encoding="utf-8") as _f:
    COORDS = json.load(_f)

# coords.json also anchors the housing-grid origin; the 8 gameplay POIs are
# what the #308 order names.
POIS = [k for k in COORDS["locations"] if k != "houses.origin"]

NODE_RUNNER = r"""
import fs from "fs";
import vm from "vm";

const block = fs.readFileSync(process.argv[2], "utf-8");
const inputs = JSON.parse(fs.readFileSync(process.argv[3], "utf-8"));
const ctx = vm.createContext({});
vm.runInContext(
  block
    + "\n;globalThis.__proj = { MAP_WORLD, MAP_COVER_CROP_X,"
    + " locationToWorld, worldToScreen };",
  ctx,
);
const p = ctx.__proj;
const out = {
  MAP_WORLD: { w: p.MAP_WORLD.w, h: p.MAP_WORLD.h },
  MAP_COVER_CROP_X: p.MAP_COVER_CROP_X,
  coords: {},
};
for (const [k, loc] of Object.entries(inputs.locations)) {
  const world = p.locationToWorld({ x: loc.x, y: loc.y });
  out.coords[k] = { world, screen: p.worldToScreen(world) };
}
process.stdout.write(JSON.stringify(out));
"""


def _projection_block():
    """The verbatim production projection block, from MAP_WORLD to buildLivingMap."""
    start = APP_JS.find("const MAP_WORLD")
    assert start != -1, "app.js must declare MAP_WORLD"
    end = APP_JS.find("function buildLivingMap(", start)
    assert end != -1, "projection block must precede function buildLivingMap"
    block = APP_JS[start:end]
    for name in ("MAP_COVER_CROP_X", "function locationToWorld", "function worldToScreen"):
        assert name in block, f"projection block missing {name!r}"
    return block


def _living_map_body():
    start = APP_JS.find("function buildLivingMap(locs, cardW, cardH) {")
    assert start != -1, "app.js must declare function buildLivingMap(locs, cardW, cardH)"
    end = APP_JS.find("\nfunction rerenderLivingMap(", start)
    assert end != -1, "buildLivingMap must be followed by rerenderLivingMap"
    return APP_JS[start:end]


@pytest.fixture(scope="module")
def projection():
    node = shutil.which("node")
    assert node, "node is required to evaluate the production projection block"
    os.makedirs(SCRATCH, exist_ok=True)
    block_path = os.path.join(SCRATCH, "projection_block.js")
    runner_path = os.path.join(SCRATCH, "projection_runner.mjs")
    inputs_path = os.path.join(SCRATCH, "projection_inputs.json")
    with open(block_path, "w", encoding="utf-8") as f:
        f.write(_projection_block())
    with open(runner_path, "w", encoding="utf-8") as f:
        f.write(NODE_RUNNER)
    with open(inputs_path, "w", encoding="utf-8") as f:
        json.dump({"locations": COORDS["locations"]}, f)
    res = subprocess.run(
        [node, runner_path, block_path, inputs_path],
        capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, f"node projection run failed: {res.stderr.strip()}"
    return json.loads(res.stdout)


# ---- constants: source pin + evaluated value ----
def test_map_world_is_master_image_space(projection):
    assert re.search(
        r"const\s+MAP_WORLD\s*=\s*\{\s*w:\s*1600\s*,\s*h:\s*900\s*\}\s*;",
        APP_JS,
    ), "MAP_WORLD must be the literal master size {w:1600,h:900}"
    assert projection["MAP_WORLD"] == {"w": 1600, "h": 900}
    assert COORDS["size"] == [1600, 900], "coords.json master size drifted"


def test_cover_crop_derives_from_card_aspect_ratio(projection):
    assert re.search(
        r"const\s+MAP_COVER_CROP_X\s*=\s*\(1\s*-\s*\(742\s*/\s*616\)\s*"
        r"/\s*\(MAP_WORLD\.w\s*/\s*MAP_WORLD\.h\)\)\s*/\s*2\s*;",
        APP_JS,
    ), "MAP_COVER_CROP_X must derive from the .map-card 742/616 aspect ratio"
    expected = (1 - (742 / 616) / (1600 / 900)) / 2
    c = projection["MAP_COVER_CROP_X"]
    assert abs(c - expected) < 1e-12, (c, expected)
    # A centered crop of an honest fraction, never a degenerate value.
    assert 0.15 < c < 0.17


# ---- transform: real proof + self-consistency ----
def test_poi_census_is_eight(projection):
    assert len(POIS) == 8, POIS
    assert set(POIS) <= set(projection["coords"])


def test_locations_map_to_image_space(projection):
    """locations (visible container %) -> world px reproduces image_space (master %)."""
    for k in POIS:
        world = projection["coords"][k]["world"]
        image = COORDS["image_space"][k]
        dx = abs(world["x"] - image["x"] / 100 * 1600)
        dy = abs(world["y"] - image["y"] / 100 * 900)
        assert dx <= 0.5, f"{k}: world.x off by {dx:.3f} px from image_space"
        assert dy <= 0.5, f"{k}: world.y off by {dy:.3f} px from image_space"


def test_world_to_screen_round_trip(projection):
    """worldToScreen(locationToWorld(locations[k])) is the identity at 100%."""
    for k in POIS:
        loc = COORDS["locations"][k]
        screen = projection["coords"][k]["screen"]
        assert abs(screen["x"] - loc["x"]) <= 0.01, f"{k}: x round trip drifted"
        assert abs(screen["y"] - loc["y"]) <= 0.01, f"{k}: y round trip drifted"


# ---- marker sites: both go through the shared transform ----
def test_marker_sites_use_shared_projection():
    body = _living_map_body()
    assert body.count("worldToScreen(locationToWorld(l), cardW, cardH)") == 2, (
        "both marker sites (house span + POI anchor) must use the projection"
    )
    assert body.count("style: `left:${pos.x}%; top:${pos.y}%`") == 3, (
        "house, POI and night-light marker styles must read the projected pos"
    )
    assert "left:${l.x}%; top:${l.y}%" not in body, (
        "raw /locations percentages must not position markers anymore"
    )
    for marker in ('class: "anchor anchor--house"', "class: `anchor location-anchor"):
        at = body.index(marker)
        window = body[max(0, at - 200):at + 200]
        assert "worldToScreen(locationToWorld(l), cardW, cardH)" in window, (
            f"marker site {marker!r} bypasses the shared projection"
        )
        assert "left:${pos.x}%; top:${pos.y}%" in window, (
            f"marker site {marker!r} does not use projected pos"
        )
