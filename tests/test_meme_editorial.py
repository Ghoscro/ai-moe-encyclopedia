import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/meme-intake'))
import editorial
import pipeline
import db

class EditorialTests(unittest.TestCase):
    def setUp(self):
        patcher=mock.patch.object(pipeline.daily_plan,'discover_archives',return_value={})
        patcher.start();self.addCleanup(patcher.stop)
        self.item={'title':'模型拒绝加班名场面','summary':'用户让模型写自动回复，模型回答本人正在休假，自动回复也在休假。'}
        self.selection={'eligible':True,'original_source':True,'source_type':'amusing_incident','evidence_quote':'本人正在休假，自动回复也在休假','comic_turn':'自动回复本来替人值班，却也跟着休假','reason':'替班与同时休假的具体反差'}
        self.draft={'body':'【虚构二创】本人正在休假，自动回复也在休假。'}
        self.report={k:True for k in ('approved','grounded','has_payoff','character_fit','concise','not_news_in_costume','visual_story')}
        self.report.update(payoff_quote='本人正在休假，自动回复也在休假',reason='替班对象也休假，落点与开场形成反差')
    def test_repost_or_unknown_origin_refused(self):
        for value in (False, None, 'true'):
            self.assertFalse(editorial.selection_passes(dict(self.selection, original_source=value), self.item))
    def test_character_round_robin_preserves_quality_order(self):
        rows=[{'character':c,'id':i} for i,c in enumerate(['gpt','gpt','claude','gpt','doubao'])]
        self.assertEqual([r['id'] for r in editorial.balanced_candidates(rows)],[0,2,4,1,3])
        self.assertEqual(editorial.balanced_candidates([]),[])
    def test_concrete_source_supported_incident_can_pass(self):
        self.assertTrue(editorial.selection_passes(self.selection,self.item))
    def test_news_cannot_pass_by_eligible_flag(self):
        self.assertFalse(editorial.selection_passes(dict(self.selection,source_type='ordinary_news'),self.item))
    def test_invented_source_evidence_refused(self):
        self.assertFalse(editorial.selection_passes(dict(self.selection,evidence_quote='这是模型凭空编造的具体笑点'),self.item))
    def test_missing_or_string_booleans_refused(self):
        for value in (None,{},dict(self.selection,eligible='true')):
            self.assertFalse(editorial.selection_passes(value,self.item))
    def test_specific_short_payoff_passes(self):
        self.assertTrue(editorial.review_passes(self.report,self.draft))
    def test_any_failed_quality_dimension_refuses(self):
        for key in ('grounded','has_payoff','character_fit','concise','not_news_in_costume','visual_story'):
            self.assertFalse(editorial.review_passes(dict(self.report,**{key:False}),self.draft))
    def test_approval_alone_is_not_quality_review(self):
        self.assertFalse(editorial.review_passes({'approved':True},self.draft))
    def test_quoted_payoff_must_exist(self):
        self.assertFalse(editorial.review_passes(dict(self.report,payoff_quote='完全不在正文中出现的笑点'),self.draft))
    def test_overlong_story_refused(self):
        self.assertFalse(editorial.review_passes(self.report,{'body':self.draft['body']+'啊'*801}))
    def test_cycle_caps_and_character_distribution(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {'MEME_INTAKE_DB':str(Path(tmp)/'queue.db')}):
            with db.Store() as store:
                for i, character in enumerate(['gpt','gpt','claude','doubao','kimi']):
                    store.add_candidate(dict(self.item, fp=str(i)*40, source='fixture', url=f'https://example.com/{i}', character=character, meme_score=99-i, draft=json.dumps({'llm_entry':dict(self.draft,character=character)})))
            model=mock.Mock()
            model.select.return_value=self.selection
            model.review_report.return_value=self.report
            model.draft.side_effect=lambda row: dict(self.draft,character=row.get('character'))
            with mock.patch.object(pipeline.origin,'fetch',side_effect=lambda url: {'version':pipeline.origin.VERSION,'url':url,'text':self.item['summary'],'sha256':'fixture'}), mock.patch.object(pipeline,'command',return_value=''), mock.patch.object(pipeline.intake,'cmd_run'), mock.patch.object(pipeline.llm,'is_configured',return_value=True), mock.patch.object(pipeline.llm,'MemeLLM',return_value=model), mock.patch.object(pipeline.llm,'valid_draft',side_effect=lambda d: bool(d)), mock.patch.object(pipeline.intake,'_entry_from_card',return_value={}), mock.patch.object(pipeline.intake,'_merge_llm',side_effect=lambda base,row,card: {'character':row['character']}), mock.patch.object(pipeline.intake,'_prewrite_checks',return_value=None):
                result=pipeline.prepare(SimpleNamespace(prepare_only=True,limit=5),Path(tmp)/'receipt.json')
            self.assertEqual([e['character'] for e in result['entries']], ['gpt','claude','doubao'])
            self.assertEqual(model.review_report.call_count,3)

    def test_high_score_news_does_not_reach_writer(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ,{'MEME_INTAKE_DB':str(Path(tmp)/'queue.db')}):
            with db.Store() as store:
                store.add_candidate({'fp':'a'*40,'source':'fixture','url':'https://example.com/news','character':'gpt','title':'模型发布428B参数','summary':'只发布参数和下载地址','meme_score':99,'draft':'{}'})
            model=mock.Mock()
            model.select.return_value={'eligible':False,'source_type':'ordinary_news','reason':'只有参数发布，无具体笑点'}
            with mock.patch.object(pipeline.origin,'fetch',side_effect=lambda url: {'version':pipeline.origin.VERSION,'url':url,'text':self.item['summary'],'sha256':'fixture'}), mock.patch.object(pipeline,'command',return_value=''), mock.patch.object(pipeline.intake,'cmd_run'), mock.patch.object(pipeline.llm,'is_configured',return_value=True), mock.patch.object(pipeline.llm,'MemeLLM',return_value=model):
                result=pipeline.prepare(SimpleNamespace(prepare_only=True,limit=3),Path(tmp)/'receipt.json')
            self.assertIsNone(result)
            model.draft.assert_not_called()
            model.review_report.assert_not_called()
            self.assertEqual(json.loads((Path(tmp)/'receipt.json').read_text())['stage'],'empty')


    def test_aggregator_approval_cannot_override_original_rejection(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {'MEME_INTAKE_DB':str(Path(tmp)/'queue.db')}):
            with db.Store() as store:
                store.add_candidate(dict(self.item, fp='z'*40, source='fixture',
                    url='https://simonwillison.net/test/', character='claude', meme_score=99, draft='{}'))
            model=mock.Mock()
            model.select.side_effect=[self.selection, {'eligible':False,'reason':'原文没有黑白线稿笑点'}]
            with mock.patch.object(pipeline,'command',return_value=''), mock.patch.object(pipeline.intake,'cmd_run'), mock.patch.object(pipeline.llm,'is_configured',return_value=True), mock.patch.object(pipeline.llm,'MemeLLM',return_value=model), mock.patch.object(pipeline.origin,'fetch',return_value={'version':pipeline.origin.VERSION,'url':'https://simonwillison.net/test/','text':'The original shows a colorful oil painting.','sha256':'test'}):
                self.assertIsNone(pipeline.prepare(SimpleNamespace(prepare_only=True,limit=3),Path(tmp)/'receipt.json'))
            model.draft.assert_not_called()
            self.assertEqual(model.select.call_count,2)

if __name__=='__main__': unittest.main()
