"""Meme intake tests. Stdlib unittest, matching the encyclopedia's style.

Network is never touched: AIHotSource._get / _get_json / _get_text are
monkeypatched with fixtures, and approvals write into a temp MEMES_DIR.
"""
import contextlib
import io
import json
import os
import socket
import sqlite3
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
INTAKE = HERE.parent / 'services' / 'meme-intake'
sys.path.insert(0, str(INTAKE))
sys.path.insert(0, str(HERE.parent / 'tools'))

import db as db_mod            # noqa: E402
import radar                   # noqa: E402
import intake                  # noqa: E402
import llm as llm_mod          # noqa: E402
import build as build_mod      # noqa: E402
import sources                 # noqa: E402


def setUpModule():
    """Offline by default; only explicitly mocked I/O may cross this guard."""
    stack = contextlib.ExitStack()
    unittest.addModuleCleanup(stack.close)
    loader = intake._load_dotenv

    def temporary_dotenv_only(path=None):
        target = Path(path) if path else intake.HERE / '.env'
        if not target.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()):
            return 0
        return loader(target)

    stack.enter_context(mock.patch.object(intake, '_load_dotenv',
                                         side_effect=temporary_dotenv_only))
    stack.enter_context(mock.patch.dict(os.environ, {
        'MEME_LLM_BASE_URL': '', 'MEME_LLM_API_KEY': '', 'MEME_LLM_MODEL': ''}))
    stack.enter_context(mock.patch('urllib.request.urlopen',
                                  side_effect=AssertionError('Real network forbidden in intake tests')))
    stack.enter_context(mock.patch.object(socket.socket, 'connect',
                                         side_effect=AssertionError('Real socket forbidden in intake tests')))


def hot_item(title='GPT 娘玩梗翻车名场面', summary='社区二创爆火，表情包热传。',
             score=80, url='https://example.com/a', published_at=None,
             category='ai-models'):
    return {'source': 'AIHOT', 'url': url, 'title': title, 'summary': summary,
            'category': category, 'source_score': score,
            'published_at': published_at or '2026-10-22T10:00:00.000Z',
            'discovered_at': '2026-10-22T10:05:00.000Z'}


RSS20_FIXTURE = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>梗信源 fixture</title>
    <link>https://example.com</link>
    <description>test feed</description>
    <item>
      <title>GPT 娘玩梗翻车名场面</title>
      <link>https://example.com/a</link>
      <description>社区二创爆火，表情包热传。</description>
      <pubDate>Tue, 21 Oct 2025 10:00:00 GMT</pubDate>
      <category>ai-models</category>
    </item>
    <item>
      <title>坏条目：没有链接</title>
      <description>应被跳过，且不能拖垮整批。</description>
    </item>
    <item>
      <title>Kimi 又整活了</title>
      <link>https://example.com/b</link>
      <description>月之暗面新梗出圈。</description>
      <pubDate>not-a-date</pubDate>
    </item>
  </channel>
</rss>"""

ATOM_FIXTURE = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom 信源 fixture</title>
  <link rel="self" href="https://example.com/feed"/>
  <entry>
    <title>DeepSeek 梗图热传</title>
    <link rel="alternate" href="https://example.com/c"/>
    <id>tag:example.com,2025:c</id>
    <updated>2025-10-20T08:30:00Z</updated>
    <summary>深度求索表情包二创爆火。</summary>
    <category term="ai-models"/>
  </entry>
</feed>"""

