#!/usr/bin/env python3
"""Reassemble approved map PNG masters byte-for-byte from Git-tracked parts."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile

DEFAULT_MANIFEST=Path("docs/design/map-source/source-parts/manifest.json")
PNG_MAGIC=b"\x89PNG\r\n\x1a\n"

def _inside_root(root: Path, relative: str) -> Path:
    path=(root/relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Artwork path escapes root: {relative}")
    return path

def _verify(path: Path, expected_size: int, expected_sha: str, expected_dims: tuple[int,int]) -> None:
    digest=hashlib.sha256()
    actual_size=0
    with path.open("rb") as stream:
        header=stream.read(24)
        digest.update(header)
        actual_size+=len(header)
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            digest.update(chunk)
            actual_size+=len(chunk)
    if actual_size!=expected_size or digest.hexdigest()!=expected_sha:
        raise ValueError(f"Source checksum mismatch for {path}")
    if len(header)<24 or header[:8]!=PNG_MAGIC or header[12:16]!=b"IHDR":
        raise ValueError(f"Invalid PNG header: {path}")
    if struct.unpack(">II",header[16:24])!=expected_dims:
        raise ValueError(f"PNG dimensions mismatch for {path}")

def materialize(root: Path, manifest_path: Path=DEFAULT_MANIFEST) -> list[Path]:
    root=root.resolve()
    manifest=_inside_root(root,str(manifest_path))
    config=json.loads(manifest.read_text(encoding="utf-8"))
    if config.get("schemaVersion")!=1:
        raise ValueError("Unsupported manifest schema")
    result=[]
    for entry in config["sources"]:
        out=_inside_root(root,entry["output"])
        size=int(entry["bytes"])
        sha=entry["sha256"]
        dims=(int(entry["width"]),int(entry["height"]))
        if out.exists():
            _verify(out,size,sha,dims)
            result.append(out)
            continue
        parts=[_inside_root(root,p) for p in entry["parts"]]
        if not parts or any(not p.is_file() for p in parts):
            raise FileNotFoundError(f"PNG source parts missing for {out}")
        out.parent.mkdir(parents=True,exist_ok=True)
        tmp=None
        try:
            with tempfile.NamedTemporaryFile(prefix=f".{out.name}.",suffix=".tmp",dir=out.parent,delete=False) as dest:
                tmp=Path(dest.name)
                for part in parts:
                    with part.open("rb") as src:
                        for chunk in iter(lambda: src.read(1024*1024), b""):
                            dest.write(chunk)
            _verify(tmp,size,sha,dims)
            os.replace(tmp,out)
            result.append(out)
        finally:
            if tmp is not None and tmp.exists():
                tmp.unlink()
    return result

def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=Path("."))
    parser.add_argument("--manifest",type=Path,default=DEFAULT_MANIFEST)
    args=parser.parse_args()
    try:
        outputs=materialize(args.root,args.manifest)
    except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError) as exc:
        parser.exit(1,f"Artwork materialization failed: {exc}\n")
    for path in outputs:
        print(f"PNG verified: {path}")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
