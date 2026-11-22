#!/usr/bin/env python3
"""Optional, manually-run tile QA (not part of normal app test discovery)."""
import json
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from build_map_tiles import generate

class MapTilingSpikeTests(unittest.TestCase):
 def test_three_layers_from_independent_sources(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td)
   for name,size in [('base',(1600,900)),('region',(1920,1080)),('local',(1920,960))]:
    Image.new('RGB',size,'#74916a').save(root/f'{name}.png')
   spec={'worldSize':[1600,900],'tileSize':512,'bleedPx':2,'layers':[
    {'id':'lod1','source':'base.png','required':True,'bounds':[0,0,1600,900]},
    {'id':'lod2','source':'region.png','bounds':[320,240,960,540]},
    {'id':'lod3','source':'local.png','bounds':[480,330,480,240]}]}
   (root/'spec.json').write_text(json.dumps(spec))
   m=generate(root/'spec.json',root/'tiles',root)
   self.assertEqual([len(m['layers'][x]['tiles']) for x in ['lod1','lod2','lod3']],[8,12,8])
   with Image.open(root/'tiles/lod3/1-3.webp') as im:self.assertEqual(im.size,(386,450))
 def test_absent_artwork_not_masqueraded(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td)
   Image.new('RGB',(1600,900)).save(root/'base.png')
   spec={'worldSize':[1600,900], 'layers':[
    {'id':'lod1','source':'base.png','bounds':[0,0,1600,900],'required':True},
    {'id':'lod2','source':'missing2.png','bounds':[320,240,960,540]},
    {'id':'lod3','source':'missing3.png','bounds':[480,330,480,240]}]}
   (root/'spec.json').write_text(json.dumps(spec))
   m=generate(root/'spec.json',root/'out',root)
   self.assertFalse(m['layers']['lod2']['available']);self.assertFalse(m['layers']['lod3']['available'])
if __name__=='__main__':unittest.main()
