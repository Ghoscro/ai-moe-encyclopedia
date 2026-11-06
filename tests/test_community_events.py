import sys,unittest,datetime
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/meme-intake'))
import bilibili_source as b,community,editorial
class CommunityEventTests(unittest.TestCase):
 def test_exact_video_url_only(self):
  for u in ('http://www.bilibili.com/video/BV1Jfnu6jEe6/','https://www.bilibili.com.evil/video/BV1Jfnu6jEe6/','https://user@www.bilibili.com/video/BV1Jfnu6jEe6/','https://www.bilibili.com/video/BV1Jfnu6jEe6/?redirect=x','https://127.0.0.1/video/BV1Jfnu6jEe6/'):
   self.assertIsNone(b.video_id(u))
  self.assertEqual(b.video_id('https://www.bilibili.com/video/BV1Jfnu6jEe6/'),'BV1Jfnu6jEe6')
 def test_metadata_identity_and_scope(self):
  d={'code':0,'data':{'bvid':'BV1Jfnu6jEe6','title':'Claude announces a community account','owner':{'name':'Claude creator'},'desc':'Human reviews publication.','pubdate':1792303807}}
  with patch.object(community,'get',return_value=d):
   e=b.evidence('https://www.bilibili.com/video/BV1Jfnu6jEe6/')
   self.assertEqual(e['evidence_scope'],'original_video_metadata_only')
   self.assertIn('Human reviews publication.',e['text'])
   d['data']['bvid']='BV1pGnn67EC2'
   self.assertIsNone(b.evidence('https://www.bilibili.com/video/BV1Jfnu6jEe6/'))
 def test_event_quotes_without_invented_question_answer(self):
  item={'character':'claude','summary':'Claude creator announces a new account. Human reviews publication before pressing publish.'}
  result={'eligible':True,'story_kind':'community_event','source_type':'community_event','original_source':True,'evidence_quote':'Human reviews publication','event_action_quote':'announces a new account','event_outcome_quote':'Human reviews publication','model_identity_quote':'Claude creator','comic_turn':'The human becomes the final reviewer.','reason':'Roles switch in the actual announcement.'}
  self.assertTrue(editorial.selection_passes(result,item))
  result['model_identity_quote']='GPT creator'
  self.assertFalse(editorial.selection_passes(result,item))
  result['model_identity_quote']='Claude creator';result['event_outcome_quote']='Invented viral success'
  self.assertFalse(editorial.selection_passes(result,item))
