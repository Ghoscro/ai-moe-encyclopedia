import sys
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/meme-intake'))
import impostor_source as source
import community, origin
URL='https://impostorbench.ai/en/games/main-261019-r05-g0879/'
HTML="""<main>main-261019-r05-g0879 2026-10-19 21:19 UTC
<section class="castw">Muse Spark 1.3</section><section id="ph3">Muse: microwave ate wifi<img alt="invented image"><script>evil()</script></section><section id="ph4">MiniMax: too funny to be made up</section><section id="ph5">secret voting reasoning</section><section id="result">Muse zero votes</section></main>"""
class TranscriptTests(unittest.TestCase):
    def test_url_boundaries(self):
        self.assertTrue(source.supported(URL))
        for bad in (URL+'?x=1',URL+'#ph3',URL.replace('https','http'),URL.replace('.ai','.ai.evil'),URL.replace('.ai','.ai:443'),URL.replace('impostorbench','user@impostorbench'),URL+'../',None):
            with self.subTest(url=bad),patch.object(community,'get') as get:
                self.assertIsNone(source.evidence(bad));get.assert_not_called()
    def test_scope_date_and_roleplay(self):
        with patch.object(community,'get',return_value=HTML): e=origin.fetch(URL)
        self.assertEqual(e['published_at'],'2026-10-19T21:19:00+00:00')
        self.assertIn('All players are models',e['text'])
        self.assertIn('Muse: microwave ate wifi',e['text'])
        for private in ('secret voting reasoning','invented image','evil()'): self.assertNotIn(private,e['text'])
    def test_missing_or_conflicting_metadata_rejected(self):
        for bad in (HTML.replace('2026-10-19','2026-10-20'), HTML.replace(' UTC',' local'),HTML.replace('main-261019-r05-g0879','different-game'),HTML.replace('id="ph4"','id="missing"'),HTML.replace('2026-10-19','2099-10-19')):
            with patch.object(community,'get',return_value=bad): self.assertIsNone(source.evidence(URL))
    def test_network_failure_is_not_evidence(self):
        with patch.object(community,'get',side_effect=OSError('unavailable')): self.assertIsNone(source.evidence(URL))
