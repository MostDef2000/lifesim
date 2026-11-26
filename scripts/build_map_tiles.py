#!/usr/bin/env python3
"""Build independently authored map LOD artwork into viewport-addressable WebP tiles.

Requires Pillow (build-time only). Missing optional LOD sources are never
synthesized or upscaled from LOD1; they are recorded as unavailable.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from PIL import Image


def generate(spec_path: Path, output_dir: Path, root: Path) -> dict:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    tile_size = int(spec.get("tileSize", 512))
    bleed = int(spec.get("bleedPx", 2))
    quality = int(spec.get("webpQuality", 84))
    world = spec.get("worldSize", [1600, 900])
    if not (128 <= tile_size <= 2048 and 0 <= bleed <= 8 and 0 <= quality <= 100):
        raise ValueError("Invalid tileSize, bleedPx, or webpQuality")
    if len(world) != 2 or any(not isinstance(v, int) or v <= 0 for v in world):
        raise ValueError("worldSize must be [positive integer width, height]")

    output_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "schemaVersion": 1,
        "worldSize": world,
        "coordinateSystem": "top-left/world-pixels; anchors follow coords.json",
        "tileSize": tile_size,
        "bleedPx": bleed,
        "format": "webp",
        "layers": {},
    }
    seen = set()
    for layer in spec["layers"]:
        name = str(layer["id"])
        if name not in ("lod1", "lod2", "lod3") or name in seen:
            raise ValueError(f"Invalid or duplicate LOD id: {name}")
        seen.add(name)
        bounds = layer.get("bounds")
        source = layer.get("source")
        required = bool(layer.get("required", False))
        path = root / source if source else None
        if not path or not path.is_file():
            if required:
                raise FileNotFoundError(f"Required {name} source artwork missing: {path}")
            result["layers"][name] = {
                "available": False,
                "reason": "Independent source artwork not supplied",
                "source": source,
                "bounds": bounds,
            }
            continue

        if not bounds or len(bounds) != 4:
            raise ValueError(f"{name} requires bounds [x, y, w, h] in LOD1 space")
        x, y, w, h = [float(v) for v in bounds]
        if min(x, y) < 0 or min(w, h) <= 0 or x + w > world[0] or y + h > world[1]:
            raise ValueError(f"{name} bounds outside canonical world")
        with Image.open(path) as opened:
            im = opened.convert("RGB")
        pw, ph = im.size
        sx, sy = pw / w, ph / h
        if abs(sx - sy) / max(sx, sy) > 0.01:
            raise ValueError(f"{name} aspect mismatch: pixel scale x={sx:.4f}, y={sy:.4f}")
        if name == "lod1" and (bounds != [0, 0, *world] or im.size != tuple(world)):
            raise ValueError("LOD1 must match the 1600x900 canonical world exactly")
        if name in ("lod2", "lod3") and min(sx, sy) <= 1.0:
            raise ValueError(f"{name} artwork needs native higher resolution than LOD1")

        cols, rows = math.ceil(pw / tile_size), math.ceil(ph / tile_size)
        tiles = []
        folder = output_dir / name
        folder.mkdir(parents=True, exist_ok=True)
        for row in range(rows):
            for col in range(cols):
                x0, y0 = col * tile_size, row * tile_size
                x1, y1 = min(x0 + tile_size, pw), min(y0 + tile_size, ph)
                # 2px overlap allows the renderer to sample filtering safely.
                # Draw only the inner x0..x1 / y0..y1 subrect of each tile.
                ox0, oy0 = max(0, x0 - bleed), max(0, y0 - bleed)
                ox1, oy1 = min(pw, x1 + bleed), min(ph, y1 + bleed)
                chunk = im.crop((ox0, oy0, ox1, oy1))
                filename = f"{row}-{col}.webp"
                chunk.save(folder / filename, format="WEBP", quality=quality, method=6)
                tiles.append({
                    "row": row, "col": col,
                    "url": f"{name}/{filename}",
                    "pixelRect": [x0, y0, x1 - x0, y1 - y0],
                    "bleedLTRB": [x0 - ox0, y0 - oy0, ox1 - x1, oy1 - y1],
                })
        result["layers"][name] = {
            "available": True,
            "bounds": bounds,
            "source": source,
            "sourcePixels": [pw, ph],
            "pixelsPerWorldUnit": round((sx + sy) / 2, 6),
            "grid": [cols, rows],
            "tiles": tiles,
        }
    for name in ("lod1", "lod2", "lod3"):
        if name not in seen:
            raise ValueError(f"Spec missing {name}")
    manifest = output_dir / "manifest.json"
    manifest.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--spec", default="docs/design/map-tiles.json", type=Path)
    p.add_argument("--output", default="backend/app/web/static/map/tiles", type=Path)
    p.add_argument("--root", default=".", type=Path)
    args = p.parse_args()
    root = args.root.resolve()
    try:
        result = generate(args.spec, args.output, root)
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
        print(f"Map tile build failed: {error}", file=sys.stderr)
        return 1
    for lod, value in result["layers"].items():
        label = ('%d WebP tiles' % len(value['tiles'])
                 if value['available'] else 'MISSING artwork, no fake tiles')
        print(f"{lod}: {label}")
    print(f"Manifest: {args.output / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
