# Reineke Island map pipeline (issue #95)

Deterministic, off-box pipeline that renders the base map of the game world
from a pinned OpenStreetMap snapshot of **Reineke Island** (Vladivostok area,
Japan Sea — OSM relation **1933752**). Pure Python stdlib + Pillow; no network,
no clock, no RNG in the render path.

Style contract: `docs/VISUAL-BIBLE.md` §28–30 (rich top-down geographic map;
real coastline; whole island in frame; readability first).

## Provenance

| Field | Value |
|---|---|
| Snapshot | `scripts/map/input/reineke-island.osm` (75 818 bytes) |
| Snapshot sha256 | `776cacbd716ae5e9fc0b0ddb336f9f1e869de2b5c0ecab908f48ef4dc5980f0b` |
| OSM relation | 1933752 (`place=island`, `type=multipolygon`, 2 outer `natural=coastline` ways: 22530040 + 939633611) |
| Fetched from | `overpass-api.de/api/interpreter` |
| Overpass query | `[out:xml][timeout:50];(relation(1933752);way(r);node(w););out body;` |
| OSM data timestamp | `2026-10-25T12:01:18Z` |
| License | ODbL — © OpenStreetMap contributors |

License links: [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/)
(Open Data Commons Open Database License) and the
[OSM copyright page](https://www.openstreetmap.org/copyright) (also mirrored in
`scripts/map/input/manifest.json` → `license_links`). The attribution is baked
into the rendered map itself: a small gray "© OpenStreetMap contributors" line
in the sea near the bottom edge, centered inside the client-visible x-band, so
it is visible to map users in the served JPEG (and must be preserved by any
future re-render or asset swap).

The provenance lives in `scripts/map/input/manifest.json`. The renderer **reads
the sha256 from the manifest and verifies it against the snapshot file on every
run**; a mismatch aborts the render loudly. If you ever re-fetch data, replace
the snapshot, recompute the hash, and update `manifest.json` — the renderer
never trusts an unverified file.

## How to run

From the repo root:

```bash
uv run python scripts/map/render_map.py
```

One run produces all three outputs:

- `backend/app/web/static/map/base.png` — 1600×900 RGB master render.
- `backend/app/web/static/map/world-island.jpg` — deterministic JPEG re-encode
  of `base.png` (quality 87; the encoder only steps down if the size ceiling
  were violated). This is the file the client serves.
- `backend/app/web/static/map/coords.json` — anchor coordinates + OSM
  provenance (schema below).

It also prints a placement report with the nine `locations.coords` values to
paste into `config/default.yaml`.

### Render style (visual-bible "Rich Top-Down", review round 2)

The paint pass is artistic, not chart-like (per docs/VISUAL-BIBLE.md §28) and
the sea stays calm: flat dark-teal open water, a tight organically-varying
shelf band hugging the coast (~130–230 m, sinusoidal modulation of the
distance-field threshold per segment) plus one faint outer edge and small
hashed shoal accents — no open-water blobs, no halo. The coast line is a
feathered ~3 px surf straddling the waterline instead of a crisp 1-px stroke.
Land is quiet: hashed forest clumps in two green tones with open grassland
between them (sparse near the village), elongated ridge shading along the
island spine, and faint hash-grain. The settlement is a compact village of a
few chunky roofed structures (light wall fringes, dark roof lines, a well with
dark rim) on a stronger meadow clearing — the 24 houses stay client-side
anchors, not drawn dots. A dashed curved footpath links the village to an
oversized planked dock perpendicular into open water at the true pier vertex.
A small gray "© OpenStreetMap contributors" credit (PIL built-in font, fixed
16 px, deterministic) is baked into the sea near the bottom edge, centered
inside the client-visible x-band — ODbL attribution visible to map users.
All "randomness" is splitmix-style integer hashing over fixed salts —
deterministic and byte-identical across runs.

### coords.json schema

```json
{
  "image": "base.png",
  "size": [1600, 900],
  "locations": { "<key>": {"x": <container %>, "y": <container %>} },
  "osm": {"bbox": [s, w, n, e], "snapshot_sha256": "...",
          "fetched_from": "overpass-api.de/api/interpreter",
          "fetched_at": "2026-10-25T12:01:18Z"},
  "image_space": { "<key>": {"x": <raw image %>, "y": <raw image %>} }
}
```

`locations` holds container-space percentages (the 742/616 `.map-card` box the
client anchors are positioned in, exactly what `config/default.yaml`
`locations.coords` stores); `image_space` records the raw image-space positions
for debug/provenance. Keys: `settlement, shop, workshop, kitchen, storage,
well, pier, home, houses.origin`.

The container↔image conversion is computed in `render_map.py` from the actual
image size and the `.map-card` aspect (742/616) with `object-fit: cover` center
crop — for 1600×900 the visible x-band is 16.12%–83.88% of the raw image and
the full height is visible. The renderer asserts that every anchor (the nine
POIs **and** all 24 house-grid cells) sits inside the visible band with margin,
POIs sit strictly inland, and the pier sits on the coastline.

## Determinism / byte-identity

- All OSM elements are sorted by id before use; the two outer coastline ways
  are stitched into one closed ring deterministically (sorted by way id).
- Placement is fully deterministic: pier = southernmost-then-westernmost
  coastline vertex; settlement = polygon area centroid (deterministic inland
  bisection fallback if a future shape pushes it outside); the other POIs ride
  the `seed_world` house-grid offsets so the client-side house anchors never
  overlap POI anchors; no RNG anywhere.
- **Claim:** re-running the renderer inside the pinned `uv.lock` environment
  produces byte-identical `base.png`, `world-island.jpg` and `coords.json`.
