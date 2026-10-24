"""Public navigation and build contract for the interactive cafe."""
import importlib.util
from pathlib import Path
import unittest
ROOT = Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('cafe_build',ROOT/'tools/build.py')
build=importlib.util.module_from_spec(spec);spec.loader.exec_module(build)
class CafeBuildTests(unittest.TestCase):
    def test_back_to_encyclopedia_stays_on_encyclopedia(self):
        source=(ROOT/'site/cafe/index.html').read_text()
        page=build.migrate_page(source,'cafe/index.html')
        self.assertIn('href="/"',page)
        self.assertNotIn('href="https://mickerbook.com/"',page)
        self.assertIn('https://aiher.ai/cafe/',page)
        self.assertNotIn('/meme/pageviews.js',page)
    def test_home_and_both_characters_offer_cafe_entry(self):
        for route in ['meme','claude','deepseek']:
            with self.subTest(route=route):
                page=build.migrate_page((ROOT/'site'/route/'index.html').read_text(),route+'/index.html')
                self.assertIn('href="/cafe/"',page)