EMPTY_FEED = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>空信源</title></channel></rss>"""

BAIDU_FIXTURE = {'data': {'cards': [
    # real shape: card.content is a LIST of groups, each wrapping the words
    {'content': [{'content': [
        {'word': '置顶热词', 'url': 'https://www.baidu.com/s?wd=top', 'isTop': True}]}]},
    {'content': [{'content': [
        {'word': 'GPT 娘玩梗翻车名场面', 'url': 'https://www.baidu.com/s?wd=gpt'}]}]},
    {'content': 'not-a-dict'},   # bad card: skipped, not fatal
    'garbage',                   # bad card: skipped, not fatal
    {'content': {'content': [    # bare-dict shape is tolerated
        {'word': '字典形态热词', 'url': 'https://www.baidu.com/s?wd=dict'}]}},
]}}

DOUYIN_FIXTURE = {'data': {'word_list': [
    {'word': 'Kimi 又整活了', 'position': 1, 'hot_value': 12099024,
     'event_time': 1729598400},
    {'word': '坏位置热词', 'position': 'x', 'hot_value': 5, 'event_time': 'not-epoch'},
    'garbage',                  # bad entry: skipped, not fatal
]}}


class RadarTest(unittest.TestCase):
    def test_character_match_gates_score(self):
        with_char = radar.score_item(hot_item())
        without = radar.score_item(hot_item(title='某开源工具发布新版本', summary=''))
        self.assertEqual(with_char['character'], 'gpt')
        self.assertGreater(with_char['meme_score'], without['meme_score'])
        self.assertLess(without['factors']['w_character'], 1.0)

    def test_noise_penalises_but_does_not_exclude(self):
        noisy = radar.score_item(hot_item(title='GPT 娘玩梗翻车', summary='伴随财报与裁员新闻'))
        self.assertTrue(noisy['noise'])
        self.assertEqual(noisy['factors']['noise'], 0.6)
        self.assertGreater(noisy['meme_score'], 0)

    def test_codex_folds_into_gpt_family(self):
        self.assertEqual(radar.match_character(hot_item(title='Codex 又整活了')), 'gpt')

    def test_explain_is_informative(self):
        text = radar.explain(hot_item())
        self.assertIn('gpt', text)
        self.assertIn('分', text)

    def test_lead_card_never_claims_sourced(self):
        card = radar.make_lead_card(hot_item())
        self.assertEqual(card['suggested_kind'], 'community')
        self.assertIn('待人工核实', card['note'])

    def test_hostile_item_types_do_not_explode(self):
        # P1-3: a third-party API drifting into ints/None must not crash
        hostile = {'title': None, 'summary': 42, 'source_score': 'abc',
                   'published_at': 1729598400, 'url': 'https://e.com/x'}
        scored = radar.score_item(hostile)
        self.assertEqual(scored['meme_score'], 0.0)
        self.assertEqual(scored['character'], '')
        self.assertEqual(scored['signals'], [])
        self.assertEqual(radar._freshness(1729598400), 0.8)
        self.assertEqual(radar._freshness(None), 0.8)
        self.assertEqual(radar._freshness('not-a-date'), 0.8)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = db_mod.Store(Path(self.tmp.name) / 't.db')

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_fingerprint_dedupe(self):
        row = dict(hot_item(), fp=db_mod.fingerprint('AIHOT', 'https://example.com/a'),
                   meme_score=42.0)
        self.assertTrue(self.store.add_candidate(row))
        self.assertFalse(self.store.add_candidate(row))

    def test_published_gate_blocks_reapproval(self):
        row = dict(hot_item(), fp='abc', meme_score=1.0)
        self.store.add_candidate(row)
        self.store.mark_published('abc', 'meme-1')
        self.assertTrue(self.store.is_published('abc'))

    def test_status_flow(self):
        row = dict(hot_item(url='https://example.com/b'), fp='def', meme_score=5.0)
        self.store.add_candidate(row)
        self.store.set_status('def', 'rejected', 'test')
        got = self.store.get('def')
        self.assertEqual(got['status'], 'rejected')
        self.assertEqual(got['reject_reason'], 'test')

    def test_wal_pragma_on_temp_db(self):
        # P1-2: WAL + busy_timeout so a scheduled run and a manual approve
        # can overlap without an instant "database is locked"
        with tempfile.TemporaryDirectory() as tmp:
            store = db_mod.Store(Path(tmp) / 'wal.db')
            mode = store.db.execute('PRAGMA journal_mode').fetchone()[0]
            busy = store.db.execute('PRAGMA busy_timeout').fetchone()[0]
            store.close()
        self.assertEqual(mode.lower(), 'wal')
        self.assertEqual(busy, 30000)


class SourceTest(unittest.TestCase):
    def test_degraded_response_stops_pagination(self):
        src = sources.AIHotSource()
        pages = [
            {'items': [{'title': 't', 'url': 'https://e.com/1', 'score': 50}],
             'page': {'hasMore': True, 'nextCursor': 'c1'}},
            {'items': [], 'page': {'hasMore': True}},  # degraded: short non-final
        ]
        with mock.patch.object(sources, '_get', side_effect=pages):
            items, receipt = src.fetch()
        self.assertEqual(len(items), 1)
        self.assertEqual(receipt['degraded'], 1)

    def test_full_pagination(self):
        src = sources.AIHotSource()
        pages = [
            {'items': [{'title': 'a', 'url': 'https://e.com/1', 'score': 10}],
             'page': {'hasMore': True, 'nextCursor': 'c1'}},
            {'items': [{'title': 'b', 'url': 'https://e.com/2', 'score': 20}],
             'page': {'hasMore': False}},
        ]
        with mock.patch.object(sources, '_get', side_effect=pages):
            items, receipt = src.fetch()
        self.assertEqual(len(items), 2)
        self.assertEqual(receipt['degraded'], 0)

    def test_hostile_api_item_is_normalised(self):
        # P1-3: epoch-int dates, 'abc' scores and None titles read as
        # empty/zero instead of exploding mid-pagination
        src = sources.AIHotSource()
        pages = [{'items': [{'title': None, 'summary': 42, 'url': 'https://e.com/1',
                             'score': 'abc', 'publishedAt': 1729598400,
                             'category': 7}],
                  'page': {'hasMore': False}}]
        with mock.patch.object(sources, '_get', side_effect=pages):
            items, _ = src.fetch()
        self.assertEqual(items[0]['title'], '')
        self.assertEqual(items[0]['summary'], '')
        self.assertEqual(items[0]['category'], '')
        self.assertEqual(items[0]['published_at'], '')
        self.assertEqual(items[0]['source_score'], 0)
        self.assertEqual(items[0]['url'], 'https://e.com/1')


class BoardSourceTest(unittest.TestCase):
    """Baidu / Douyin hot-search boards: rank-based scoring, no raw counters."""

    def test_baidu_board_fixture(self):
        src = sources.BaiduHotSource()
        with mock.patch.object(sources, '_get_json', return_value=BAIDU_FIXTURE):
            items, receipt = src.fetch()
        self.assertEqual(len(items), 3)  # two malformed cards skipped
        self.assertEqual(items[0]['title'], '置顶热词')
        self.assertEqual(items[0]['source'], 'Baidu')
        self.assertEqual(items[0]['category'], 'hotsearch')
        self.assertEqual(items[0]['published_at'], '')
        self.assertEqual(items[0]['source_score'], 100.0)   # isTop pins at 100
        self.assertEqual(items[1]['source_score'], 67.5)    # rank 2 of 3
        self.assertEqual(items[2]['source_score'], 40.0)    # rank 3 of 3
        self.assertEqual(receipt, {'source': 'baidu', 'items': 3, 'degraded': 0})

    def test_baidu_down_or_empty_is_degraded(self):
        src = sources.BaiduHotSource()
        with mock.patch.object(sources, '_get_json', return_value=None):
            items, receipt = src.fetch()
        self.assertEqual(items, [])
        self.assertEqual(receipt['degraded'], 1)
        with mock.patch.object(sources, '_get_json',
                               return_value={'data': {'cards': []}}):
            items, receipt = src.fetch()
        self.assertEqual(receipt['degraded'], 1)  # a real board is never empty

    def test_douyin_board_fixture(self):
        src = sources.DouyinHotSource()
        with mock.patch.object(sources, '_get_json', return_value=DOUYIN_FIXTURE):
            items, receipt = src.fetch()
        self.assertEqual(len(items), 2)
        first = items[0]
        self.assertEqual(first['source'], 'Douyin')
        self.assertEqual(first['source_score'], 95.0)  # position 1 of 2
        self.assertEqual(first['published_at'], '2024-10-22T12:00:00+00:00')
        self.assertEqual(first['url'], 'https://www.douyin.com/search/'
                         + urllib.parse.quote('Kimi 又整活了'))
        # dirty position falls back to list order; hot_value is NOT the score
        self.assertEqual(items[1]['source_score'], 40.0)
        self.assertEqual(items[1]['published_at'], '')
        self.assertEqual(receipt, {'source': 'douyin', 'items': 2, 'degraded': 0})

    def test_douyin_down_is_degraded(self):
        src = sources.DouyinHotSource()
        with mock.patch.object(sources, '_get_json', return_value=None):
            items, receipt = src.fetch()
        self.assertEqual(items, [])
        self.assertEqual(receipt['degraded'], 1)

    def test_douyin_sends_referer_header(self):
        # the WAF answers 200-with-empty-body unless Referer says /hot
        # (verified against the live API during the v0.3 real run)
        src = sources.DouyinHotSource()
        with mock.patch.object(sources, '_get_json', return_value=None) as get:
            src.fetch()
        self.assertEqual(get.call_args.kwargs['extra_headers']['Referer'],
                         'https://www.douyin.com/hot')


class HotsearchScoreTest(unittest.TestCase):
    """Board rank -> source_score, shared by the hot-search adapters."""

    def test_leader_scores_95_and_tail_40(self):
        self.assertEqual(sources.hotsearch_score(1, 50), 95.0)
        self.assertEqual(sources.hotsearch_score(50, 50), 40.0)

    def test_pinned_entry_scores_100(self):
        self.assertEqual(sources.hotsearch_score(7, 50, is_top=True), 100.0)
        self.assertEqual(sources.hotsearch_score(1, 1, is_top=True), 100.0)

    def test_single_entry_board_scores_95(self):
        self.assertEqual(sources.hotsearch_score(1, 1), 95.0)
        self.assertEqual(sources.hotsearch_score(1, 0), 95.0)

    def test_rank_out_of_bounds_is_clamped(self):
        self.assertEqual(sources.hotsearch_score(0, 50), 95.0)    # -> rank 1
        self.assertEqual(sources.hotsearch_score(999, 50), 40.0)  # -> rank n

    def test_linear_decay_stays_in_band(self):
        scores = [sources.hotsearch_score(r, 11) for r in range(1, 12)]
        self.assertEqual(scores[0], 95.0)
        self.assertEqual(scores[-1], 40.0)
        self.assertTrue(all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1)))
        self.assertTrue(all(40.0 <= s <= 95.0 for s in scores))

    def test_mid_board_value_matches_formula(self):
        # rank 26 of 50: 95 - 25 * 55 / 49
        self.assertAlmostEqual(sources.hotsearch_score(26, 50), 66.94, places=2)


class RSSSourceTest(unittest.TestCase):
    """RSS adapter: stdlib parsing, per-entry fault tolerance, RawItem shape."""

    def test_rss20_fixture(self):
        items = sources._parse_feed(RSS20_FIXTURE, 'test-feed')
        self.assertEqual(len(items), 2)  # the link-less entry is skipped
        first = items[0]
        self.assertEqual(first['source'], 'test-feed')
        self.assertEqual(first['url'], 'https://example.com/a')
        self.assertEqual(first['title'], 'GPT 娘玩梗翻车名场面')
        self.assertEqual(first['category'], 'ai-models')
        self.assertEqual(first['published_at'], '2025-10-21T10:00:00+00:00')
        # same RawItem contract as AIHotSource
        self.assertEqual(set(first), {'source', 'url', 'title', 'summary',
                                      'category', 'source_score',
                                      'published_at', 'discovered_at'})

    def test_bad_date_leaves_published_at_empty(self):
        items = sources._parse_feed(RSS20_FIXTURE, 'test-feed')
        self.assertEqual(items[1]['published_at'], '')

    def test_atom_fixture(self):
        items = sources._parse_feed(ATOM_FIXTURE, 'atom-feed')
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['url'], 'https://example.com/c')  # not rel=self
        self.assertEqual(items[0]['published_at'], '2025-10-20T08:30:00+00:00')
        self.assertEqual(items[0]['summary'], '深度求索表情包二创爆火。')

    def test_empty_feed_is_not_degraded(self):
        src = sources.RSSSource('https://example.com/feed', 'empty')
        with mock.patch.object(sources, '_get_text', return_value=EMPTY_FEED):
            items, receipt = src.fetch()
        self.assertEqual(items, [])
        self.assertEqual(receipt, {'source': 'empty', 'items': 0, 'degraded': 0})

    def test_unparseable_feed_is_degraded(self):
        src = sources.RSSSource('https://example.com/feed', 'broken')
        with mock.patch.object(sources, '_get_text', return_value='<rss><channel>'):
            items, receipt = src.fetch()
        self.assertEqual(items, [])
        self.assertEqual(receipt['degraded'], 1)

    def test_fetch_failure_is_degraded(self):
        src = sources.RSSSource('https://example.com/feed', 'down')
        with mock.patch.object(sources, '_get_text', return_value=None):
            items, receipt = src.fetch()
        self.assertEqual(items, [])
        self.assertEqual(receipt['degraded'], 1)

    def test_fetch_normalises_via_get_text(self):
        src = sources.RSSSource('https://example.com/feed', 'wire')
        with mock.patch.object(sources, '_get_text',
                               return_value=RSS20_FIXTURE) as get_text:
            items, receipt = src.fetch(timeout=5, tries=1)
        get_text.assert_called_once_with('https://example.com/feed',
                                         timeout=5, tries=1)
        self.assertEqual(receipt['items'], 2)

    def test_fetch_accepts_pipeline_kwargs(self):
        # documents the intent to drop RSSSource into cmd_run's source list
        src = sources.RSSSource('https://example.com/feed', 'wire')
        with mock.patch.object(sources, '_get_text', return_value=EMPTY_FEED):
            items, receipt = src.fetch(mode='all', window='7d')
        self.assertEqual(receipt['items'], 0)

    def test_default_source_score_is_calibrated(self):
        # feeds carry no popularity score; the default is a neutral prior
        # aligned with the station's measured 32-62 (mean ~47) band
        self.assertEqual(sources.DEFAULT_SOURCE_SCORE, 50)
        items = sources._parse_feed(RSS20_FIXTURE, 'test-feed')
        self.assertEqual(items[0]['source_score'], 50)

    def test_source_score_is_per_source_configurable(self):
        tiered = sources.RSSSource('https://example.com/feed', 'gcores',
                                   source_score=60)
        self.assertEqual(tiered.source_score, 60)
        with mock.patch.object(sources, '_get_text', return_value=RSS20_FIXTURE):
            items, _ = tiered.fetch()
        self.assertEqual({i['source_score'] for i in items}, {60})
        # a zero-score source stays possible (editorial-style opt-out)
        quiet = sources.RSSSource('https://example.com/feed', 'quiet', source_score=0)
        with mock.patch.object(sources, '_get_text', return_value=RSS20_FIXTURE):
            items, _ = quiet.fetch()
        self.assertEqual({i['source_score'] for i in items}, {0})

    def test_named_rss_instances_carry_tiers(self):
        gcores, sspai = sources.GcoresRSSSource(), sources.SspaiRSSSource()
        self.assertEqual((gcores.name, gcores.feed_url, gcores.source_score),
                         ('gcores', 'https://www.gcores.com/rss', 60))
        self.assertEqual((sspai.name, sspai.feed_url, sspai.source_score),
                         ('sspai', 'https://sspai.com/feed', 50))
        with mock.patch.object(sources, '_get_text', return_value=RSS20_FIXTURE):
            items, receipt = gcores.fetch()
        self.assertEqual(items[0]['source'], 'gcores')
        self.assertEqual({i['source_score'] for i in items}, {60})
        self.assertEqual(receipt['source'], 'gcores')


class LLMDraftTest(unittest.TestCase):
    """MemeLLM.draft behaviour, locked without a real API key."""

    DRAFT = {'character': 'gpt', 'title': 'GPT 娘玩梗翻车名场面',
             'summary': '社区二创爆火的表情包梗。',
             'body': '虚构二创正文，长度足够通过 schema 校验。',
             'tags': ['整活'], 'kind': 'community'}

    def _resp(self, content):
        payload = {'choices': [{'message': {'content': content}}]}

        class Resp:
            status = 200

            def read(self):
                return json.dumps(payload).encode('utf-8')

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return Resp()

    def _llm(self):
        env = {'MEME_LLM_BASE_URL': 'https://api.example.com/v1',
               'MEME_LLM_API_KEY': 'test-key', 'MEME_LLM_MODEL': 'test-model'}
        with mock.patch.dict(os.environ, env):
            return llm_mod.MemeLLM()

    def test_is_configured_requires_all_three_vars(self):
        # P2-8: a half-configured env would only produce silent rule mode
        # with a misleading "llm: true" receipt, so it counts as unconfigured
        full = {'MEME_LLM_BASE_URL': 'https://api.example.com/v1',
                'MEME_LLM_API_KEY': 'k', 'MEME_LLM_MODEL': 'm'}
        with mock.patch.dict(os.environ, full):
            self.assertTrue(llm_mod.is_configured())
        for drop in full:
            partial = {k: v for k, v in full.items() if k != drop}
            with mock.patch.dict(os.environ, partial, clear=True):
                self.assertFalse(llm_mod.is_configured())
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(llm_mod.is_configured())

    def test_draft_extracts_json(self):
        model = self._llm()
        content = json.dumps(self.DRAFT, ensure_ascii=False)
        with mock.patch('urllib.request.urlopen', return_value=self._resp(content)):
            draft = model.draft(hot_item())
        self.assertEqual(draft['character'], 'gpt')
        self.assertEqual(draft['kind'], 'community')
        self.assertEqual(draft['title'], self.DRAFT['title'])

    def test_draft_strips_markdown_fence(self):
        fenced = '```json\n' + json.dumps(self.DRAFT, ensure_ascii=False) + '\n```'
        model = self._llm()
        with mock.patch('urllib.request.urlopen', return_value=self._resp(fenced)):
            draft = model.draft(hot_item())
        self.assertEqual(draft['title'], self.DRAFT['title'])

    def test_draft_skip_returns_none(self):
        model = self._llm()
        with mock.patch('urllib.request.urlopen', return_value=self._resp('{"skip": true}')):
            self.assertIsNone(model.draft(hot_item()))

    def test_draft_garbage_returns_none(self):
        model = self._llm()
        with mock.patch('urllib.request.urlopen', return_value=self._resp('这不是 JSON')):
            self.assertIsNone(model.draft(hot_item()))

    def test_draft_timeout_returns_none(self):
        model = self._llm()
        with mock.patch('urllib.request.urlopen', side_effect=TimeoutError('timed out')):
            self.assertIsNone(model.draft(hot_item()))

    def test_draft_bad_payload_shape_returns_none(self):
        model = self._llm()

        class Resp:
            status = 200

            def read(self):
                return json.dumps({'unexpected': True}).encode('utf-8')

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with mock.patch('urllib.request.urlopen', return_value=Resp()):
            self.assertIsNone(model.draft(hot_item()))

    def test_draft_without_full_env_returns_none_without_network(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith('MEME_LLM_')}
        env['MEME_LLM_API_KEY'] = 'k'  # base/model deliberately missing
        with mock.patch.dict(os.environ, env, clear=True):
            model = llm_mod.MemeLLM()
        with mock.patch('urllib.request.urlopen') as opener:
            self.assertIsNone(model.draft(hot_item()))
        opener.assert_not_called()


class ApproveTest(unittest.TestCase):
    """The publisher must produce entries that build.py itself accepts."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ['MEME_INTAKE_DB'] = str(Path(self.tmp.name) / 'intake.db')
        self.store = db_mod.Store()
        # P2-5: approvals must never touch the real content/memes/
        patcher = mock.patch.object(intake, 'MEMES_DIR',
                                    Path(self.tmp.name) / 'memes')
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.store.close()
        os.environ.pop('MEME_INTAKE_DB', None)
        self.tmp.cleanup()

    def _queue_one(self, url='https://example.com/a',
                   title='GPT 娘玩梗翻车名场面'):
        item = hot_item(url=url, title=title)
        scored = radar.score_item(item)
        card = radar.make_lead_card(item, scored)
        fp = db_mod.fingerprint(item['source'], item['url'])
        self.store.add_candidate(dict(item, fp=fp, meme_score=scored['meme_score'],
                                      character=scored['character'],
                                      signals=scored['signals'],
                                      draft=json.dumps(card, ensure_ascii=False)))
        return fp

    def test_approve_writes_schema_valid_entry(self):
        fp = self._queue_one()
        meme_id = 'aihot-' + fp[:10]
        argv = ['intake.py', 'approve', fp[:10]]
        with mock.patch.object(sys, 'argv', argv):
            intake.main()
        target = intake.MEMES_DIR / (meme_id + '.json')
        self.assertTrue(target.exists(), 'entry file not written')
        entry = json.loads(target.read_text(encoding='utf-8'))
        build_mod.validate(entry, target.name)  # canonical gate, must not raise
        self.assertEqual(entry['kind'], 'community')
        self.assertIn('https://example.com/a', [s['url'] for s in entry['sources']])
        self.assertTrue(self.store.is_published(fp))
        # cleanup: the real content dir must stay clean after tests
        target.unlink()

    def test_approve_rejects_bad_id(self):
        fp = self._queue_one()
        with mock.patch.object(sys, 'argv', ['intake.py', 'approve', fp[:10], '--id', 'Bad_ID']):
            with self.assertRaises(SystemExit):
                intake.main()
        self.assertFalse((intake.MEMES_DIR / 'Bad_ID.json').exists())

    def test_double_publish_refused(self):
        fp = self._queue_one()
        with mock.patch.object(sys, 'argv', ['intake.py', 'approve', fp[:10]]):
            intake.main()
        with mock.patch.object(sys, 'argv', ['intake.py', 'approve', fp[:10]]):
            with self.assertRaises(SystemExit):
                intake.main()
        (intake.MEMES_DIR / ('aihot-' + fp[:10] + '.json')).unlink(missing_ok=True)

    def _queue_with_draft(self, draft):
        item = hot_item()
        scored = radar.score_item(item)
        fp = db_mod.fingerprint(item['source'], item['url'])
        self.store.add_candidate(dict(item, fp=fp, meme_score=scored['meme_score'],
                                      character=scored['character'],
                                      signals=scored['signals'],
                                      draft=json.dumps(draft, ensure_ascii=False)))
        return fp

    def test_approve_refuses_invalid_llm_merge(self):
        """An LLM draft that would produce a schema-invalid entry must be
        refused by the build.validate gate; bad data never reaches disk."""
        bad_llm = {'character': 'gpt', 'title': 'x',  # 1 char: below the floor
                   'summary': '短', 'body': '短',
                   'tags': ['梗'], 'kind': 'community'}
        draft = dict(radar.make_lead_card(hot_item()), llm_entry=bad_llm)
        fp = self._queue_with_draft(draft)
        with mock.patch.object(sys, 'argv', ['intake.py', 'approve', fp[:10]]):
            with self.assertRaises(SystemExit) as ctx:
                intake.main()
        self.assertIn('校验', str(ctx.exception))
        target = intake.MEMES_DIR / ('aihot-' + fp[:10] + '.json')
        self.assertFalse(target.exists(), 'invalid entry reached disk')
        self.assertFalse(self.store.is_published(fp))

    def test_approve_folds_codex_from_llm_entry(self):
        llm = {'character': 'codex', 'title': 'Codex 娘整活名场面',
               'summary': '社区二创爆火的表情包梗。',
               'body': '虚构二创正文，长度足够通过 schema 校验。' * 2,
               'tags': ['整活'], 'kind': 'community'}
        draft = dict(radar.make_lead_card(hot_item()), llm_entry=llm)
        fp = self._queue_with_draft(draft)
        meme_id = 'aihot-' + fp[:10]
        target = intake.MEMES_DIR / (meme_id + '.json')
        self.addCleanup(target.unlink, missing_ok=True)
        with mock.patch.object(sys, 'argv', ['intake.py', 'approve', fp[:10]]):
            intake.main()
        entry = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(entry['character'], 'gpt')  # Codex folds into GPT family
        build_mod.validate(entry, target.name)

    def test_approve_ignores_llm_entry_with_unknown_character(self):
        llm = {'character': 'not-a-character', 'title': '应当被忽略的标题',
               'summary': 'x', 'body': 'y', 'tags': [], 'kind': 'original'}
        draft = dict(radar.make_lead_card(hot_item()), llm_entry=llm)
        fp = self._queue_with_draft(draft)
        meme_id = 'aihot-' + fp[:10]
        target = intake.MEMES_DIR / (meme_id + '.json')
        self.addCleanup(target.unlink, missing_ok=True)
        with mock.patch.object(sys, 'argv', ['intake.py', 'approve', fp[:10]]):
            intake.main()
        entry = json.loads(target.read_text(encoding='utf-8'))
        # merge skipped: the rule-mode lead card is published instead
        self.assertEqual(entry['kind'], 'community')
        self.assertEqual(entry['title'], hot_item()['title'])
        self.assertIn('https://example.com/a', [s['url'] for s in entry['sources']])

    def test_approve_refuses_duplicate_character_title(self):
        # P1-1: the same (character, title) may not be published twice,
        # even from two different source URLs
        fp1 = self._queue_one(url='https://example.com/dup-1')
        fp2 = self._queue_one(url='https://example.com/dup-2')
        with mock.patch.object(sys, 'argv', ['intake.py', 'approve', fp1[:10]]):
            intake.main()
        self.assertTrue((intake.MEMES_DIR / ('aihot-' + fp1[:10] + '.json')).exists())
        with mock.patch.object(sys, 'argv', ['intake.py', 'approve', fp2[:10]]):
            with self.assertRaises(SystemExit) as ctx:
                intake.main()
        self.assertIn('冲突', str(ctx.exception))
        self.assertFalse((intake.MEMES_DIR / ('aihot-' + fp2[:10] + '.json')).exists())
        self.assertFalse(self.store.is_published(fp2))

    def test_approve_rejects_windows_reserved_id(self):
        # P2-2: con.json / com1.json are unbindable on the owner's machine
        fp = self._queue_one()
        for bad in ('con', 'com1', 'lpt9'):
            with mock.patch.object(sys, 'argv', ['intake.py', 'approve', fp[:10],
                                                 '--id', bad]):
                with self.assertRaises(SystemExit) as ctx:
                    intake.main()
            self.assertIn('保留设备名', str(ctx.exception))
            self.assertFalse((intake.MEMES_DIR / (bad + '.json')).exists())

    def test_approve_survives_corrupt_draft(self):
        # P2-1: a corrupt draft JSON degrades to a fresh lead card
        item = hot_item(url='https://example.com/corrupt')
        scored = radar.score_item(item)
        fp = db_mod.fingerprint(item['source'], item['url'])
        self.store.add_candidate(dict(item, fp=fp, meme_score=scored['meme_score'],
                                      character=scored['character'],
                                      signals=scored['signals'],
                                      draft='{not valid json'))
        meme_id = 'aihot-' + fp[:10]
        target = intake.MEMES_DIR / (meme_id + '.json')
        self.addCleanup(target.unlink, missing_ok=True)
        with mock.patch.object(sys, 'argv', ['intake.py', 'approve', fp[:10]]):
            intake.main()
        entry = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(entry['kind'], 'community')
        build_mod.validate(entry, meme_id + '.json')

    def test_force_overwrite_creates_backup(self):
        # P2-7: --force keeps the previous version beside the entry
        fp1 = self._queue_one(url='https://example.com/bak-1',
                              title='第一版标题')
        fp2 = self._queue_one(url='https://example.com/bak-2',
                              title='第二版标题')
        meme_id = 'forced-meme'
        target = intake.MEMES_DIR / (meme_id + '.json')
        backup = intake.MEMES_DIR / (meme_id + '.json.bak')
        self.addCleanup(target.unlink, missing_ok=True)
        self.addCleanup(backup.unlink, missing_ok=True)
        with mock.patch.object(sys, 'argv', ['intake.py', 'approve', fp1[:10],
                                             '--id', meme_id]):
            intake.main()
        first = target.read_text(encoding='utf-8')
        with mock.patch.object(sys, 'argv', ['intake.py', 'approve', fp2[:10],
                                             '--id', meme_id, '--force']):
            intake.main()
        self.assertTrue(backup.exists())
        self.assertEqual(backup.read_text(encoding='utf-8'), first)
        entry = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(entry['title'], '第二版标题')

    def test_llm_merge_reattaches_original_link(self):
        # P2-10: the LLM body replaces the rule-mode one, which carried
        # the lead link; the merge must re-attach provenance
        llm = {'character': 'gpt', 'title': 'Codex 娘整活名场面',
               'summary': '社区二创爆火的表情包梗。',
               'body': '一段不含链接的虚构二创正文，长度足够通过 schema 校验。',
               'tags': ['整活'], 'kind': 'community'}
        fp = self._queue_with_draft(dict(radar.make_lead_card(hot_item()),
                                         llm_entry=llm))
        meme_id = 'aihot-' + fp[:10]
        target = intake.MEMES_DIR / (meme_id + '.json')
        self.addCleanup(target.unlink, missing_ok=True)
        with mock.patch.object(sys, 'argv', ['intake.py', 'approve', fp[:10]]):
            intake.main()
        entry = json.loads(target.read_text(encoding='utf-8'))
        self.assertIn('原始线索：https://example.com/a', entry['body'])
        self.assertEqual(entry['body'].count('原始线索'), 1)
        build_mod.validate(entry, meme_id + '.json')

    def test_llm_merge_does_not_duplicate_existing_link(self):
        llm = {'character': 'gpt', 'title': 'Codex 娘整活名场面',
               'summary': '社区二创爆火的表情包梗。',
               'body': ('正文里已经带着 原始线索：https://example.com/a 了，'
                        '长度也足够通过 schema 校验。'),
               'tags': ['整活'], 'kind': 'community'}
        fp = self._queue_with_draft(dict(radar.make_lead_card(hot_item()),
                                         llm_entry=llm))
        meme_id = 'aihot-' + fp[:10]
        target = intake.MEMES_DIR / (meme_id + '.json')
        self.addCleanup(target.unlink, missing_ok=True)
        with mock.patch.object(sys, 'argv', ['intake.py', 'approve', fp[:10]]):
            intake.main()
        entry = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(entry['body'].count('原始线索'), 1)
        build_mod.validate(entry, meme_id + '.json')

    def test_approve_warns_when_db_update_fails_after_write(self):
        # P1-2: the entry is on disk; only the bookkeeping failed
        fp = self._queue_one()
        target = intake.MEMES_DIR / ('aihot-' + fp[:10] + '.json')
        self.addCleanup(target.unlink, missing_ok=True)
        with mock.patch.object(db_mod.Store, 'mark_published',
                               side_effect=sqlite3.OperationalError('database is locked')):
            with mock.patch.object(sys, 'argv', ['intake.py', 'approve', fp[:10]]):
                with self.assertRaises(SystemExit) as ctx:
                    intake.main()
        self.assertIn('数据库状态未更新', str(ctx.exception))
        self.assertTrue(target.exists())


