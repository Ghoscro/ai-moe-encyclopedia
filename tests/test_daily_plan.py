import sys,json,tempfile,unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/meme-intake'))
import daily_plan
class DailyPlanTests(unittest.TestCase):
 def test_verified_only_cst_day_dedup_withdrawn(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);e={'id':'a','character':'gpt'}
   r={'stage':'live','verified_at':'2026-10-27T16:01:00+00:00','entries':[e]}
   (p/'release-a.json').write_text(json.dumps(r));(p/'pipeline.json').write_text(json.dumps(r))
   self.assertEqual(daily_plan.live_counts(p,'2026-10-28')['gpt'],1)
   self.assertEqual(daily_plan.live_counts(p,'2026-10-27')['gpt'],0)
   (p/'release-withdrawn.json').write_text(json.dumps({'withdrawn_ids':['a']}))
   self.assertEqual(daily_plan.live_counts(p,'2026-10-28')['gpt'],0)
 def test_draft_is_not_daily_completion(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);(p/'pipeline.json').write_text(json.dumps({'stage':'pr','entries':[{'id':'a','character':'gpt'}]}))
   data=daily_plan.write_coverage(p,[{'character':'gpt','status':'pending'}])
   self.assertEqual(data['characters']['gpt']['status'],'awaiting_review')
   self.assertEqual(data['characters']['doubao']['status'],'source_gap')
   self.assertEqual(len(data['characters']),14)
 def test_source_failures_are_bounded_and_rotate(self):
  with tempfile.TemporaryDirectory() as t:
   with mock.patch.object(daily_plan.urllib.request,'build_opener') as opener:
    opener.return_value.open.side_effect=OSError('offline')
    daily_plan.discover_archives(mock.Mock(),t)
    self.assertEqual(opener.return_value.open.call_count,3)
    b=daily_plan.discover_archives(mock.Mock(),t)
    self.assertEqual(len(b['characters']),6)
    self.assertTrue(all(x['status']=='source_unavailable' for x in b['characters'].values()))
