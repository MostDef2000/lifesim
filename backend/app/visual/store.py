"""M6 T6: file asset store — local filesystem (§5), traversal-safe paths."""
from __future__ import annotations

from pathlib import Path

from app.config.config import VisualConfig


class AssetStore:
    """Saves/reads asset bytes under storage_dir. Paths stored in DB are relative."""

    def __init__(self, config: VisualConfig):
        self._root = Path(config.storage_dir).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def save(self, asset_id: int, data: bytes) -> str:
        relative = f"{asset_id}.png"
        target = self._resolve(relative)
        target.write_bytes(data)
        return relative

    def read(self, storage_path: str) -> bytes:
        return self._resolve(storage_path).read_bytes()

    def exists(self, storage_path: str) -> bool:
        return self._resolve(storage_path).exists()

    def _resolve(self, relative: str) -> Path:
        if "/" in relative or ".." in relative or relative.startswith("."):
            raise ValueError(f"unsafe storage path: {relative!r}")
        target = (self._root / relative).resolve()
        if target.parent != self._root:
            raise ValueError(f"unsafe storage path: {relative!r}")
        return target
