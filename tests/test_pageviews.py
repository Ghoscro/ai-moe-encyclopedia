import concurrent.futures
import contextlib
import datetime as dt
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from unittest.mock import patch
from http.server import ThreadingHTTPServer

spec = importlib.util.spec_from_file_location('pageviews', Path(__file__).resolve().parents[1]/'services/pageviews.py')
pv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pv)


class PageviewsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root/'site/claude').mkdir(parents=True)
        (self.root/'site/index.html').write_text('home')
        (self.root/'site/claude/index.html').write_text('character')
        self.db = self.root/'stats.sqlite3'
        self.store = pv.Store(self.db,self.root/'site')

    def tearDown(self):
        self.tmp.cleanup()

    def test_real_counts_dedup_restart_and_online_restore(self):
        event = str(uuid.uuid4())
        self.assertEqual(self.store.record('/',event)['total'],1)
        self.assertEqual(self.store.record('/',event)['total'],1)
        with self.assertRaises(ValueError):
            self.store.record('/claude/',event)
        self.assertEqual(self.store.record('/claude/',str(uuid.uuid4()))['page'],1)
        restored = self.root/'restored.sqlite3'
        with self.store.connect() as src, contextlib.closing(sqlite3.connect(restored)) as dst:
            src.backup(dst)
        self.assertEqual(pv.Store(restored,self.root/'site').read('/'),self.store.read('/'))
        self.assertEqual(pv.Store(self.db,self.root/'site').read('/')['total'],2)

    def test_beijing_day_and_concurrent_updates(self):
        stamp = dt.datetime(2026,10,22,15,59,59,tzinfo=dt.timezone.utc).timestamp()
        self.store.record('/',str(uuid.uuid4()),stamp)
        result = self.store.record('/',str(uuid.uuid4()),stamp+2)
        self.assertEqual((result['total'],result['today'],result['day']),(2,1,'2026-10-23'))
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _:self.store.record('/claude/',str(uuid.uuid4())),range(32)))
        self.assertEqual(self.store.read('/claude/')['page'],32)

    def test_only_existing_canonical_routes(self):
        for path in ['/', '/claude/']:
            self.assertTrue(self.store.valid_path(path))
        for path in ['/missing/','/../','//','/claude/?q=a','/claude/#meme','/INDEX.HTML',None]:
            self.assertFalse(self.store.valid_path(path))

    def test_event_capacity_expiry_and_persistent_aggregates(self):
        with patch.object(pv,'MAX_EVENTS',1):
            self.store.record('/',str(uuid.uuid4()),100000)
            with self.assertRaises(OverflowError):
                self.store.record('/',str(uuid.uuid4()),100001)
            self.assertEqual(self.store.record('/',str(uuid.uuid4()),200000)['total'],2)

    def test_http_validation_readonly_and_failure(self):
        server = ThreadingHTTPServer(('127.0.0.1',0),pv.Handler)
        server.store = self.store
        thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        url = 'http://127.0.0.1:'+str(server.server_port)+'/api/stats'
        def request(data=None, origin=pv.ORIGIN, content='application/json', suffix=''):
            req = urllib.request.Request(url+suffix,data=data,headers={'Origin':origin,'Content-Type':content,'User-Agent':'Browser Test'})
            try:
                with urllib.request.urlopen(req,timeout=3) as response:
                    return response.status,json.load(response)
            except urllib.error.HTTPError as error:
                with error:
                    return error.code,json.load(error)
        try:
            payload = json.dumps({'path':'/','event':str(uuid.uuid4())}).encode()
            self.assertEqual(request(suffix='?path=%2F')[1]['total'],0)
            self.assertEqual(request(payload)[1]['total'],1)
            self.assertEqual(request(payload)[1]['total'],1)
            self.assertEqual(request(payload,origin='https://evil.invalid')[0],403)
            self.assertEqual(request(payload,content='text/plain')[0],415)
            self.assertEqual(request(b'x'*1025)[0],413)
            self.assertEqual(request(b'[]')[0],400)
            self.assertEqual(request(suffix='?path=%2Fmissing%2F')[0],400)
            self.assertEqual(request(suffix='?path=%2F')[1]['total'],1)
            with self.store.connect() as db:
                db.execute('DROP TABLE counts')
            self.assertEqual(request(suffix='?path=%2F')[0],503)
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
