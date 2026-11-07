import importlib.util,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('build_illustrations',Path(__file__).resolve().parents[1]/'tools/build.py');build=importlib.util.module_from_spec(spec);spec.loader.exec_module(build)
class IllustrationTests(unittest.TestCase):
 def row(self):return {'id':'safe-meme','kind':'original','date':'2026-11-07','title':'A < B','summary':'summary','body':'body','contributor':'editor','tags':[]}
 def test_missing_image_keeps_text_only(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(build,'ROOT',Path(tmp)):
   self.assertNotIn('<figure',build.card(self.row()))
 def test_present_image_is_lazy_sized_and_labelled(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(build,'ROOT',Path(tmp)):
   p=Path(tmp)/'site/meme/illustrations/safe-meme.webp';p.parent.mkdir(parents=True);p.write_bytes(b'fixture')
   html=build.card(self.row());self.assertIn('loading="lazy"',html);self.assertIn('width="1024"',html);self.assertIn('非原事件截图',html);self.assertIn('A &lt; B',html)
 def test_id_cannot_escape_asset_folder(self):
  row=self.row();row['id']='../escape';self.assertNotIn('<figure',build.card(row))
