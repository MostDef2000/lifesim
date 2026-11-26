"""Tests for byte-exact art-source materialization (no image encoding changes)."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0,str(Path(__file__).parent))
from materialize_map_art import materialize


class ApprovedMapArtworkTests(unittest.TestCase):
    def test_restore_is_byte_exact_and_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            image=root/"lod2-region.png"
            Image.new("RGB",(300,150),"#386744").save(image)
            original=image.read_bytes()
            image.unlink()
            names=["parts/00","parts/01","parts/02"]
            cut=len(original)//3
            for i,name in enumerate(names):
                p=root/name
                p.parent.mkdir(parents=True,exist_ok=True)
                p.write_bytes(original[cut*i:cut*(i+1)] if i<2 else original[cut*i:])
            config={"schemaVersion":1,"sources":[{
                "output":"lod2-region.png","width":300,"height":150,
                "bytes":len(original),"sha256":hashlib.sha256(original).hexdigest(),
                "parts":names}]}
            (root/"manifest.json").write_text(json.dumps(config))
            self.assertEqual(materialize(root,Path("manifest.json")),[image])
            self.assertEqual(image.read_bytes(),original)
            self.assertEqual(materialize(root,Path("manifest.json")),[image])

    def test_corrupt_chunk_fails_without_output(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/"part").write_bytes(b"corrupt")
            config={"schemaVersion":1,"sources":[{
                "output":"lod3-settlement.png","width":1,"height":1,
                "bytes":7,"sha256":hashlib.sha256(b"not png").hexdigest(),
                "parts":["part"]}]}
            (root/"manifest.json").write_text(json.dumps(config))
            with self.assertRaises(ValueError):
                materialize(root,Path("manifest.json"))
            self.assertFalse((root/"lod3-settlement.png").exists())

if __name__=="__main__":
    unittest.main()
