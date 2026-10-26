#!/usr/bin/env python3
"""Render the Reineke Island base map from the pinned OSM snapshot (issue #95).

Deterministic off-box pipeline: pure stdlib + Pillow, no network, no clock, no
RNG in the render path. Reads ONLY scripts/map/input/**, verifies the snapshot
sha256 against scripts/map/input/manifest.json (fails loudly on mismatch) and
produces in one run:

  - backend/app/web/static/map/base.png          1600x900 RGB master image
  - backend/app/web/static/map/coords.json       anchor coords + provenance
  - backend/app/web/static/map/world-island.jpg  JPEG re-encode of base.png (<1 MB)

The base image carries a baked "© OpenStreetMap contributors" credit line
(PIL built-in font, deterministic) centered in the client-visible x-band near
the bottom edge.

Style contract: docs/VISUAL-BIBLE.md sections 28-30 (rich top-down geographic
map, real coastline, readable game map, whole island in frame).

Run from the repo root:
    uv run python scripts/map/render_map.py
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

# --------------------------------------------------------------------------
# Pinned inputs / outputs
# --------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = REPO_ROOT / "scripts" / "map" / "input" / "reineke-island.osm"
MANIFEST_PATH = REPO_ROOT / "scripts" / "map" / "input" / "manifest.json"
OUT_DIR = REPO_ROOT / "backend" / "app" / "web" / "static" / "map"

# --------------------------------------------------------------------------
# Client alignment constants.
# .map-card has aspect-ratio 742/616 and .map-base uses object-fit: cover
# (backend/app/web/style.css .map-card/.map-base); anchors are placed with
# left/top percentages of the container box (backend/app/web/app.js). For a
# 1600x900 image the cover crop only trims the sides; the visible x-band and
# the container<->image conversions below are computed from the real sizes.
# --------------------------------------------------------------------------
IMAGE_W = 1600
IMAGE_H = 900
CONTAINER_W = 742.0
CONTAINER_H = 616.0
BAND_MARGIN_FRAC = 0.02  # required anchor margin inside the visible x-band
ISLAND_WIDTH_FRAC = 0.88  # island width as a fraction of the visible band
V_MARGIN_PX = 96.0  # vertical margin above/below the island, image px

# --------------------------------------------------------------------------
# Settlement grid, mirroring backend/app/world/seed_world.py: houses fill the
# listed cells on a col*3.9 / row*4.6 (container %) grid anchored at
# houses.origin and the POIs sit on the same grid, so the 24 house anchors
# (baked into seed_world, untouchable here) never overlap a POI anchor.
# Cell (5, 0) stays empty in seed_world ("pier gap"); the real pier anchor is
# placed on the coastline instead, per the issue placement rule.
# --------------------------------------------------------------------------
GRID_COL_STEP = 3.9
GRID_ROW_STEP = 4.6
POI_CELLS = {
    "settlement": (4, 1),
    "home": (1, 2),
    "kitchen": (3, 2),
    "well": (4, 2),
    "storage": (5, 2),
    "workshop": (2, 3),
    "shop": (5, 3),
}
HOUSE_CELLS = [
    (0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (6, 0), (7, 0),
    (0, 1), (1, 1), (2, 1), (3, 1), (5, 1), (6, 1), (7, 1),
    (0, 2), (2, 2), (6, 2), (7, 2),
    (0, 3), (1, 3), (3, 3), (4, 3), (6, 3), (7, 3),
]
LOCATION_ORDER = ["settlement", "shop", "workshop", "kitchen", "storage", "well", "pier", "home"]

# --------------------------------------------------------------------------
# Palette (RGB): rich top-down geographic, readable, per VISUAL-BIBLE 28.
# --------------------------------------------------------------------------
DEEP_SEA = (36, 90, 124)
SHELF_FAR = (50, 106, 136)  # one faint second band, barely lighter than open sea
SHELF_NEAR = (86, 146, 166)  # tight bright band hugging the coast
SHOAL = (116, 168, 184)  # small lighter patches inside the near band
SURF = (186, 220, 220)

# Shelf band edges in meters: (base radius, amplitude, cycles, phase, harmonic).
# A tight organically-varying band hugging the coast plus one faint outer
# edge (review G1: calm quiet sea, no halo); clamped monotonic at paint time.
SHELF_BANDS = (
    (280.0, 0.25, 2.2, 1.9, 0.15),  # faint outer edge
    (180.0, 0.30, 3.0, 0.0, 0.12),  # tight inner edge
)
SURF_EDGE_M = 11.0
SHELF_BAND_COLORS = (SHELF_FAR, SHELF_NEAR)
BEACH_M = 16.0
FOREST_M = 110.0
RIDGE_M = 380.0
POI_INLAND_MIN_PX = 8.0  # POIs must sit at least this far (px) from the coastline
PIER_COAST_TOL_PX = 2.0  # pier must sit this close to the polygon boundary

LAND_MEADOW = (152, 158, 104)
LAND_LIGHT = (163, 169, 113)
LAND_SHADOW = (140, 146, 96)
FOREST = (94, 120, 74)
FOREST_CLUMP_A = (84, 110, 67)
FOREST_CLUMP_B = (101, 127, 80)
RIDGE = (78, 102, 64)
RIDGE_DEEP = (69, 91, 57)
VILLAGE_MEADOW = (168, 170, 118)
BEACH = (196, 182, 134)
TRAIL = (140, 120, 90)  # lighter footpath tone so dashes survive the calm terrain
POI_ROOF = (206, 122, 92)
VILLAGE_ROOF = (168, 134, 104)
ROOF_LINE = (104, 82, 64)
PIER_WOOD = (170, 138, 100)
PIER_OUTLINE = (96, 76, 58)

# Baked ODbL attribution (visible to map users; see README license section).
CREDIT_TEXT = "© OpenStreetMap contributors"
CREDIT_SIZE_PX = 16  # PIL built-in scalable font — no font files, deterministic
CREDIT_MARGIN_PX = 12
CREDIT_COLOR = (200, 200, 200)

# Hash salts for the deterministic pseudo-texture (splitmix-style integer
# hashes; no RNG, no clock — byte-identical across runs).
SALT_SHOAL = 23
SALT_FOREST = 47
SALT_RIDGE = 59
SALT_GRAIN = 71
SALT_TRAIL = 83
SALT_VILLAGE = 97


# --------------------------------------------------------------------------
# Snapshot loading + verification
# --------------------------------------------------------------------------
def load_and_verify_snapshot() -> tuple[str, str]:
    """Return (osm_data_timestamp, sha256) after verifying the pinned digest."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    pinned = manifest.get("sha256")
    if not pinned:
        raise SystemExit(f"manifest {MANIFEST_PATH} has no sha256 field")
    digest = hashlib.sha256(SNAPSHOT_PATH.read_bytes()).hexdigest()
    if digest != pinned:
        raise SystemExit(
            f"OSM snapshot verification FAILED: {SNAPSHOT_PATH} sha256 {digest} "
            f"!= manifest sha256 {pinned}"
        )
    timestamp = manifest.get("osm_data_timestamp")
    if not timestamp:
        raise SystemExit(f"manifest {MANIFEST_PATH} has no osm_data_timestamp field")
    return str(timestamp), digest