class RunTest(unittest.TestCase):
    """cmd_run: six-source wiring, per-source isolation, abort receipts."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ['MEME_INTAKE_DB'] = str(Path(self.tmp.name) / 'intake.db')
        self.store = db_mod.Store()

    def tearDown(self):
        self.store.close()
        os.environ.pop('MEME_INTAKE_DB', None)
        self.tmp.cleanup()

    def _patch_network(self, get_side_effect):
        patches = [
            mock.patch.object(sources, '_get', side_effect=get_side_effect),
            mock.patch.object(sources, '_get_json', side_effect=get_side_effect),
            mock.patch.object(sources, '_get_text', return_value=RSS20_FIXTURE),
            mock.patch.object(sources, 'EDITORIAL_DIR',
                              Path(self.tmp.name) / 'no-editorial'),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

    def test_existing_pending_and_rejected_never_call_llm_again(self):
        item = hot_item()
        scored = radar.score_item(item)
        fp = db_mod.fingerprint(item['source'], item['url'])
        self.store.add_candidate(dict(item, fp=fp,
            character=scored['character'], signals=scored['signals'],
            meme_score=scored['meme_score']))
        class FixtureSource:
            def fetch(self, **kwargs):
                return [item], {'source': 'fixture', 'items': 1}
        with contextlib.ExitStack() as stack:
            for name in ('AIHotSource', 'EditorialSource', 'BaiduHotSource',
                         'DouyinHotSource', 'GcoresRSSSource', 'SspaiRSSSource'):
                stack.enter_context(mock.patch.object(intake, name, FixtureSource))
            stack.enter_context(mock.patch.object(llm_mod, 'is_configured', return_value=True))
            model = stack.enter_context(mock.patch.object(llm_mod, 'MemeLLM'))
            for status in ('pending', 'rejected'):
                with self.subTest(status=status):
                    self.store.set_status(fp, status)
                    with mock.patch.object(sys, 'argv', ['intake.py', 'run', '--threshold', '0']):
                        with contextlib.redirect_stdout(io.StringIO()):
                            intake.main()
                    model.return_value.draft.assert_not_called()

    def test_run_collects_from_all_sources(self):
        def fake_get(url, timeout=45, tries=3, **kwargs):
            if 'baidu' in url:
                return BAIDU_FIXTURE
            if 'douyin' in url:
                return DOUYIN_FIXTURE
            return {'items': [hot_item()], 'page': {'hasMore': False}}
        self._patch_network(fake_get)
        out = io.StringIO()
        with mock.patch.object(sys, 'argv', ['intake.py', 'run', '--threshold', '0']):
            with contextlib.redirect_stdout(out):
                intake.main()
        summary = json.loads(out.getvalue())
        # aihot 1 + baidu 3 + douyin 2 + gcores 2 + sspai 2
        self.assertEqual(summary['fetched'], 10)
        # The same URL from a second source is fetched but never re-queued.
        self.assertEqual(summary['new'], 7)
        receipts = {r['source']: r for r in
                    json.loads(self.store.last_run()['result'])['receipts']}
        self.assertEqual(set(receipts),
                         {'aihot', 'editorial', 'baidu', 'douyin', 'gcores', 'sspai'})
        self.assertEqual(receipts['baidu']['items'], 3)
        self.assertEqual(receipts['gcores']['items'], 2)
        self.assertEqual({r['source'] for r in self.store.list(limit=100)},
                         {'AIHOT', 'Baidu', 'Douyin', 'gcores'})
        # Both RSS fixtures carry identical URLs: the later source gets a
        # receipt, while the earlier source retains ownership of the URL.
        self.assertEqual(receipts['sspai']['items'], 2)

    def test_run_survives_one_dead_source(self):
        def fake_get(url, timeout=45, tries=3, **kwargs):
            if 'baidu' in url:
                raise RuntimeError('board exploded')
            if 'douyin' in url:
                return DOUYIN_FIXTURE
            return {'items': [hot_item()], 'page': {'hasMore': False}}
        self._patch_network(fake_get)
        out = io.StringIO()
        with mock.patch.object(sys, 'argv', ['intake.py', 'run', '--threshold', '0']):
            with contextlib.redirect_stdout(out):
                intake.main()
        summary = json.loads(out.getvalue())
        self.assertGreater(summary['fetched'], 0)  # the round still completed
        receipts = {r['source']: r for r in
                    json.loads(self.store.last_run()['result'])['receipts']}
        self.assertEqual(receipts['baidu']['degraded'], 1)
        self.assertIn('exploded', receipts['baidu']['error'])
        self.assertEqual(receipts['douyin']['items'], 2)

    def test_run_records_aborted_receipt_on_crash(self):
        def fake_get(url, timeout=45, tries=3, **kwargs):
            return {'items': [hot_item()], 'page': {'hasMore': False}}
        self._patch_network(fake_get)
        with mock.patch.object(db_mod.Store, 'add_candidate',
                               side_effect=sqlite3.OperationalError('disk I/O')):
            with mock.patch.object(sys, 'argv', ['intake.py', 'run', '--threshold', '0']):
                with self.assertRaises(sqlite3.OperationalError):
                    intake.main()
        row = self.store.last_run()
        self.assertIn('ABORTED', row['result'])
        self.assertIn('disk I/O', row['result'])

    def test_run_rejects_non_finite_threshold(self):
        # P2-6: nan/inf would silently poison every score comparison
        # (--threshold=-inf spelled out: a bare -inf looks like a flag)
        for bad in ('nan', 'inf', '--threshold=-inf'):
            argv = (['intake.py', 'run', '--threshold', bad] if bad.startswith('--')
                    else ['intake.py', 'run', '--threshold=' + bad])
            with mock.patch.object(sys, 'argv', argv):
                with self.assertRaises(SystemExit):
                    intake.main()


class AutoTest(unittest.TestCase):
    """The machine gate: written drafts only, every check, no half-writes."""

    # strings proven to pass build.validate in the approve tests above
    DRAFT = {'character': 'gpt', 'title': 'Codex 娘整活名场面',
             'summary': '社区二创爆火的表情包梗。',
             'body': '一段不含链接的虚构二创正文，长度足够通过 schema 校验。',
             'tags': ['整活'], 'kind': 'community'}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ['MEME_INTAKE_DB'] = str(Path(self.tmp.name) / 'intake.db')
        self.store = db_mod.Store()
        # the machine gate must never touch the real content/memes/ either
        patcher = mock.patch.object(intake, 'MEMES_DIR',
                                    Path(self.tmp.name) / 'memes')
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.store.close()
        os.environ.pop('MEME_INTAKE_DB', None)
        self.tmp.cleanup()

    def _queue(self, draft=None, url='https://example.com/a',
               title='GPT 娘玩梗翻车名场面', source='AIHOT', score=None):
        item = hot_item(url=url, title=title)
        item['source'] = source
        scored = radar.score_item(item)
        card = radar.make_lead_card(item, scored)
        if draft is not None:
            card = dict(card, llm_entry=draft)
        fp = db_mod.fingerprint(item['source'], item['url'])
        self.store.add_candidate(dict(
            item, fp=fp,
            meme_score=scored['meme_score'] if score is None else score,
            character=scored['character'], signals=scored['signals'],
            draft=json.dumps(card, ensure_ascii=False)))
        return fp

    def _run_auto(self, *extra):
        out = io.StringIO()
        with mock.patch.object(sys, 'argv', ['intake.py', 'auto'] + list(extra)):
            with contextlib.redirect_stdout(out):
                intake.main()
        return json.loads(out.getvalue())

    def test_auto_bad_body_types_skip_without_crashing(self):
        for index, body in enumerate((None, 42, [], {}, True)):
            with self.subTest(body=body):
                self._queue(draft=dict(self.DRAFT, body=body),
                            url=f'https://example.com/bad-body-{index}')
        report = self._run_auto()
        self.assertEqual(report['published'], [])
        self.assertEqual(len(report['skipped']), 5)

    def test_full_length_body_keeps_original_url_in_sources(self):
        source_url = 'https://example.com/provenance-full-body'
        fp = self._queue(draft=dict(self.DRAFT, body='梗' * 6000), url=source_url)
        report = self._run_auto()
        self.assertEqual(len(report['published']), 1)
        target = intake.MEMES_DIR / (report['published'][0]['id'] + '.json')
        entry = json.loads(target.read_text(encoding='utf-8'))
        self.assertIn(source_url, [source['url'] for source in entry['sources']])
        self.assertEqual(len(entry['body']), 6000)
        build_mod.validate(entry, target.name)
        self.assertTrue(self.store.is_published(fp))

    def test_auto_publishes_written_draft_above_threshold(self):
        fp = self._queue(draft=self.DRAFT)
        report = self._run_auto()
        self.assertEqual(len(report['published']), 1)
        meme_id = report['published'][0]['id']
        self.assertEqual(meme_id, 'aihot-' + fp[:10])   # source-prefixed
        target = intake.MEMES_DIR / (meme_id + '.json')
        self.assertTrue(target.exists())
        entry = json.loads(target.read_text(encoding='utf-8'))
        build_mod.validate(entry, target.name)
        self.assertTrue(self.store.is_published(fp))

    def test_auto_never_publishes_rule_mode_lead_card(self):
        fp = self._queue()   # no llm_entry: a citation stub
        report = self._run_auto()
        self.assertEqual(report['published'], [])
        self.assertEqual(len(report['skipped']), 1)
        self.assertIn('线索卡', report['skipped'][0]['reason'])
        self.assertFalse((intake.MEMES_DIR / ('aihot-' + fp[:10] + '.json')).exists())
        self.assertFalse(self.store.is_published(fp))

    def test_auto_skips_written_draft_below_threshold(self):
        self._queue(draft=self.DRAFT, score=69.9)
        report = self._run_auto()
        self.assertEqual(report['published'], [])
        self.assertEqual(report['below_threshold'], 1)
        self.assertEqual(report['skipped'], [])

    def test_auto_refuses_invalid_llm_merge(self):
        bad = dict(self.DRAFT, title='x', summary='短', body='短')
        fp = self._queue(draft=bad)
        report = self._run_auto()
        self.assertEqual(report['published'], [])
        self.assertTrue(report['skipped'][0]['reason'])
        self.assertFalse((intake.MEMES_DIR / ('aihot-' + fp[:10] + '.json')).exists())
        self.assertFalse(self.store.is_published(fp))

    def test_auto_skips_duplicate_character_title(self):
        fp1 = self._queue(draft=self.DRAFT, url='https://example.com/dup-1')
        fp2 = self._queue(draft=self.DRAFT, url='https://example.com/dup-2')
        report = self._run_auto()
        self.assertEqual(len(report['published']), 1)
        self.assertEqual(len(report['skipped']), 1)
        self.assertIn('冲突', report['skipped'][0]['reason'])
        # equal scores scan newest-first, so either one may win - the
        # contract is that exactly one is published and the other refused
        published_fp = report['published'][0]['fp']
        self.assertIn(published_fp, (fp1[:10], fp2[:10]))
        winner = fp1 if fp1.startswith(published_fp) else fp2
        loser = fp2 if winner is fp1 else fp1
        self.assertTrue(self.store.is_published(winner))
        self.assertFalse(self.store.is_published(loser))

    def test_auto_dry_run_catches_same_round_duplicate(self):
        # without a real write, only in-memory planning sees the clash
        self._queue(draft=self.DRAFT, url='https://example.com/d-1')
        self._queue(draft=self.DRAFT, url='https://example.com/d-2')
        report = self._run_auto('--dry-run')
        self.assertEqual(len(report['published']), 1)
        self.assertEqual(len(report['skipped']), 1)
        self.assertIn('同一轮', report['skipped'][0]['reason'])

    def test_auto_dry_run_writes_nothing(self):
        fp = self._queue(draft=self.DRAFT)
        report = self._run_auto('--dry-run')
        self.assertEqual(len(report['published']), 1)
        self.assertFalse((intake.MEMES_DIR / ('aihot-' + fp[:10] + '.json')).exists())
        self.assertFalse(self.store.is_published(fp))
        self.assertEqual(list(intake.MEMES_DIR.glob('*.json')), [])

    def test_auto_ignores_llm_entry_with_unknown_character(self):
        draft = dict(self.DRAFT, character='not-a-character')
        fp = self._queue(draft=draft)
        report = self._run_auto()
        self.assertEqual(report['published'], [])
        self.assertIn('白名单', report['skipped'][0]['reason'])
        self.assertFalse(self.store.is_published(fp))

    def test_auto_id_is_source_prefixed(self):
        fp = self._queue(draft=self.DRAFT, source='gcores')
        report = self._run_auto()
        self.assertEqual(report['published'][0]['id'], 'gcores-' + fp[:10])

    def test_auto_hard_stops_when_db_update_fails_after_write(self):
        # P1-2 semantics: file on disk + queue unaware = stop, don't plough on
        fp = self._queue(draft=self.DRAFT)
        target = intake.MEMES_DIR / ('aihot-' + fp[:10] + '.json')
        self.addCleanup(target.unlink, missing_ok=True)
        with mock.patch.object(db_mod.Store, 'mark_published',
                               side_effect=sqlite3.OperationalError('database is locked')):
            with mock.patch.object(sys, 'argv', ['intake.py', 'auto']):
                with self.assertRaises(SystemExit) as ctx:
                    intake.main()
        self.assertIn('数据库状态未更新', str(ctx.exception))
        self.assertTrue(target.exists())


class DotenvTest(unittest.TestCase):
    """The mini-dotenv loader: scheduled runs have no shell profile."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env_path = Path(self.tmp.name) / '.env'

    def _write(self, text):
        self.env_path.write_text(text, encoding='utf-8')
        return str(self.env_path)

    def test_loads_key_value_pairs(self):
        path = self._write('MEME_LLM_BASE_URL=https://api.example.com/v1\n'
                           'MEME_LLM_API_KEY=test-key\n')
        with mock.patch.dict(os.environ, {}, clear=True):
            loaded = intake._load_dotenv(path)
            self.assertEqual(loaded, 2)
            self.assertEqual(os.environ['MEME_LLM_BASE_URL'],
                             'https://api.example.com/v1')
            self.assertEqual(os.environ['MEME_LLM_API_KEY'], 'test-key')

    def test_real_environment_wins(self):
        # setdefault semantics: an explicit env var beats the file, so
        # `MEME_LLM_MODEL=xyz intake.py run` still overrides for one round
        path = self._write('MEME_LLM_MODEL=from-file\n')
        with mock.patch.dict(os.environ, {'MEME_LLM_MODEL': 'from-shell'}):
            intake._load_dotenv(path)
            self.assertEqual(os.environ['MEME_LLM_MODEL'], 'from-shell')

    def test_missing_file_is_noop(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(intake._load_dotenv(str(self.env_path)), 0)

    def test_comments_blanks_and_quotes(self):
        path = self._write('# comment\n\nMEME_LLM_BASE_URL="https://q/v1"\n'
                           "MEME_LLM_MODEL='m1'\nnot-a-kv-line\n")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(intake._load_dotenv(path), 2)
            self.assertEqual(os.environ['MEME_LLM_BASE_URL'], 'https://q/v1')
            self.assertEqual(os.environ['MEME_LLM_MODEL'], 'm1')

    def test_main_loads_dotenv_before_dispatch(self):
        # the scheduled task has no profile: main() must load .env itself
        self._write('MEME_LLM_BASE_URL=https://api.example.com/v1\n'
                    'MEME_LLM_API_KEY=test-key\nMEME_LLM_MODEL=test-model\n')
        env = {k: v for k, v in os.environ.items() if not k.startswith('MEME_LLM_')}
        env['MEME_INTAKE_DB'] = str(Path(self.tmp.name) / 'intake.db')
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(intake, 'HERE', Path(self.tmp.name)):
                with mock.patch.object(sys, 'argv', ['intake.py', 'stats']):
                    with contextlib.redirect_stdout(io.StringIO()):
                        intake.main()
            self.assertTrue(llm_mod.is_configured())


class BackfillTest(unittest.TestCase):
    """backfill: lead cards queued before a key existed get LLM drafts."""

    DRAFT = {'character': 'gpt', 'title': 'GPT 娘玩梗翻车名场面',
             'summary': '社区二创爆火的表情包梗。',
             'body': '一段不含链接的虚构二创正文，长度足够通过 schema 校验。',
             'tags': ['整活'], 'kind': 'community'}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ['MEME_INTAKE_DB'] = str(Path(self.tmp.name) / 'intake.db')
        self.store = db_mod.Store()
        patcher = mock.patch.object(intake, 'MEMES_DIR',
                                    Path(self.tmp.name) / 'memes')
        patcher.start()
        self.addCleanup(patcher.stop)
        # neutralize the real services/meme-intake/.env (it holds the live key)
        loader = mock.patch.object(intake, '_load_dotenv', return_value=0)
        loader.start()
        self.addCleanup(loader.stop)

    def tearDown(self):
        self.store.close()
        os.environ.pop('MEME_INTAKE_DB', None)
        self.tmp.cleanup()

    def _queue(self, draft=None, url='https://example.com/a',
               title='GPT 娘玩梗翻车名场面'):
        item = hot_item(url=url, title=title)
        scored = radar.score_item(item)
        card = radar.make_lead_card(item, scored)
        if draft is not None:
            card = dict(card, llm_entry=draft)
        fp = db_mod.fingerprint(item['source'], item['url'])
        self.store.add_candidate(dict(
            item, fp=fp, meme_score=scored['meme_score'],
            character=scored['character'], signals=scored['signals'],
            draft=json.dumps(card, ensure_ascii=False)))
        return fp

    def _fake_llm(self, draft=None):
        class FakeLLM:
            def __init__(self):
                self.calls = []

            def draft(self, item):
                self.calls.append(item)
                return draft
        fake = FakeLLM()
        return fake, mock.patch.object(llm_mod, 'MemeLLM', return_value=fake)

    def _run_backfill(self, *extra):
        out = io.StringIO()
        with mock.patch.object(sys, 'argv', ['intake.py', 'backfill'] + list(extra)):
            with contextlib.redirect_stdout(out):
                intake.main()
        return json.loads(out.getvalue())

    def _run_auto(self, *extra):
        out = io.StringIO()
        with mock.patch.object(sys, 'argv', ['intake.py', 'auto'] + list(extra)):
            with contextlib.redirect_stdout(out):
                intake.main()
        return json.loads(out.getvalue())

    def test_backfill_drafts_lead_card(self):
        fp = self._queue()
        fake, patcher = self._fake_llm(self.DRAFT)
        env = {'MEME_LLM_BASE_URL': 'https://api.example.com/v1',
               'MEME_LLM_API_KEY': 'k', 'MEME_LLM_MODEL': 'm'}
        with patcher, mock.patch.dict(os.environ, env):
            report = self._run_backfill()
        self.assertEqual(report['scanned'], 1)
        self.assertEqual(report['drafted'], 1)
        self.assertEqual(len(fake.calls), 1)
        card = json.loads(self.store.get(fp)['draft'])
        self.assertEqual(card['llm_entry']['title'], self.DRAFT['title'])
        self.assertEqual(card['note'], 'LLM 草稿，待人工核实。')
        # the lead card's score/status must not be disturbed
        row = self.store.get(fp)
        self.assertEqual(row['status'], 'pending')

    def test_backfill_skips_already_drafted(self):
        fp = self._queue(draft=self.DRAFT)
        fake, patcher = self._fake_llm(self.DRAFT)
        env = {'MEME_LLM_BASE_URL': 'https://api.example.com/v1',
               'MEME_LLM_API_KEY': 'k', 'MEME_LLM_MODEL': 'm'}
        with patcher, mock.patch.dict(os.environ, env):
            report = self._run_backfill()
        self.assertEqual(report['already_drafted'], 1)
        self.assertEqual(report['drafted'], 0)
        self.assertEqual(fake.calls, [])   # idempotent: no wasted API calls

    def test_backfill_dry_run_writes_nothing(self):
        fp = self._queue()
        fake, patcher = self._fake_llm(self.DRAFT)
        env = {'MEME_LLM_BASE_URL': 'https://api.example.com/v1',
               'MEME_LLM_API_KEY': 'k', 'MEME_LLM_MODEL': 'm'}
        with patcher, mock.patch.dict(os.environ, env):
            report = self._run_backfill('--dry-run')
        self.assertEqual(report['drafted'], 1)
        self.assertTrue(report['dry_run'])
        card = json.loads(self.store.get(fp)['draft'])
        self.assertNotIn('llm_entry', card)

    def test_backfill_requires_a_key(self):
        self._queue()
        env = {k: v for k, v in os.environ.items() if not k.startswith('MEME_LLM_')}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(sys, 'argv', ['intake.py', 'backfill']):
                with self.assertRaises(SystemExit) as ctx:
                    intake.main()
        self.assertIn('LLM 未配置', str(ctx.exception))

    def test_backfill_counts_failed_drafts(self):
        fp = self._queue()
        fake, patcher = self._fake_llm(None)   # model declined / timed out
        env = {'MEME_LLM_BASE_URL': 'https://api.example.com/v1',
               'MEME_LLM_API_KEY': 'k', 'MEME_LLM_MODEL': 'm'}
        with patcher, mock.patch.dict(os.environ, env):
            report = self._run_backfill()
        self.assertEqual(report['failed'], 1)
        self.assertEqual(report['drafted'], 0)
        card = json.loads(self.store.get(fp)['draft'])
        self.assertNotIn('llm_entry', card)

    def test_backfilled_draft_is_publishable_by_the_machine_gate(self):
        # the whole point: a backfilled card clears the auto gate end to end
        fp = self._queue()
        fake, patcher = self._fake_llm(self.DRAFT)
        env = {'MEME_LLM_BASE_URL': 'https://api.example.com/v1',
               'MEME_LLM_API_KEY': 'k', 'MEME_LLM_MODEL': 'm'}
        with patcher, mock.patch.dict(os.environ, env):
            self._run_backfill()
        with mock.patch.dict(os.environ, env):
            report = self._run_auto()
        self.assertEqual(len(report['published']), 1)
        self.assertTrue(self.store.is_published(fp))


if __name__ == '__main__':
    unittest.main()
