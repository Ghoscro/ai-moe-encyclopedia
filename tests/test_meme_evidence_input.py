import sys, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/meme-intake'))
import origin, editorial, llm
class EvidenceInputTests(unittest.TestCase):
    def test_html_inline_entity_quote(self):
        text='supports <a>multiplayer</a> for two devices. (Update: also <a>online multiplayer</a>; audio induced headaches!)'
        self.assertEqual(origin.plain_text(text),'supports multiplayer for two devices. (Update: also online multiplayer; audio induced headaches!)')
        self.assertEqual(origin.plain_text('feet &amp; pedals'),'feet & pedals')
    def test_scripts_not_evidence(self):
        self.assertEqual(origin.plain_text('<script>invented punchline</script><p>Real report.</p>'),'Real report.')
    def test_retry_is_bounded_and_exact(self):
        row={'title':'Example','summary':'The audio multiplayer induced headaches!','url':'https://simonwillison.net/a'}
        bad={'eligible':True,'original_source':True,'source_type':'amusing_incident','evidence_quote':'The players exploded!', 'reason':'Specific surprising effect','comic_turn':'Expected a game, got headaches'}
        good=dict(bad,evidence_quote=row['summary']);model=llm.MemeLLM()
        with mock.patch.object(model,'editorial_json',side_effect=[bad,good]) as call:
            result=model.select(row)
        self.assertEqual(call.call_count,2)
        self.assertTrue(editorial.selection_passes(result,row))
        with mock.patch.object(model,'editorial_json',return_value=bad) as call:
            result=model.select(row)
        self.assertEqual(call.call_count,2)
        self.assertFalse(editorial.selection_passes(result,row))
    def test_news_not_retried(self):
        model=llm.MemeLLM()
        with mock.patch.object(model,'editorial_json',return_value={'eligible':False}) as call:
            model.select({'title':'News'})
        self.assertEqual(call.call_count,1)

    def test_secondary_source_can_only_discover_not_approve(self):
        row={'summary':'This is an exact evidence quotation.'}
        result={'eligible':True,'original_source':False,'source_type':'amusing_incident',
                'evidence_quote':row['summary'],'comic_turn':'A concrete surprising event','reason':'Specific observed funny result'}
        self.assertTrue(editorial.discovery_passes(result,row))
        self.assertFalse(editorial.selection_passes(result,row))
    def test_explicit_source_resolution_is_bounded_and_allowlisted(self):
        row={'url':'https://simonwillison.net/quote','summary':'<a href="http://127.0.0.1/a">bad</a><a href="https://www.404media.co/story/">author</a>'}
        with mock.patch.object(origin,'fetch',return_value={'url':'https://www.404media.co/story/','text':'Original'}) as fetch:
            result=origin.resolve(row,False)
        fetch.assert_called_once_with('https://www.404media.co/story/')
        self.assertEqual(result['discovery_url'],row['url'])
    def test_missing_original_never_falls_back_to_aggregator(self):
        with mock.patch.object(origin,'fetch') as fetch:
            self.assertIsNone(origin.resolve({'url':'https://other.test','summary':'No original link'},False))
        fetch.assert_not_called()