# --------------------------------------------------------------------------
# OSM parsing: relation 1933752 -> single closed outer ring
# --------------------------------------------------------------------------
def parse_osm(path: Path) -> tuple[dict[int, tuple[float, float]], list[int]]:
    root = ET.parse(path).getroot()
    nodes = {
        int(node.attrib["id"]): (float(node.attrib["lon"]), float(node.attrib["lat"]))
        for node in root.findall("node")
    }
    ways = {
        int(way.attrib["id"]): [int(nd.attrib["ref"]) for nd in way.findall("nd")]
        for way in root.findall("way")
    }
    relations = root.findall("relation")
    if len(relations) != 1:
        raise SystemExit(f"expected exactly one relation in {path}, found {len(relations)}")
    outer_ids = sorted(
        int(member.attrib["ref"])
        for member in relations[0].findall("member")
        if member.attrib.get("type") == "way" and member.attrib.get("role") == "outer"
    )
    ring = _stitch_outer_ring({way_id: ways[way_id] for way_id in outer_ids})
    return nodes, ring


def _stitch_outer_ring(outer_ways: dict[int, list[int]]) -> list[int]:
    """Join the outer coastline ways (sorted by way id) into one closed ring."""
    order = sorted(outer_ways)
    ring = list(outer_ways[order[0]])
    remaining = list(order[1:])
    while remaining:
        tail, head = ring[-1], ring[0]
        for way_id in remaining:
            seq = outer_ways[way_id]
            if seq[0] == tail:
                ring.extend(seq[1:])
            elif seq[-1] == tail:
                ring.extend(reversed(seq[:-1]))
            elif seq[-1] == head:
                ring = list(reversed(seq[:-1])) + ring
            elif seq[0] == head:
                ring = list(reversed(seq[1:])) + ring
            else:
                continue
            remaining.remove(way_id)
            break
        else:
            raise SystemExit(f"outer coastline ways do not close a ring: {remaining}")
    if ring[0] != ring[-1]:
        raise SystemExit("stitched outer ring is not closed")
    return ring[:-1]


# --------------------------------------------------------------------------
# Geometry helpers (stdlib only)
# --------------------------------------------------------------------------
def local_projection_constants(
    lats: list[float], lons: list[float]
) -> tuple[float, float, float, float]:
    """(lat0, lon0, meters per degree lat, meters per degree lon) at bbox center."""
    lat0 = (min(lats) + max(lats)) / 2.0
    lon0 = (min(lons) + max(lons)) / 2.0
    phi = math.radians(lat0)
    m_per_deg_lat = 111132.92 - 559.82 * math.cos(2 * phi) + 1.175 * math.cos(4 * phi)
    m_per_deg_lon = 111412.84 * math.cos(phi) - 93.5 * math.cos(3 * phi)
    return lat0, lon0, m_per_deg_lat, m_per_deg_lon