- **Limits:** byte-identity holds for the pinned Pillow version and fixed
  encode parameters. A Pillow upgrade, a different platform build of the JPEG
  encoder, or changed render constants will produce different (still valid)
  bytes — re-verify by running twice and comparing `sha256sum` outputs.

## Asset swap procedure

The three output files are committed in the repo; shipping a new map is a
routine static deploy (lane A — they are ordinary files under
`backend/app/web/static/`):

1. Replace `scripts/map/input/reineke-island.osm` and update `sha256` +
   `osm_data_timestamp` in `scripts/map/input/manifest.json`.
2. `uv run python scripts/map/render_map.py` (from repo root).
3. Copy the nine values from the placement report into
   `config/default.yaml` → `locations.coords` (they must match `coords.json`).
4. Inspect the PNG, commit the three regenerated outputs together with the
   config change.

## Prod DB note — already-seeded worlds

`seed_world` runs only on fresh world creation
(`backend/app/simulation/cli.py:99`); an already-seeded prod DB keeps the old
coordinates. The world DB is SQLite at `persistence.db_path`, table `locations`
(`world_id`, `name`, `type`, `x`, `y`).

**Paths — read this before running anything:**

- **Production** (`config/production.yaml:161`): `db_path: "data/lifesim.db"`
  (relative to the service working directory, `/opt/lifesim/data` on the VPS).
- **Dev** (`config/default.yaml:151`): `db_path: "lifesim.db"` (repo root).
- The snippet below **defaults to the dev DB** (`lifesim.db`) when called
  without an argument — for production you MUST pass the production path
  explicitly as `argv[1]`.

**Pre-flight (do not skip):**

1. Take a DB backup.
2. Check the DB file exists where you expect it:
   `ls -la /opt/lifesim/data/lifesim.db` (prod) — abort if it is missing or
   freshly created (that would mean wrong working directory).
3. **Confirm the target world before any UPDATE.** A DB may contain several
   worlds; `locations` rows are keyed by `world_id` (`worlds` table:
   `id`, `seed`, `start_real`, `config_sha256`):

   ```sql
   SELECT id, seed, start_real, config_sha256 FROM worlds;
   SELECT world_id, COUNT(*) FROM locations GROUP BY world_id;
   ```

   Use the `world_id` you confirmed in the `world_id` guard of the UPDATEs
   below.

The one-off snippet below is **idempotent** (every write is a pure function of
`coords.json` — safe to run any number of times), uses only the stdlib, changes
no schema and no app code. It updates the eight named location rows by name and
re-derives the 24 `House N` rows from the `seed_world` grid anchored at the new
`houses.origin`.

Run from the repo root (`uv run python -`), passing the production DB path:

```python
# One-off prod fix for issue #95 — idempotent, stdlib only.
# Usage: uv run python - <<'EOF'   (argv[1] = DB path; the default below is
#                                   the DEV database, not production!)
import json, sqlite3, sys
from pathlib import Path

# PRODUCTION: pass /opt/lifesim/data/lifesim.db (or the host's data/lifesim.db)
# as argv[1]. The fallback "lifesim.db" is the DEV database from
# config/default.yaml — do not touch prod with the default.
db_path = Path(sys.argv[1] if len(sys.argv) > 1 else "lifesim.db")
if not db_path.exists():
    raise SystemExit(f"DB file not found: {db_path} — check the path before "
                     "any UPDATE (prod default: data/lifesim.db)")
locs = json.loads(
    Path("backend/app/web/static/map/coords.json").read_text(encoding="utf-8")
)["locations"]

# config key -> locations.name (config/default.yaml locations.locations)
NAME_BY_KEY = {
    "settlement": "Main Settlement",
    "shop": "General Store",
    "workshop": "Workshop",
    "kitchen": "Community Kitchen",
    "storage": "Community Storage",
    "well": "Water Well",
    "pier": "Main Pier",
    "home": "Residential House",
}

# seed_world house grid (backend/app/world/seed_world.py): container-% steps
# and the fixed 24-cell list; House N sits at origin + col*3.9 / row*4.6.
GRID_COL_STEP, GRID_ROW_STEP = 3.9, 4.6
CELLS = [
    (0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (6, 0), (7, 0),
    (0, 1), (1, 1), (2, 1), (3, 1), (5, 1), (6, 1), (7, 1),
    (0, 2), (2, 2), (6, 2), (7, 2),
    (0, 3), (1, 3), (3, 3), (4, 3), (6, 3), (7, 3),
]
origin = locs["houses.origin"]

db = sqlite3.connect(db_path)
try:
    with db:  # one transaction
        for key, name in NAME_BY_KEY.items():
            cur = db.execute(
                "UPDATE locations SET x = ?, y = ? WHERE name = ?",
                (locs[key]["x"], locs[key]["y"], name),
            )
            print(f"{name}: {cur.rowcount} row(s) updated")
        for i, (col, row) in enumerate(CELLS, start=1):
            x = origin["x"] + col * GRID_COL_STEP
            y = origin["y"] + row * GRID_ROW_STEP
            cur = db.execute(
                "UPDATE locations SET x = ?, y = ? WHERE name = ?",
                (x, y, f"House {i}"),
            )
            print(f"House {i}: {cur.rowcount} row(s) updated")
finally:
    db.close()
EOF
```

Verify afterwards (names not found → `0 row(s)` lines deserve a look; multiple
worlds in one DB → add `AND world_id = ?` to the UPDATEs):

```sql
SELECT name, x, y FROM locations
WHERE name IN ('Main Settlement','General Store','Workshop','Community Storage',
               'Water Well','Main Pier','Community Kitchen','Residential House')
   OR name LIKE 'House %';
```
