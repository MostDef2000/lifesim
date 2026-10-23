"""020 P3: user reference upload store — local filesystem, originals only.

Mirrors AssetStore (app/visual/store.py): bytes live under a configured
dir, DB rows carry relative paths, same traversal guard. The extension is
server-derived from the magic-byte mime ({ref_id}.png|.jpg) — never from
the client filename. Original bytes are stored unconverted (never
re-encoded).
"""
from __future__ import annotations

from pathlib import Path

from app.config.config import VisualConfig


class VisualReferenceStore:
    """Saves/reads reference bytes under references_dir. Paths stored in DB
    are relative, keyed by the VisualReference id."""

    _EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg"}

    def __init__(self, config: VisualConfig):
        self._root = Path(config.references_dir).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def save(self, ref_id: int, mime: str, data: bytes) -> str:
        relative = f"{ref_id}{self._EXTENSIONS[mime]}"
        self._resolve(relative).write_bytes(data)
        return relative

    def read(self, storage_path: str) -> bytes:
        return self._resolve(storage_path).read_bytes()

    def exists(self, storage_path: str) -> bool:
        return self._resolve(storage_path).exists()

    def remove(self, storage_path: str) -> None:
        """Delete a reference file (owner-managed delete; no TTL)."""
        self._resolve(storage_path).unlink(missing_ok=True)

    # Copied from AssetStore._resolve rather than shared: 6 lines, and
    # 020 P3 scope keeps store.py untouched.
    def _resolve(self, relative: str) -> Path:
        if "/" in relative or ".." in relative or relative.startswith("."):
            raise ValueError(f"unsafe storage path: {relative!r}")
        target = (self._root / relative).resolve()
        if target.parent != self._root:
            raise ValueError(f"unsafe storage path: {relative!r}")
        return target