def polygon_centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Area centroid of a simple polygon (shoelace)."""
    area2 = cx = cy = 0.0
    count = len(points)
    for i in range(count):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % count]
        cross = x1 * y2 - x2 * y1
        area2 += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(area2) < 1e-9:
        raise SystemExit("degenerate island polygon (zero area)")
    return cx / (3.0 * area2), cy / (3.0 * area2)


def point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    """Ray casting (even-odd rule)."""
    px, py = point
    inside = False
    count = len(polygon)
    j = count - 1
    for i in range(count):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > py) != (yj > py):
            x_cross = (xj - xi) * (py - yi) / (yj - yi) + xi
            if px < x_cross:
                inside = not inside
        j = i
    return inside


def dist_to_polygon_boundary(
    point: tuple[float, float], polygon: list[tuple[float, float]]
) -> float:
    """Min distance (px) from the point to the polygon edge segments."""
    px, py = point
    best = float("inf")
    count = len(polygon)
    for i in range(count):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % count]
        dx, dy = x2 - x1, y2 - y1
        seg_len2 = dx * dx + dy * dy
        if seg_len2 > 0.0:
            t = ((px - x1) * dx + (py - y1) * dy) / seg_len2
            t = max(0.0, min(1.0, t))
        else:
            t = 0.0
        nearest = (x1 + t * dx - px, y1 + t * dy - py)
        best = min(best, math.hypot(*nearest))
    return best


def inland_fallback(
    start: tuple[float, float], polygon: list[tuple[float, float]]
) -> tuple[float, float]:
    """Deterministic inland point: bisect from `start` toward the vertex mean."""
    vx = sum(p[0] for p in polygon) / len(polygon)
    vy = sum(p[1] for p in polygon) / len(polygon)
    lo, hi = 0.0, 1.0
    for _ in range(48):
        mid = (lo + hi) / 2.0
        probe = (start[0] + (vx - start[0]) * mid, start[1] + (vy - start[1]) * mid)
        if point_in_polygon(probe, polygon):
            hi = mid
        else:
            lo = mid
    point = (start[0] + (vx - start[0]) * hi, start[1] + (vy - start[1]) * hi)
    if not point_in_polygon(point, polygon):
        raise SystemExit("could not find an inland settlement fallback point")
    return point


# --------------------------------------------------------------------------
# Client alignment math (computed, not hardcoded)
# --------------------------------------------------------------------------
def visible_x_band() -> tuple[float, float]:
    """Visible image-x fraction range under object-fit: cover center crop."""
    container_aspect = CONTAINER_W / CONTAINER_H
    image_aspect = IMAGE_W / IMAGE_H
    if image_aspect >= container_aspect:
        # Height-limited: the sides are cropped.
        half = 0.5 * container_aspect / image_aspect
        return 0.5 - half, 0.5 + half
    # Width-limited: the top/bottom are cropped (not our case; kept for safety).
    return 0.0, 1.0


def image_to_container_pct(x_px: float, y_px: float) -> tuple[float, float]:
    x0, x1 = visible_x_band()
    return (x_px / IMAGE_W - x0) / (x1 - x0) * 100.0, y_px / IMAGE_H * 100.0


def container_pct_to_image(pct_x: float, pct_y: float) -> tuple[float, float]:
    x0, x1 = visible_x_band()
    return (x0 + pct_x / 100.0 * (x1 - x0)) * IMAGE_W, pct_y / 100.0 * IMAGE_H


# --------------------------------------------------------------------------
# Scanline interval machinery: exact distance-to-coastline fills.
# For a scanline row y, `_capsule_intervals` returns the merged x-ranges where
# the distance to the coastline segments is <= radius (a union of capsule
# cross-sections). Combined with the even-odd inside intervals of the polygon
# this yields exact shelf rings, surf, beach rim and the eroded forest/ridge
# zones without any geometry dependencies.
# --------------------------------------------------------------------------
def _segment_intervals_radii(
    segments: list[tuple[float, float, float, float]],
    y: float,
    radii: list[float],
) -> list[tuple[float, float]]:
    """Merged x-spans where distance to each segment <= its own radius.

    Per-segment radii let the shelf band edges breathe organically along the
    coast arc (review F1) while the union stays continuous for smooth modulus.
    """
    spans: list[tuple[float, float]] = []
    for (x1, y1, x2, y2), radius in zip(segments, radii):
        if radius <= 0.0:
            continue
        lo, hi = (y1, y2) if y1 <= y2 else (y2, y1)
        if y < lo - radius or y > hi + radius:
            continue
        if lo == hi:  # horizontal segment
            w = math.sqrt(max(0.0, radius * radius - (y - y1) * (y - y1)))
            spans.append((min(x1, x2) - w, max(x1, x2) + w))
        elif lo <= y <= hi:
            x_at = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            spans.append((x_at - radius, x_at + radius))
        else:  # cap around the nearer endpoint
            ye = hi if y > hi else lo
            xe = x2 if (y > hi and y2 >= y1) or (y < lo and y2 < y1) else x1
            dy = y - ye
            w = math.sqrt(max(0.0, radius * radius - dy * dy))
            spans.append((xe - w, xe + w))
    return _merge_spans(spans)


def _segment_intervals(
    segments: list[tuple[float, float, float, float]], y: float, radius: float
) -> list[tuple[float, float]]:
    return _segment_intervals_radii(segments, y, [radius] * len(segments))


def _polygon_inside_intervals(
    polygon: list[tuple[float, float]], y: float
) -> list[tuple[float, float]]:
    crossings: list[float] = []
    count = len(polygon)
    j = count - 1
    for i in range(count):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            crossings.append((xj - xi) * (y - yi) / (yj - yi) + xi)
        j = i
    crossings.sort()
    return [(crossings[k], crossings[k + 1]) for k in range(0, len(crossings) - 1, 2)]


def _merge_spans(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not spans:
        return []
    spans = sorted(spans)
    merged = [spans[0]]
    for start, end in spans[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _subtract_spans(
    base: list[tuple[float, float]], cut: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for b_start, b_end in base:
        cursor = b_start
        for c_start, c_end in cut:
            if c_end <= cursor or c_start >= b_end:
                continue
            if c_start > cursor:
                result.append((cursor, min(c_start, b_end)))
            cursor = max(cursor, c_end)
            if cursor >= b_end:
                break
        if cursor < b_end:
            result.append((cursor, b_end))
    return result


def _intersect_spans(
    a: list[tuple[float, float]], b: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for a_start, a_end in a:
        for b_start, b_end in b:
            lo, hi = max(a_start, b_start), min(a_end, b_end)
            if lo < hi:
                result.append((lo, hi))
    return result


def paint_spans(
    mask: Image.Image, row_y: int, spans: list[tuple[float, float]]
) -> None:
    if not spans:
        return
    draw = ImageDraw.Draw(mask)
    for start, end in spans:
        x0 = max(0, int(math.floor(start)))
        x1 = min(IMAGE_W, int(math.ceil(end)))
        if x1 > x0:
            draw.rectangle([x0, row_y, x1 - 1, row_y], fill=255)


# --------------------------------------------------------------------------
# Placement
# --------------------------------------------------------------------------
def pier_vertex(nodes: dict[int, tuple[float, float]], ring: list[int]) -> int:
    """Southernmost-then-westernmost coastline vertex (min lat, then min lon)."""
    best = min(ring, key=lambda node_id: (nodes[node_id][1], nodes[node_id][0]))
    return best


def outward_normal_at(
    index: int, points: list[tuple[float, float]], polygon: list[tuple[float, float]]
) -> tuple[float, float]:
    """Unit normal at ring vertex `index` pointing away from the island."""
    count = len(points)
    prev_pt = points[(index - 1) % count]
    next_pt = points[(index + 1) % count]
    px, py = points[index]
    normals = []
    for other in (prev_pt, next_pt):
        dx, dy = px - other[0], py - other[1]
        length = math.hypot(dx, dy) or 1.0
        normals.append((dx / length, dy / length))
    nx = sum(n[0] for n in normals)
    ny = sum(n[1] for n in normals)
    length = math.hypot(nx, ny) or 1.0
    nx, ny = nx / length, ny / length
    probe = (px + nx * 8.0, py + ny * 8.0)
    if point_in_polygon(probe, polygon):
        return -nx, -ny
    return nx, ny


# --------------------------------------------------------------------------
# Deterministic pseudo-texture (review pass): integer hashes stand in for RNG.
# splitmix64-style mixing over 64-bit ints — platform-stable, no RNG/clock.
# --------------------------------------------------------------------------
_HASH_MASK = 0xFFFFFFFFFFFFFFFF


def _hash2(a: int, b: int, salt: int) -> int:
    h = ((a & 0xFFFFFFFF) * 0x9E3779B97F4A7C15) & _HASH_MASK
    h ^= (b & 0xFFFFFFFF) * 0xC2B2AE3D27D4EB4F
    h = (h ^ ((h >> 30) * 0xBF58476D1CE4E5B9)) & _HASH_MASK
    h ^= (salt & 0xFFFFFFFF) * 0x165667B19E3779F9
    h = (h ^ ((h >> 27) * 0x94D049BB133111EB)) & _HASH_MASK
    h ^= h >> 31
    return h


def _unit(a: int, b: int, salt: int) -> float:
    """Deterministic uniform float in [0, 1)."""
    return _hash2(a, b, salt) / float(0xFFFFFFFFFFFFFFFF)


def _rot_rect_corners(
    cx: float, cy: float, angle: float, w: float, h: float
) -> list[tuple[float, float]]:
    ca, sa = math.cos(angle), math.sin(angle)
    hw, hh = w / 2.0, h / 2.0
    return [
        (cx + ca * dx - sa * dy, cy + sa * dx + ca * dy)
        for dx, dy in ((hw, hh), (-hw, hh), (-hw, -hh), (hw, -hh))
    ]


def _blob_polygon(
    cx: float, cy: float, rx: float, ry: float, rot: float, n: int = 12
) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for i in range(n):
        t = 2.0 * math.pi * i / n
        ex, ey = rx * math.cos(t), ry * math.sin(t)
        pts.append((cx + ex * math.cos(rot) - ey * math.sin(rot),
                    cy + ex * math.sin(rot) + ey * math.cos(rot)))
    return pts


def _dashed_polyline(
    draw: ImageDraw.ImageDraw,
    pts: list[tuple[float, float]],
    dash_px: float,
    gap_px: float,
    fill: tuple[int, int, int, int],
    width: int,
) -> None:
    """Walk the polyline by arc length drawing on/off dashes (footpath look)."""
    dense: list[tuple[float, float]] = []
    for (x1, y1), (x2, y2) in zip(pts[:-1], pts[1:]):
        steps = max(2, int(math.hypot(x2 - x1, y2 - y1) / 2.0))
        for i in range(steps):
            t = i / steps
            dense.append((x1 + (x2 - x1) * t, y1 + (y2 - y1) * t))
    dense.append(pts[-1])
    on = True
    phase = 0.0
    run: list[tuple[float, float]] = [dense[0]]
    for prev, cur in zip(dense[:-1], dense[1:]):
        step = math.hypot(cur[0] - prev[0], cur[1] - prev[1])
        phase += step
        if phase >= (dash_px if on else gap_px):
            phase = 0.0
            if on:
                run.append(cur)
                if len(run) > 1:
                    draw.line(run, fill=fill, width=width)
                run = [cur]
            else:
                run = [cur]
            on = not on
        elif on:
            run.append(cur)
    if on and len(run) > 1:
        draw.line(run, fill=fill, width=width)


def place_locations(
    points_px: list[tuple[float, float]], pier_px: tuple[float, float]
) -> tuple[dict[str, tuple[float, float]], bool]:
    """Choose houses.origin so the seed grid lands inside the island.

    Settlement targets the polygon centroid; the remaining POIs ride the
    seed_world grid cells relative to houses.origin. If the centroid-anchored
    cluster misses the island, a deterministic spiral search shifts the origin.
    """
    band = visible_x_band()
    band_px = (band[1] - band[0]) * IMAGE_W
    anchor_margin = BAND_MARGIN_FRAC * band_px

    def cluster_from(origin_container: tuple[float, float]) -> dict[str, tuple[float, float]]:
        origin_img = container_pct_to_image(*origin_container)
        out: dict[str, tuple[float, float]] = {}
        for name, (col, row) in POI_CELLS.items():
            dx = col * GRID_COL_STEP / 100.0 * band_px
            dy = row * GRID_ROW_STEP / 100.0 * IMAGE_H
            out[name] = (origin_img[0] + dx, origin_img[1] + dy)
        out["houses.origin"] = origin_img
        return out

    def valid(placement: dict[str, tuple[float, float]]) -> bool:
        for name, (x_px, y_px) in placement.items():
            if not (band[0] * IMAGE_W + anchor_margin <= x_px <= band[1] * IMAGE_W - anchor_margin):
                return False
            if not (0.0 <= y_px <= IMAGE_H):
                return False
            if name == "houses.origin":
                continue  # a grid corner, validated via its cells below
            if dist_to_polygon_boundary((x_px, y_px), points_px) < POI_INLAND_MIN_PX:
                return False
        for col, row in HOUSE_CELLS:
            hx = placement["houses.origin"][0] + col * GRID_COL_STEP / 100.0 * band_px
            hy = placement["houses.origin"][1] + row * GRID_ROW_STEP / 100.0 * IMAGE_H
            if not point_in_polygon((hx, hy), points_px):
                return False
            if not (band[0] * IMAGE_W + anchor_margin <= hx <= band[1] * IMAGE_W - anchor_margin):
                return False
        return True

    centroid_img = polygon_centroid(points_px)
    settlement_container = image_to_container_pct(*centroid_img)
    origin0 = (settlement_container[0] - POI_CELLS["settlement"][0] * GRID_COL_STEP,
               settlement_container[1] - POI_CELLS["settlement"][1] * GRID_ROW_STEP)

    placement = cluster_from(origin0)
    if valid(placement):
        return placement, False

    # Deterministic spiral search around the centroid-anchored origin.
    for step in range(1, 15):
        radius = step * 1.5
        for j in range(16):
            angle = -math.pi / 2.0 + 2.0 * math.pi * j / 16.0
            candidate = (origin0[0] + radius * math.cos(angle),
                         origin0[1] + radius * math.sin(angle))
            placement = cluster_from(candidate)
            if valid(placement):
                return placement, True
    raise SystemExit("no deterministic placement satisfied the island/band constraints")


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def render_island(
    points_px: list[tuple[float, float]],
    placement: dict[str, tuple[float, float]],
    pier_px: tuple[float, float],
    pier_index: int,
    px_per_m: float,
) -> Image.Image:
    canvas = Image.new("RGB", (IMAGE_W, IMAGE_H), DEEP_SEA)
    count = len(points_px)
    segments = [
        (points_px[i][0], points_px[i][1],
         points_px[(i + 1) % count][0], points_px[(i + 1) % count][1])
        for i in range(count)
    ]

    # Arc-length parameter of the coastline (organic shelf modulation, F1).
    arc = [0.0]
    for i in range(count):
        x1, y1 = points_px[i]
        x2, y2 = points_px[(i + 1) % count]
        arc.append(arc[-1] + math.hypot(x2 - x1, y2 - y1))
    arc_total = arc[-1] or 1.0

    def band_radii(
        base_m: float, amp: float, cycles: float, phase: float, harm: float
    ) -> list[float]:
        radii: list[float] = []
        for i in range(count):
            mid_arc = arc[i] + 0.5 * (arc[i + 1] - arc[i])
            phi = 2.0 * math.pi * mid_arc / arc_total
            wave = amp * math.sin(cycles * phi + phase) + harm * math.sin(2.7 * phi + phase * 1.7)
            radii.append(max(12.0, base_m * (1.0 + wave)) * px_per_m)
        return radii

    def paint_capsule_mask(radii: list[float]) -> Image.Image:
        mask = Image.new("L", (IMAGE_W, IMAGE_H), 0)
        for row in range(IMAGE_H):
            paint_spans(mask, row, _segment_intervals_radii(segments, row + 0.5, radii))
        return mask

    # Island pixel extents (used by the hashed clump/ridge grids below).
    ix_min = min(p[0] for p in points_px)
    ix_max = max(p[0] for p in points_px)
    iy_min = min(p[1] for p in points_px)
    iy_max = max(p[1] for p in points_px)

    # Sea: calm flat deep water with a tight organically-varying shelf band
    # hugging the coast plus one faint outer edge (review G1).
    prev_radii: list[float] | None = None
    for (base_m, amp, cycles, phase, harm), color in zip(SHELF_BANDS, SHELF_BAND_COLORS):
        radii = band_radii(base_m, amp, cycles, phase, harm)
        if prev_radii is not None:
            # keep band edges monotonic (outer > inner) pointwise
            radii = [max(r, p + 40.0) for r, p in zip(radii, prev_radii)]
        band_mask = paint_capsule_mask(radii)
        band_mask = band_mask.filter(ImageFilter.GaussianBlur(3.0))
        canvas.paste(Image.new("RGB", (IMAGE_W, IMAGE_H), color), (0, 0), band_mask)
        prev_radii = radii

    # Small hashed shoal accents inside the near band (breaks chart regularity).
    shoal_centers: list[tuple[float, float, float]] = []
    used: set[int] = set()
    for k in range(24):
        if len(shoal_centers) >= 5:
            break
        idx = _hash2(k, 5, SALT_SHOAL) % count
        if any(abs(idx - u) < 40 or abs(idx - u) > count - 40 for u in used):
            continue
        used.add(idx)
        nx_pt, ny_pt = points_px[idx]
        nrm = outward_normal_at(idx, points_px, points_px)
        off_m = 40.0 + 70.0 * _unit(k, 6, SALT_SHOAL)
        shoal_centers.append((nx_pt + nrm[0] * off_m * px_per_m,
                              ny_pt + nrm[1] * off_m * px_per_m,
                              (22.0 + 30.0 * _unit(k, 7, SALT_SHOAL)) * px_per_m))
    shoal_mask = Image.new("L", (IMAGE_W, IMAGE_H), 0)
    shoal_draw = ImageDraw.Draw(shoal_mask)
    for cx, cy, cr in shoal_centers:
        shoal_draw.ellipse([cx - cr, cy - cr * 0.62, cx + cr, cy + cr * 0.62], fill=200)
    shoal_mask = shoal_mask.filter(ImageFilter.GaussianBlur(6.0))
    canvas.paste(Image.new("RGB", (IMAGE_W, IMAGE_H), SHOAL), (0, 0), shoal_mask)

    # Land base (exact OSM coastline).
    draw = ImageDraw.Draw(canvas)
    draw.polygon(points_px, fill=LAND_MEADOW)

    # Soft land tones from eroded-distance masks: beach rim, forest, ridge.
    def eroded_mask(radius_m: float, inside: bool, outside: bool) -> Image.Image:
        radius_px = radius_m * px_per_m
        mask = Image.new("L", (IMAGE_W, IMAGE_H), 0)
        for row in range(IMAGE_H):
            spans: list[tuple[float, float]] = []
            if outside:
                spans = _segment_intervals(segments, row + 0.5, radius_px)
            if inside:
                insides = _polygon_inside_intervals(points_px, row + 0.5)
                if outside:
                    spans = _intersect_spans(insides, spans)
                else:
                    spans = _subtract_spans(
                        insides, _segment_intervals(segments, row + 0.5, radius_px)
                    )
            paint_spans(mask, row, spans)
        return mask

    beach_radii = band_radii(BEACH_M, 0.28, 6.0, 1.3, 0.14)
    beach_mask = Image.new("L", (IMAGE_W, IMAGE_H), 0)
    for row in range(IMAGE_H):
        insides = _polygon_inside_intervals(points_px, row + 0.5)
        paint_spans(beach_mask, row, _intersect_spans(
            insides, _segment_intervals_radii(segments, row + 0.5, beach_radii)))
    beach_mask = beach_mask.filter(ImageFilter.GaussianBlur(2.0))
    canvas.paste(Image.new("RGB", (IMAGE_W, IMAGE_H), BEACH), (0, 0), beach_mask)

    # Feathered surf line straddling the waterline (review G4: matches the
    # soft shelf instead of a crisp 1-px line against a smooth halo).
    surf_radii = band_radii(SURF_EDGE_M, 0.30, 6.0, 2.6, 0.14)
    surf_mask = paint_capsule_mask(surf_radii)
    surf_mask = surf_mask.filter(ImageFilter.GaussianBlur(1.2))
    canvas.paste(Image.new("RGB", (IMAGE_W, IMAGE_H), SURF), (0, 0), surf_mask)

    forest_mask = eroded_mask(FOREST_M, True, False)
    forest_mask = forest_mask.filter(ImageFilter.GaussianBlur(8.0))
    canvas.paste(Image.new("RGB", (IMAGE_W, IMAGE_H), FOREST), (0, 0), forest_mask)

    ridge_mask = eroded_mask(RIDGE_M, True, False)
    ridge_mask = ridge_mask.filter(ImageFilter.GaussianBlur(14.0))
    canvas.paste(Image.new("RGB", (IMAGE_W, IMAGE_H), RIDGE), (0, 0), ridge_mask)

    # Village meadow clearing around the settlement grid center.
    grid_center = (
        placement["houses.origin"][0] + 3.5 * GRID_COL_STEP / 100.0 * (
            visible_x_band()[1] - visible_x_band()[0]) * IMAGE_W,
        placement["houses.origin"][1] + 1.5 * GRID_ROW_STEP / 100.0 * IMAGE_H,
    )
    meadow = Image.new("L", (IMAGE_W, IMAGE_H), 0)
    meadow_draw = ImageDraw.Draw(meadow)
    rx = 430.0 * px_per_m
    ry = 260.0 * px_per_m
    meadow_draw.ellipse(
        [grid_center[0] - rx, grid_center[1] - ry, grid_center[0] + rx, grid_center[1] + ry],
        fill=205,
    )
    meadow = meadow.filter(ImageFilter.GaussianBlur(12.0))
    canvas.paste(Image.new("RGB", (IMAGE_W, IMAGE_H), VILLAGE_MEADOW), (0, 0), meadow)

    # Hashed forest clumps: two tones, sparse near the village (review F2/F5).
    settle = placement["settlement"]
    cell_m = 92.0
    cols = int((ix_max - ix_min) / (cell_m * px_per_m)) + 3
    rows = int((iy_max - iy_min) / (cell_m * px_per_m)) + 3
    clump_a = Image.new("L", (IMAGE_W, IMAGE_H), 0)
    clump_b = Image.new("L", (IMAGE_W, IMAGE_H), 0)
    ca_draw = ImageDraw.Draw(clump_a)
    cb_draw = ImageDraw.Draw(clump_b)
    for gx in range(cols):
        for gy in range(rows):
            h = _hash2(gx + 101, gy + 317, SALT_FOREST)
            cx = ix_min + (gx + _unit(gx, gy * 4 + 1, SALT_FOREST)) * cell_m * px_per_m
            cy = iy_min + (gy + _unit(gx * 4 + 3, gy, SALT_FOREST)) * cell_m * px_per_m
            dist_village_m = math.hypot(cx - settle[0], cy - settle[1]) / px_per_m
            if dist_village_m < 240.0:
                prob = 0.05
            elif dist_village_m < 420.0:
                prob = 0.16
            else:
                prob = 0.28
            if _unit(gx, gy, SALT_FOREST) >= prob:
                continue
            if not point_in_polygon((cx, cy), points_px):
                continue
            if dist_to_polygon_boundary((cx, cy), points_px) < 55.0 * px_per_m:
                continue
            rx = (30.0 + 34.0 * _unit(gx, gy, SALT_FOREST + 1)) * px_per_m
            ry = rx * (0.55 + 0.45 * _unit(gx, gy, SALT_FOREST + 2))
            rot = _unit(gx, gy, SALT_FOREST + 3) * 2.0 * math.pi
            blob = _blob_polygon(cx, cy, rx, ry, rot)
            (ca_draw if h % 2 == 0 else cb_draw).polygon(blob, fill=210)
    clump_a = clump_a.filter(ImageFilter.GaussianBlur(2.5))
    clump_b = clump_b.filter(ImageFilter.GaussianBlur(2.5))
    canvas.paste(Image.new("RGB", (IMAGE_W, IMAGE_H), FOREST_CLUMP_A), (0, 0), clump_a)
    canvas.paste(Image.new("RGB", (IMAGE_W, IMAGE_H), FOREST_CLUMP_B), (0, 0), clump_b)

    # Ridge shading: elongated hashed blobs along the island spine (relief hint).
    spine = -0.65
    ridge_cell = 150.0
    ridge_blobs = Image.new("L", (IMAGE_W, IMAGE_H), 0)
    rb_draw = ImageDraw.Draw(ridge_blobs)
    rcols = int((ix_max - ix_min) / (ridge_cell * px_per_m)) + 2
    rrows = int((iy_max - iy_min) / (ridge_cell * px_per_m)) + 2
    for gx in range(rcols):
        for gy in range(rrows):
            if _unit(gx + 57, gy + 91, SALT_RIDGE) > 0.55:
                continue
            cx = ix_min + (gx + _unit(gx, gy, SALT_RIDGE)) * ridge_cell * px_per_m
            cy = iy_min + (gy + _unit(gx, gy, SALT_RIDGE + 1)) * ridge_cell * px_per_m
            if dist_to_polygon_boundary((cx, cy), points_px) < 330.0 * px_per_m:
                continue
            rx = (46.0 + 42.0 * _unit(gx, gy, SALT_RIDGE + 2)) * px_per_m
            ry = rx * (0.20 + 0.10 * _unit(gx, gy, SALT_RIDGE + 3))
            rot = spine + (_unit(gx, gy, SALT_RIDGE + 3) - 0.5) * 0.5
            rb_draw.polygon(_blob_polygon(cx, cy, rx, ry, rot, n=10), fill=220)
    ridge_blobs = ImageChops.darker(ridge_blobs.filter(ImageFilter.GaussianBlur(3.5)), ridge_mask)
    canvas.paste(Image.new("RGB", (IMAGE_W, IMAGE_H), RIDGE_DEEP), (0, 0), ridge_blobs)

    # Faint deterministic grain so flat areas are not flat (review F2).
    grain_small = Image.new("L", (96, 54))
    for gy in range(54):
        for gx in range(96):
            grain_small.putpixel((gx, gy), _hash2(gx + 13, gy + 57, SALT_GRAIN) & 0xFF)
    grain = grain_small.resize((IMAGE_W, IMAGE_H), Image.Resampling.BICUBIC)
    land_mask = Image.new("L", (IMAGE_W, IMAGE_H), 0)
    ImageDraw.Draw(land_mask).polygon(points_px, fill=255)

    def grain_dark(v: int) -> int:
        return max(0, min(255, int((v - 158) * 0.65)))

    def grain_light(v: int) -> int:
        return max(0, min(255, int((112 - v) * 0.65)))

    dark_grain = ImageChops.darker(grain.point(grain_dark), land_mask)
    light_grain = ImageChops.darker(grain.point(grain_light), land_mask)
    canvas.paste(Image.new("RGB", (IMAGE_W, IMAGE_H), LAND_SHADOW), (0, 0), dark_grain)
    canvas.paste(Image.new("RGB", (IMAGE_W, IMAGE_H), LAND_LIGHT), (0, 0), light_grain)

    # ------------------------------------------------------------------
    # Overlay: footpath trail, dock, village (RGBA, deterministic, G2-G4).
    # ------------------------------------------------------------------
    overlay = Image.new("RGBA", (IMAGE_W, IMAGE_H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)

    pier_normal = outward_normal_at(pier_index, points_px, points_px)
    dock_angle = math.atan2(pier_normal[1], pier_normal[0])
    dock_start = (
        pier_px[0] - pier_normal[0] * 14.0 * px_per_m,
        pier_px[1] - pier_normal[1] * 14.0 * px_per_m,
    )
    dock_end = (
        pier_px[0] + pier_normal[0] * 96.0 * px_per_m,
        pier_px[1] + pier_normal[1] * 96.0 * px_per_m,
    )
    dock_mid = ((dock_start[0] + dock_end[0]) / 2.0, (dock_start[1] + dock_end[1]) / 2.0)

    # Footpath: gentle deterministic curve settlement -> dock, dashed (G6).
    trail_dx = dock_start[0] - settle[0]
    trail_dy = dock_start[1] - settle[1]
    trail_len = math.hypot(trail_dx, trail_dy) or 1.0
    perp = (-trail_dy / trail_len, trail_dx / trail_len)
    h_sign = 1.0 if _hash2(3, 8, SALT_TRAIL) % 2 == 0 else -1.0
    p1 = (
        settle[0] + trail_dx * 0.33 + perp[0] * trail_len * 0.07 * h_sign,
        settle[1] + trail_dy * 0.33 + perp[1] * trail_len * 0.07 * h_sign,
    )
    p2 = (
        settle[0] + trail_dx * 0.68 - perp[0] * trail_len * 0.05 * h_sign,
        settle[1] + trail_dy * 0.68 - perp[1] * trail_len * 0.05 * h_sign,
    )
    _dashed_polyline(
        od, [settle, p1, p2, dock_start],
        dash_px=46.0 * px_per_m, gap_px=16.0 * px_per_m,
        fill=TRAIL + (215,), width=3,
    )

    # Dock at the pier: chunky planked shaft + wide landing pad (review G3).
    shaft_len_m = 110.0  # 14 m on land + ~96 m into open water
    shaft = _rot_rect_corners(dock_mid[0], dock_mid[1], dock_angle,
                              16.0 * px_per_m + 2.2, shaft_len_m * px_per_m + 2.2)
    od.polygon(shaft, fill=PIER_OUTLINE + (255,))
    shaft = _rot_rect_corners(dock_mid[0], dock_mid[1], dock_angle,
                              16.0 * px_per_m, shaft_len_m * px_per_m)
    od.polygon(shaft, fill=PIER_WOOD + (255,))
    pad = _rot_rect_corners(dock_end[0], dock_end[1], dock_angle,
                            30.0 * px_per_m, 24.0 * px_per_m)
    od.polygon(pad, fill=PIER_OUTLINE + (255,))
    pad = _rot_rect_corners(dock_end[0], dock_end[1], dock_angle,
                            27.0 * px_per_m, 21.0 * px_per_m)
    od.polygon(pad, fill=PIER_WOOD + (255,))
    od.line([dock_start, dock_end], fill=PIER_OUTLINE + (160,), width=1)

    # Compact village impression around the settlement anchor (review G3):
    # fewer, chunkier structures with light wall fringes and dark roof lines,
    # snapped near the seed-grid positions.
    WALL = (218, 204, 176)

    def draw_building(bx: float, by: float, bang: float, bw: float, bd: float,
                      roof: tuple[int, int, int], ridge_w: int) -> None:
        wall = _rot_rect_corners(bx, by, bang, bw * 1.18, bd * 1.18)
        od.polygon(wall, fill=WALL + (255,))
        od.polygon(_rot_rect_corners(bx, by, bang, bw, bd), fill=roof + (255,))
        ridge_dir = (math.cos(bang) * bw * 0.5, math.sin(bang) * bw * 0.5)
        od.line([(bx - ridge_dir[0], by - ridge_dir[1]),
                 (bx + ridge_dir[0], by + ridge_dir[1])],
                fill=ROOF_LINE + (255,), width=ridge_w)

    village_po = ["settlement", "shop", "workshop", "kitchen", "storage", "home"]
    for vi, name in enumerate(village_po):
        bx, by = placement[name]
        bx += (_unit(vi, 17, SALT_VILLAGE) - 0.5) * 40.0 * px_per_m
        by += (_unit(vi, 23, SALT_VILLAGE) - 0.5) * 40.0 * px_per_m
        bw = (58.0 + 26.0 * _unit(vi, 29, SALT_VILLAGE)) * px_per_m
        bd = (36.0 + 16.0 * _unit(vi, 31, SALT_VILLAGE)) * px_per_m
        bang = (_unit(vi, 37, SALT_VILLAGE) - 0.5) * 0.9
        tone = POI_ROOF if name in ("settlement", "shop") else VILLAGE_ROOF
        draw_building(bx, by, bang, bw, bd, tone, 2)
    for k in range(3):
        anchor_name = ("shop", "storage", "home")[k]
        bx, by = placement[anchor_name]
        ang = _unit(k, 41, SALT_VILLAGE) * 2.0 * math.pi
        off = (55.0 + 60.0 * _unit(k, 37, SALT_VILLAGE)) * px_per_m
        bx += math.cos(ang) * off
        by += math.sin(ang) * off
        if dist_to_polygon_boundary((bx, by), points_px) < 18.0 * px_per_m:
            continue
        bw = (38.0 + 20.0 * _unit(k, 41, SALT_VILLAGE)) * px_per_m
        bd = (26.0 + 12.0 * _unit(k, 41, SALT_VILLAGE + 1)) * px_per_m
        bang = (_unit(k, 41, SALT_VILLAGE + 2) - 0.5) * 1.2
        draw_building(bx, by, bang, bw, bd, VILLAGE_ROOF, 1)
    well_x, well_y = placement["well"]
    well_r = 9.0 * px_per_m
    od.ellipse([well_x - well_r, well_y - well_r, well_x + well_r, well_y + well_r],
               fill=ROOF_LINE + (255,))
    well_ir = 6.0 * px_per_m
    od.ellipse([well_x - well_ir, well_y - well_ir, well_x + well_ir, well_y + well_ir],
               fill=(170, 174, 180) + (255,))

    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")

    # Baked ODbL credit, centered inside the client-visible x-band near the
    # bottom edge (object-fit: cover can never crop it out of the card view).
    band = visible_x_band()
    credit_font = ImageFont.load_default(size=CREDIT_SIZE_PX)
    cb = credit_font.getbbox(CREDIT_TEXT)
    credit_x = (band[0] + band[1]) / 2.0 * IMAGE_W - (cb[0] + cb[2]) / 2.0
    credit_y = IMAGE_H - CREDIT_MARGIN_PX - cb[3]
    ImageDraw.Draw(canvas).text((credit_x, credit_y), CREDIT_TEXT,
                                font=credit_font, fill=CREDIT_COLOR)
    return canvas


# --------------------------------------------------------------------------
# Outputs + report
# --------------------------------------------------------------------------
def main() -> None:
    timestamp, digest = load_and_verify_snapshot()
    nodes, ring = parse_osm(SNAPSHOT_PATH)

    lats = [nodes[node_id][1] for node_id in ring]
    lons = [nodes[node_id][0] for node_id in ring]
    lat0, lon0, m_per_deg_lat, m_per_deg_lon = local_projection_constants(lats, lons)
    bbox = [round(min(lats), 7), round(min(lons), 7), round(max(lats), 7), round(max(lons), 7)]

    data_points = [
        ((lon - lon0) * m_per_deg_lon, (lat - lat0) * m_per_deg_lat)
        for lon, lat in (nodes[node_id] for node_id in ring)
    ]
    data_xs = [p[0] for p in data_points]
    data_ys = [p[1] for p in data_points]
    island_w_m = max(data_xs) - min(data_xs)
    island_h_m = max(data_ys) - min(data_ys)

    band = visible_x_band()
    band_px = (band[1] - band[0]) * IMAGE_W
    scale = min(ISLAND_WIDTH_FRAC * band_px / island_w_m,
                (IMAGE_H - 2.0 * V_MARGIN_PX) / island_h_m)
    px_per_m = scale
    center_x = IMAGE_W / 2.0
    center_y = IMAGE_H / 2.0
    mid_x = (max(data_xs) + min(data_xs)) / 2.0
    mid_y = (max(data_ys) + min(data_ys)) / 2.0

    def to_image(data_point: tuple[float, float]) -> tuple[float, float]:
        return (center_x + (data_point[0] - mid_x) * scale,
                center_y - (data_point[1] - mid_y) * scale)

    points_px = [to_image(p) for p in data_points]

    pier_node = pier_vertex(nodes, ring)
    pier_index = ring.index(pier_node)
    pier_px = points_px[pier_index]

    placement, shifted = place_locations(points_px, pier_px)
    placement["pier"] = pier_px

    canvas = render_island(points_px, placement, pier_px, pier_index, px_per_m)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    png_path = OUT_DIR / "base.png"
    jpg_path = OUT_DIR / "world-island.jpg"
    coords_path = OUT_DIR / "coords.json"

    canvas.save(png_path, format="PNG")

    # Byte-identical re-encode ladder: try q87 first, step down only if the
    # pinned 1 MB ceiling (tests/m21/test_alpha_a1.py) would be violated.
    re_encoded = Image.open(png_path)
    jpeg_bytes = None
    used_quality = 87
    for quality in (87, 85, 83, 80, 75, 70):
        candidate = _encode_jpeg(re_encoded, quality)
        if len(candidate) < 1_000_000:
            jpeg_bytes = candidate
            used_quality = quality
            break
    if jpeg_bytes is None:
        raise SystemExit("world-island.jpg exceeds the 1 MB ceiling even at q70")
    jpg_path.write_bytes(jpeg_bytes)

    _validate_anchors(placement, points_px, pier_px, band_px)

    locations: dict[str, dict[str, float]] = {}
    image_space: dict[str, dict[str, float]] = {}
    for name in LOCATION_ORDER + ["houses.origin"]:
        x_px, y_px = placement[name]
        pct_x, pct_y = image_to_container_pct(x_px, y_px)
        locations[name] = {"x": round(pct_x, 2), "y": round(pct_y, 2)}
        image_space[name] = {"x": round(x_px / IMAGE_W * 100.0, 2),
                             "y": round(y_px / IMAGE_H * 100.0, 2)}

    coords = {
        "image": "base.png",
        "size": [IMAGE_W, IMAGE_H],
        "locations": locations,
        "osm": {
            "bbox": bbox,
            "snapshot_sha256": digest,
            "fetched_from": "overpass-api.de/api/interpreter",
            "fetched_at": timestamp,
        },
        "image_space": image_space,
    }
    coords_path.write_text(json.dumps(coords, indent=2) + "\n", encoding="utf-8")

    _print_report(nodes, ring, bbox, island_w_m, island_h_m, scale, band_px,
                  placement, pier_node, pier_px, points_px, shifted, used_quality,
                  png_path, jpg_path, coords_path)


def _encode_jpeg(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def _validate_anchors(
    placement: dict[str, tuple[float, float]],
    points_px: list[tuple[float, float]],
    pier_px: tuple[float, float],
    band_px: float,
) -> None:
    """Acceptance asserts: anchors in the visible band, POIs inland, pier on coast."""
    band = visible_x_band()
    margin = BAND_MARGIN_FRAC * band_px
    for name, (x_px, y_px) in placement.items():
        if not (band[0] * IMAGE_W + margin <= x_px <= band[1] * IMAGE_W - margin):
            raise SystemExit(f"anchor {name} outside the visible x-band: {x_px:.1f}")
        if not (0.0 <= y_px <= IMAGE_H):
            raise SystemExit(f"anchor {name} outside the canvas vertically: {y_px:.1f}")
    for name in POI_CELLS:
        x_px, y_px = placement[name]
        if not point_in_polygon((x_px, y_px), points_px):
            raise SystemExit(f"POI {name} fell outside the island polygon")
    pier_dist = dist_to_polygon_boundary(pier_px, points_px)
    if pier_dist > PIER_COAST_TOL_PX:
        raise SystemExit(f"pier is {pier_dist:.2f} px from the coastline")


def _print_report(
    nodes: dict[int, tuple[float, float]],
    ring: list[int],
    bbox: list[float],
    island_w_m: float,
    island_h_m: float,
    scale: float,
    band_px: float,
    placement: dict[str, tuple[float, float]],
    pier_node: int,
    pier_px: tuple[float, float],
    points_px: list[tuple[float, float]],
    shifted: bool,
    used_quality: int,
    png_path: Path,
    jpg_path: Path,
    coords_path: Path,
) -> None:
    print("== Reineke Island map render (issue #95) ==")
    print(f"snapshot sha256 verified: {hashlib.sha256(SNAPSHOT_PATH.read_bytes()).hexdigest()}")
    print(f"ring nodes: {len(ring)}  bbox (s,w,n,e): {bbox}")
    print(f"island: {island_w_m:.0f} x {island_h_m:.0f} m  scale: {scale:.5f} px/m")
    band = visible_x_band()
    print(f"visible x-band: {band[0] * 100:.2f}% .. {band[1] * 100:.2f}% of the raw image")
    pier_lat, pier_lon = nodes[pier_node]
    print(f"pier: node {pier_node} ({pier_lat:.7f}, {pier_lon:.7f}) on coastline "
          f"(dist {dist_to_polygon_boundary(pier_px, points_px):.2f} px)")
    print(f"placement: {'spiral-searched' if shifted else 'centroid-anchored'}")
    for name in LOCATION_ORDER:
        x_px, y_px = placement[name]
        pct_x, pct_y = image_to_container_pct(x_px, y_px)
        dist_m = dist_to_polygon_boundary((x_px, y_px), points_px) / scale
        print(f"  {name:14s} container=({pct_x:6.2f}, {pct_y:6.2f})%  "
              f"image=({x_px:7.1f}, {y_px:7.1f})px  dist-to-coast={dist_m:7.1f} m")
    for name in ("houses.origin",):
        x_px, y_px = placement[name]
        pct_x, pct_y = image_to_container_pct(x_px, y_px)
        print(f"  {name:14s} container=({pct_x:6.2f}, {pct_y:6.2f})%  "
              f"image=({x_px:7.1f}, {y_px:7.1f})px  (house-grid anchor)")
    print(f"outputs: {png_path} ({png_path.stat().st_size} B), "
          f"{jpg_path} ({jpg_path.stat().st_size} B, q{used_quality}), {coords_path}")
    print("config/default.yaml locations.coords values for this run:")
    for name in LOCATION_ORDER + ["houses.origin"]:
        pct_x, pct_y = image_to_container_pct(*placement[name])
        print(f"  {name}: {{ x: {round(pct_x, 2)}, y: {round(pct_y, 2)} }}")


if __name__ == "__main__":
    main()
