"""Offline release-state and crash-recovery contracts; never call Git or HTTP."""
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest import mock

SERVICE = Path(__file__).resolve().parents[1] / 'services' / 'meme-intake'
sys.path.insert(0, str(SERVICE))
sys.path.insert(0, str(SERVICE.parents[1] / 'tools'))
import pipeline
from locking import exclusive


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'pipeline.json'
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        patch = stack.enter_context
        patch(mock.patch.dict(os.environ, {'MEME_INTAKE_DB': str(self.root / 'queue.db')}))
        patch(mock.patch.object(pipeline.intake, '_load_dotenv', return_value=0))
        self.command = patch(mock.patch.object(pipeline, 'command',
            side_effect=AssertionError('Unexpected external command')))
        patch(mock.patch('urllib.request.urlopen',
            side_effect=AssertionError('Real HTTP forbidden')))
        patch(mock.patch.object(socket.socket, 'connect',
            side_effect=AssertionError('Real socket forbidden')))
        self.prepare = patch(mock.patch.object(pipeline, 'prepare',
            side_effect=AssertionError('Resume must not recollect')))

    def receipt(self, stage='merged'):
        return {'stage': stage, 'commit': 'head123', 'merge_commit': 'merge123',
                'pr': 'https://example.com/pr/1',
                'entries': [{'fp': 'fingerprint', 'id': 'test-meme',
                             'character': 'gpt', 'url': 'https://example.com/source',
                             'entry': {'title': 'Test'}}]}

    def main(self):
        with mock.patch.object(sys, 'argv', ['pipeline.py']):
            with contextlib.redirect_stdout(io.StringIO()):
                pipeline.main()

    def test_lock_conflict_and_release(self):
        path = self.root / 'release.lock'
        with exclusive(path):
            with self.assertRaises(SystemExit):
                with exclusive(path):
                    self.fail('Concurrent publisher acquired lock')
        with exclusive(path):
            pass

    def test_atomic_receipt_replace_failure_preserves_previous(self):
        old = self.receipt('pr')
        pipeline.save(self.path, old)
        with mock.patch.object(pipeline.os, 'replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                pipeline.save(self.path, self.receipt('live'))
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8')), old)

    def test_public_false_never_marks_live_or_recollects(self):
        pipeline.save(self.path, self.receipt())
        with mock.patch.object(pipeline, 'verify_public', return_value=False) as verify:
            self.main()
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['stage'], 'merged')
        verify.assert_called_once()
        self.prepare.assert_not_called()
        self.command.assert_not_called()

    def test_public_exception_preserves_resumable_merged_receipt(self):
        pipeline.save(self.path, self.receipt())
        with mock.patch.object(pipeline, 'verify_public', side_effect=TimeoutError('offline')):
            with self.assertRaises(TimeoutError):
                self.main()
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['stage'], 'merged')
        self.prepare.assert_not_called()

    def test_public_success_marks_live_once(self):
        pipeline.save(self.path, self.receipt())
        with mock.patch.object(pipeline, 'verify_public', return_value=True) as verify:
            self.main()
            self.main()
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(saved['stage'], 'live')
        self.assertTrue(saved['verified_at'])
        verify.assert_called_once()
        self.prepare.assert_not_called()

    def test_open_pr_waits_without_recollection_or_merge(self):
        pipeline.save(self.path, self.receipt('pr'))
        self.command.side_effect = None
        self.command.return_value = json.dumps({'state': 'OPEN', 'headRefOid': 'head123',
                                                'statusCheckRollup': []})
        self.main()
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['stage'], 'pr')
        self.assertEqual(self.command.call_count, 1)
        self.prepare.assert_not_called()

    def test_pr_changed_head_refuses_merge(self):
        pipeline.save(self.path, self.receipt('pr'))
        self.command.side_effect = None
        self.command.return_value = json.dumps({'state': 'OPEN', 'headRefOid': 'changed',
            'statusCheckRollup': [{'name': 'Content validation', 'conclusion': 'SUCCESS'}]})
        with self.assertRaises(RuntimeError):
            self.main()
        self.assertEqual(self.command.call_count, 1)

    def test_merged_pr_resumes_public_check_without_merge_or_recollection(self):
        pipeline.save(self.path, self.receipt('pr'))
        self.command.side_effect = None
        self.command.return_value = json.dumps({'state': 'MERGED', 'headRefOid': 'head123',
                                                'mergeCommit': {'oid': 'merge123'}})
        with mock.patch.object(pipeline, 'verify_public', return_value=False):
            self.main()
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['stage'], 'merged')
        self.assertEqual(self.command.call_count, 1)
        self.prepare.assert_not_called()

    def test_public_release_commit_must_match_before_loading_pages(self):
        with mock.patch.object(pipeline, 'public_json', return_value={'source_commit': 'other'}):
            self.assertFalse(pipeline.verify_public(self.receipt()))


if __name__ == '__main__':
    unittest.main()
