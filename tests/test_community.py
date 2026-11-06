import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services/meme-intake'))
import community
import daily_plan
import db

class CommunityTests(unittest.TestCase):
    def test_exact_public_permalink_only(self):
        for url in ('http://www.reddit.com/r/a/comments/abc/story/',
                    'https://www.reddit.com.evil/r/a/comments/abc/story/',
                    'https://x@www.reddit.com/r/a/comments/abc/story/',
                    'https://www.v2ex.com:444/t/123', 'https://127.0.0.1/t/123',
                    'https://news.ycombinator.com/item?id=123&redirect=foo'):
            self.assertFalse(community.supported(url), url)
        self.assertTrue(community.supported('https://www.reddit.com/r/DeepSeek/comments/abc/story/'))
        self.assertTrue(community.supported('https://www.v2ex.com/t/123'))
    def test_alias_boundaries(self):
        self.assertFalse(community.matches('glm', 'glmatrix release'))
        self.assertTrue(community.matches('glm', 'GLM told me'))
        self.assertTrue(community.matches('doubao', '我让豆包写作'))
    def test_rss_preserves_original_date_not_updated(self):
        xml = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>DeepSeek story</title>
        <link href="https://www.reddit.com/r/DeepSeek/comments/abc/story/"/>
        <published>2026-01-01T00:00:00+00:00</published><updated>2026-11-07T00:00:00Z</updated>
        <content>&lt;p&gt;I asked &lt;b&gt;DeepSeek&lt;/b&gt; for a joke.&lt;/p&gt;</content></entry></feed>'''
        row = community.parse_reddit(xml)[0]
        self.assertEqual(row['published_at'], '2026-01-01T00:00:00+00:00')
        self.assertIn('I asked DeepSeek', row['summary'])
    def test_wrong_original_id_never_used(self):
        with mock.patch.object(community, 'get', return_value=[{'id': 999}]):
            self.assertIsNone(community.evidence('https://www.v2ex.com/t/123'))
    def test_image_only_short_source_rejected(self):
        with mock.patch.object(community, 'get', return_value=[{'id': 123, 'created': 1700000000,
                'title': 'AI picture', 'content': ''}]):
            self.assertIsNone(community.evidence('https://www.v2ex.com/t/123'))
    def test_failures_rotate_and_do_not_hammer(self):
        with tempfile.TemporaryDirectory() as tmp, db.Store(Path(tmp) / 'queue.db') as store:
            with mock.patch.object(community, 'get', side_effect=OSError('offline')) as get:
                first=community.discover(store,tmp,{})
                self.assertEqual(len(first['characters']),4)
                second=community.discover(store,tmp,{})
                self.assertEqual(len(second['characters']),8)
                self.assertEqual(get.call_count,19)
    def test_daily_target_ten_verified_receipts_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            stamp=dt.datetime.now(dt.timezone.utc).isoformat()
            entries=[{'id':str(i),'character':'gpt'} for i in range(9)]
            (p/'pipeline.json').write_text(json.dumps({'stage':'live','verified_at':stamp,'entries':entries}))
            cov=daily_plan.write_coverage(p,[])
            self.assertEqual(cov['target_per_character'],10)
            self.assertNotEqual(cov['characters']['gpt']['status'],'complete')
            entries.append({'id':'9','character':'gpt'})
            (p/'pipeline.json').write_text(json.dumps({'stage':'live','verified_at':stamp,'entries':entries}))
            self.assertEqual(daily_plan.write_coverage(p,[])['characters']['gpt']['status'],'complete')
    def test_duplicate_story_url_does_not_fill_two_slots(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            stamp=dt.datetime.now(dt.timezone.utc).isoformat()
            rows=[{'id':'one','character':'gpt','url':'https://example.com/post'},
                  {'id':'two','character':'gpt','url':'https://example.com/post?utm_source=copy'}]
            (p/'pipeline.json').write_text(json.dumps({'stage':'live','verified_at':stamp,'entries':rows}))
            self.assertEqual(daily_plan.live_counts(p)['gpt'],1)
    def test_character_window_not_crowded_out(self):
        with tempfile.TemporaryDirectory() as tmp, db.Store(Path(tmp)/'q.db') as store:
            for i in range(20):
                store.add_candidate({'fp':str(i),'source':'test','url':'https://example.com/'+str(i),'title':'GPT','character':'gpt','meme_score':100})
            store.add_candidate({'fp':'claude','source':'test','url':'https://example.com/c','title':'Claude','character':'claude','meme_score':30})
            rows=store.list(status='pending', min_score=30, limit=3, character='claude')
            self.assertEqual([r['fp'] for r in rows],['claude'])
    def test_completed_roles_not_fetched(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(community, 'get') as get:
            community.discover(mock.Mock(),tmp,{c:10 for c in community.ALIASES})
            get.assert_not_called()

if __name__ == '__main__': unittest.main()
