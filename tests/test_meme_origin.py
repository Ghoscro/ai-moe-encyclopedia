import sys
from pathlib import Path
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'services/meme-intake'))
import origin

class OriginTests(unittest.TestCase):
    def test_unapproved_hosts_never_requested(self):
        with mock.patch.object(origin.urllib.request, 'build_opener') as opener:
            for url in ['http://simonwillison.net/a', 'https://127.0.0.1/a',
                        'https://simonwillison.net.evil.test/a',
                        'https://user:password@simonwillison.net/a']:
                self.assertIsNone(origin.fetch(url))
            opener.assert_not_called()
    def test_redirects_are_not_followed(self):
        self.assertIsNone(origin.NoRedirect().redirect_request(None,None,302,'',{},'http://127.0.0.1'))
    def test_scripts_removed_but_image_evidence_retained(self):
        parser=origin.Text()
        parser.feed('<script>misleading instructions</script><p>Painting</p><img alt="A colorful oil painting">')
        text=' '.join(parser.parts)
        self.assertNotIn('misleading',text)
        self.assertIn('colorful oil painting',text)
    def test_fetch_failure_is_not_original_evidence(self):
        with mock.patch.object(origin.urllib.request,'build_opener') as opener:
            opener.return_value.open.side_effect=OSError('unavailable')
            self.assertIsNone(origin.fetch('https://simonwillison.net/a'))
