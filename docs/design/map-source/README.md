# Owner-approved LOD2/LOD3 source PNGs — Issue #308

Owner visually approved both images on 2026-11-22 for implementation. Geographic alignment, roads/footprints and browser acceptance remain open.

Exact original PNG byte streams are tracked as binary parts in `source-parts/` due to the size limit of a single GitHub connector write. Restore them (no re-encoding):

```sh
python scripts/materialize_map_art.py
python -m pip install Pillow
python scripts/build_map_tiles.py --spec docs/design/map-tiles.json --output backend/app/web/static/map/tiles --root .
```

| Original | Size | Bounds in world master 1600x900 | SHA-256 |
|---|---|---|---|
| `lod2-region.png` | 1920x1080 | `[320,240,960,540]` | `6041950476364cb23aa75a5f1c50b95df96f82d6e7a301f400a844d1e75ce2ee` |
| `lod3-settlement.png` | 1920x960 | `[480,330,480,240]` | `cfb38fd58133ffdae41d06d63367d93f2440289944958ebeafcf2e8534a41f7e` |

Restoration checks original SHA-256, exact byte count, and PNG dimensions. Source PNG is not a lossy WebP preview. After rebuilding with existing `base.png`, expect 8 LOD1 + 12 LOD2 + 8 LOD3 raster WebP tiles (512px core, 2px bleed). Preview `tiles/manifest.json` has `lod1.available=false` until rebuild and must not be shipped unchanged.

Approving the concept does not verify POI-to-pixel transform, real roads/building footprints, LOD transitions, or performance. LOD3 derives landscape composition from LOD2 with an additional high-res detailing pass; verify useful new objects before production sign-off.
