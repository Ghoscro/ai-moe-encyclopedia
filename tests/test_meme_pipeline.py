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
                'created_at': '2026-10-23T12:00:00+00:00',
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

    def test_admin_merge_requires_authorization_successful_checks_and_ahead_base(self):
        for authorized, conclusion, expected_merge in (
                ('1', 'SUCCESS', True), ('1', 'FAILURE', False),
                ('1', 'PENDING', False), ('0', 'SUCCESS', True)):
            with self.subTest(authorized=authorized, conclusion=conclusion):
                pipeline.save(self.path, self.receipt('pr'))
                calls = []

                def command(*args):
                    calls.append(args)
                    if args[:3] == ('gh', 'pr', 'view'):
                        if args[-1] == 'mergeCommit,state':
                            return json.dumps({'state': 'MERGED', 'mergeCommit': {'oid': 'merge123'}})
                        return json.dumps({'state': 'OPEN', 'headRefOid': 'head123',
                            'statusCheckRollup': [{'name': 'Content validation', 'conclusion': conclusion}]})
                    if args[:2] == ('gh', 'api'):
                        self.assertEqual(args[2], f'repos/{pipeline.REPOSITORY}/compare/main...head123')
                        return json.dumps({'status': 'ahead'})
                    if args[:3] == ('gh', 'pr', 'merge'):
                        return ''
                    self.fail(f'Unexpected command: {args}')

                self.command.side_effect = command
                with mock.patch.dict(os.environ, {'MEME_ALLOW_ADMIN_MERGE': authorized}):
                    with mock.patch.object(pipeline, 'verify_public', return_value=False):
                        self.main()
                merges = [args for args in calls if args[:3] == ('gh', 'pr', 'merge')]
                self.assertEqual(bool(merges), expected_merge)
                if merges:
                    self.assertEqual('--admin' in merges[0], authorized == '1')
                    self.assertIn('--match-head-commit', merges[0])
                    self.assertEqual(merges[0][-1] if authorized == '0' else merges[0][-2], 'head123')
                comparisons = [args for args in calls if args[:2] == ('gh', 'api')]
                self.assertEqual(bool(comparisons), authorized == '1' and conclusion == 'SUCCESS')

    def test_admin_authorization_still_refuses_diverged_base(self):
        pipeline.save(self.path, self.receipt('pr'))
        calls = []

        def command(*args):
            calls.append(args)
            if args[:3] == ('gh', 'pr', 'view'):
                return json.dumps({'state': 'OPEN', 'headRefOid': 'head123',
                    'statusCheckRollup': [{'name': 'Content validation', 'conclusion': 'SUCCESS'}]})
            if args[:2] == ('gh', 'api'):
                return json.dumps({'status': 'diverged'})
            self.fail(f'Must refuse before merge: {args}')

        self.command.side_effect = command
        with mock.patch.dict(os.environ, {'MEME_ALLOW_ADMIN_MERGE': '1'}):
            with self.assertRaisesRegex(RuntimeError, 'Base branch changed'):
                self.main()
        self.assertFalse(any(args[:3] == ('gh', 'pr', 'merge') for args in calls))
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['stage'], 'pr')


if __name__ == '__main__':
    unittest.main()


class SchedulingTest(unittest.TestCase):
    def test_empty_queue_resumes_hourly_live_keeps_publication_cap(self):
        self.assertEqual(pipeline.receipt_cooldown({'stage': 'empty'}), 3600)
        self.assertEqual(pipeline.receipt_cooldown({'stage': 'live'}), 72000)

    def test_review_continuation_does_not_recollect(self):
        with mock.patch.object(pipeline.time, 'time', return_value=100000):
            recent = {'finished_at': pipeline.dt.datetime.fromtimestamp(
                99000, pipeline.dt.timezone.utc).isoformat()}
            old = {'finished_at': pipeline.dt.datetime.fromtimestamp(
                10000, pipeline.dt.timezone.utc).isoformat()}
            self.assertFalse(pipeline.collection_due(recent))
            self.assertTrue(pipeline.collection_due(old))
            self.assertTrue(pipeline.collection_due(None))
            self.assertTrue(pipeline.collection_due({'finished_at': None}))
