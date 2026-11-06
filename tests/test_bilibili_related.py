import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services/meme-intake'))
import bilibili_related as related
import community


class BilibiliRelatedTests(unittest.TestCase):
    now = dt.datetime(2026, 11, 7, tzinfo=dt.timezone.utc)
    seeds = [{'url': 'https://www.bilibili.com/video/' + bv + '/'} for bv in
             ('BV159nc6mEsm', 'BV1TPna6pEW2', 'BV1RCnK6fEGp')]

    def test_all_roles_and_ambiguous_attribution(self):
        for role, aliases in community.ALIASES.items():
            self.assertEqual(related.character_for('creator introduction', aliases[0]), role)
        self.assertEqual(related.character_for('Claude and Grok react', 'Grok小姐'), 'grok')
        self.assertEqual(related.character_for('千问娘宣布接管', '文昭'), 'qwen')
        self.assertIsNone(related.character_for('Claude and Grok react', 'creator'))
        self.assertIsNone(related.character_for('Claude plays', 'Claude and Grok'))

    def test_bounds_exact_ids_duplicates_and_success_cooldown(self):
        payload = {'code': 0, 'data': [
            {'bvid': 'BV1PGnF6oEno', 'title': 'Grok black hair story', 'owner': {'name': 'Grok小姐'}},
            {'bvid': 'BV1PGnF6oEno/../../evil', 'title': 'Grok title', 'owner': {'name': 'Grok'}},
            {'bvid': 'BV1dhHM6zEDu', 'title': 'Claude vs Grok', 'owner': {'name': 'community'}},
            {'bvid': 'BV1dhHM6zEDu', 'title': 'Grok title', 'owner': []}]}
        with tempfile.TemporaryDirectory() as root, patch.object(related, '_fetch', return_value=payload) as fetch:
            got = related.discover(self.seeds, root, self.now)
            self.assertEqual(fetch.call_count, 2)
            self.assertTrue(all(call.kwargs['timeout'] <= 20 for call in fetch.call_args_list))
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0]['character'], 'grok')
            checked = json.loads((Path(root) / 'bilibili-related.json').read_text())['seeds']
            checked_seeds = [{'url': 'https://www.bilibili.com/video/' + bv + '/'} for bv in checked]
            fetch.reset_mock()
            related.discover(checked_seeds, root, self.now + dt.timedelta(hours=3))
            fetch.assert_not_called()
            related.discover(checked_seeds, root, self.now + dt.timedelta(hours=4))
            self.assertEqual(fetch.call_count, 2)

    def test_failure_receipt_redacts_message_and_retries_after_hour(self):
        with tempfile.TemporaryDirectory() as root, patch.object(related, '_fetch', side_effect=ValueError('secret-value')) as fetch:
            self.assertEqual(related.discover(self.seeds[:1], root, self.now), [])
            text = (Path(root) / 'bilibili-related.json').read_text()
            self.assertNotIn('secret-value', text)
            self.assertIn('ValueError', text)
            fetch.reset_mock()
            related.discover(self.seeds[:1], root, self.now + dt.timedelta(minutes=59))
            fetch.assert_not_called()
            related.discover(self.seeds[:1], root, self.now + dt.timedelta(hours=1))
            fetch.assert_called_once()

    def test_global_deadline_and_bad_seed(self):
        with tempfile.TemporaryDirectory() as root, patch.object(related, '_fetch') as fetch, \
                patch.object(related.time, 'monotonic', side_effect=[0, 61]):
            self.assertEqual(related.discover(self.seeds, root, self.now), [])
            fetch.assert_not_called()
        with tempfile.TemporaryDirectory() as root, patch.object(related, '_fetch') as fetch:
            related.discover([{'url': 'https://evil.test/video/BV159nc6mEsm/'}, {'url': self.seeds[0]['url'] + '?redirect=x'}], root, self.now)
            fetch.assert_not_called()

    def test_child_timeout_enforced_and_credentials_not_forwarded(self):
        with patch.dict(related.os.environ, {'MEME_API_KEY': 'secret', 'GH_TOKEN': 'secret'}), \
                patch.object(related.subprocess, 'run', side_effect=subprocess.TimeoutExpired('python', 5)) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                related._fetch('https://api.bilibili.com/x/web-interface/archive/related?bvid=BV159nc6mEsm', 5)
            args = run.call_args.kwargs
            self.assertEqual(args['timeout'], 5)
            self.assertNotIn('MEME_API_KEY', args['env'])
            self.assertNotIn('GH_TOKEN', args['env'])
