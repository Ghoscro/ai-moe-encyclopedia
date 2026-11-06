import sys, json, os, tempfile, unittest, urllib.error
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/meme-intake'))
from llm import MemeLLM, ModelUnavailable
class RoutingTests(unittest.TestCase):
 def test_rotation_and_failover(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'routing.json'; seen=[]
   class Response:
    def __enter__(self): return self
    def __exit__(self,*a): pass
    def read(self,*a): return json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]}).encode()
   def request(req,**kw):
    seen.append(req.get_header('X-mbdeck-backend'))
    self.assertEqual(kw['timeout'],180)
    self.assertFalse(json.loads(req.data)['chat_template_kwargs']['enable_thinking'])
    if len(seen)==3: raise urllib.error.URLError('offline')
    return Response()
   with patch.dict(os.environ,{'MEME_LLM_BACKENDS':'mac1-ds1017,mac2-ds1017','MEME_LLM_ROUTING_FILE':str(path),'MEME_LLM_BASE_URL':'https://test.invalid/v1','MEME_LLM_API_KEY':'private-key'}),patch('urllib.request.urlopen',side_effect=request):
    for _ in range(3): self.assertEqual(MemeLLM().request({}),'{}')
   self.assertEqual(seen,['mac1-ds1017','mac2-ds1017','mac1-ds1017','mac2-ds1017'])
   self.assertNotIn('private-key',path.read_text())
 def test_both_unavailable_raise(self):
  with patch.dict(os.environ,{'MEME_LLM_BACKENDS':'mac1-ds1017,mac2-ds1017','MEME_LLM_ROUTING_FILE':'','MEME_LLM_BASE_URL':'https://test.invalid'}),patch('urllib.request.urlopen',side_effect=TimeoutError) as call:
   with self.assertRaises(ModelUnavailable): MemeLLM().editorial_json('test',{})
   self.assertEqual(call.call_count,2)
