import copy
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services/meme-intake'))
import community
import discourse_source as source

URL = 'https://www.nodeloc.com/t/topic/105378'


def fixture():
    return {'id': 105378, 'title': 'MiniMax mooncake story', 'post_stream': {'posts': [
        {'topic_id': 105378, 'post_number': 1, 'username': 'player', 'created_at': '2026-08-26T14:12:19Z',
         'cooked': '<p>Three mooncake tasks</p><img alt="unverified image claim" title="hidden claim">'},
        {'topic_id': 105378, 'post_number': 9, 'username': 'agent', 'created_at': '2026-08-27T22:51:05Z',
         'cooked': '<p>What an obsession</p><script>evil()</script>'},
        {'topic_id': 105378, 'post_number': 10, 'username': 'player', 'created_at': '2026-08-28T05:42:13Z',
         'cooked': '<blockquote>What an obsession</blockquote><p>I mean you</p>'}]}}


class DiscourseSourceTests(unittest.TestCase):
    def test_exact_url_boundaries(self):
        for good in (URL, URL + '/10', 'https://linux.do/t/topic/42'):
            self.assertTrue(source.supported(good))
        for bad in (URL+'?page=2', URL+'#post', URL+'/0', URL+'/01', URL.replace('https','http'),
                    URL.replace('www.nodeloc.com','www.nodeloc.com.evil'), URL.replace('www.nodeloc.com','user@www.nodeloc.com'),
                    URL.replace('www.nodeloc.com','www.nodeloc.com:443'), URL+'/10/more', URL+'\n',
                    URL.replace('105378','%31'), None):
            with self.subTest(url=bad), patch.object(community, 'get') as get:
                self.assertFalse(source.supported(bad))
                self.assertIsNone(source.evidence(bad))
                get.assert_not_called()

    def test_authors_dates_and_attachments_are_separate(self):
        with patch.object(community, 'get', return_value=fixture()) as get:
            result=source.evidence(URL+'/10')
        get.assert_called_once_with('https://www.nodeloc.com/t/105378.json', True)
        self.assertEqual(result['published_at'], '2026-08-28T05:42:13+00:00')
        self.assertEqual(result['author'], 'player')
        self.assertEqual([p['author'] for p in result['posts']], ['player','agent','player'])
        self.assertNotIn('unverified image claim', result['text'])
        self.assertNotIn('hidden claim', result['text'])
        self.assertNotIn('evil()', result['text'])
        self.assertIn('非本楼作者原话', result['text'])
        self.assertEqual(result['sha256'], hashlib.sha256(result['text'].encode()).hexdigest())

    def test_exact_topic_floor_and_default_first_post(self):
        with patch.object(community,'get',return_value=fixture()):
            self.assertIsNone(source.evidence(URL+'/2'))
            self.assertEqual(source.evidence(URL)['published_at'],'2026-08-26T14:12:19+00:00')
        for change in ('wrong_topic','wrong_row_topic','no_first','duplicate'):
            data=fixture()
            if change=='wrong_topic':data['id']=105379
            if change=='wrong_row_topic':data['post_stream']['posts'][1]['topic_id']=1
            if change=='no_first':data['post_stream']['posts']=data['post_stream']['posts'][1:]
            if change=='duplicate':data['post_stream']['posts'].append(data['post_stream']['posts'][0])
            with self.subTest(change=change), patch.object(community,'get',return_value=data):
                self.assertIsNone(source.evidence(URL))

    def test_unknown_naive_future_dates_rejected(self):
        for date in (None,'','broken','2026-08-26T14:12:19','2999-01-01T00:00:00Z'):
            data=fixture();data['post_stream']['posts'][0]['created_at']=date
            with self.subTest(date=date), patch.object(community,'get',return_value=data):
                self.assertIsNone(source.evidence(URL))

    def test_twenty_posts_no_pagination_and_error(self):
        data=fixture();template=data['post_stream']['posts'][0]
        data['post_stream']['posts']=[dict(template,post_number=i) for i in range(1,31)]
        with patch.object(community,'get',return_value=data) as get:
            self.assertEqual(len(source.evidence(URL)['posts']),20)
            self.assertIsNone(source.evidence(URL+'/21'))
            self.assertEqual(get.call_count,2)
        with patch.object(community,'get',side_effect=ValueError('oversized source')):
            self.assertIsNone(source.evidence(URL))


if __name__=='__main__':unittest.main()
