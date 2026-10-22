"""First-party aggregate pageviews; stdlib only, HTTP over a local Unix socket."""
import argparse
import contextlib
import datetime as dt
import json
import os
from pathlib import Path
import re
import socketserver
import sqlite3
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

TZ = dt.timezone(dt.timedelta(hours=8))
ORIGIN = 'https://aiher.ai'
EVENT = re.compile(r'^[a-f0-9-]{36}$')
PATH = re.compile(r'^/(?:[a-z0-9-]+/)*$')
MAX_EVENTS = 50000


class Store:
    def __init__(self, database, site):
        self.database, self.site = str(database), Path(site)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS counts(day TEXT, path TEXT, n INTEGER NOT NULL,
                    PRIMARY KEY(day,path));
                CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, path TEXT NOT NULL, ts INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
            db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)',
                       ('started_at', dt.datetime.now(TZ).isoformat()))

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def valid_path(self, path):
        if not isinstance(path, str) or len(path) > 160 or not PATH.fullmatch(path):
            return False
        root = self.site.resolve()
        target = (root / path.lstrip('/') / 'index.html').resolve()
        return root in target.parents and target.is_file()

    def stats(self, db, path, stamp):
        day = dt.datetime.fromtimestamp(stamp, TZ).date().isoformat()
        total, today, page = db.execute('''SELECT COALESCE(SUM(n),0),
            COALESCE(SUM(CASE WHEN day=? THEN n ELSE 0 END),0),
            COALESCE(SUM(CASE WHEN path=? THEN n ELSE 0 END),0) FROM counts''', (day,path)).fetchone()
        return dict(total=total, today=today, page=page,
                    started_at=db.execute("SELECT value FROM meta WHERE key='started_at'").fetchone()[0],
                    day=day)

    def read(self, path, stamp=None):
        with self.connect() as db:
            return self.stats(db, path, time.time() if stamp is None else stamp)

    def record(self, path, event, stamp=None):
        stamp = time.time() if stamp is None else stamp
        day = dt.datetime.fromtimestamp(stamp, TZ).date().isoformat()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM events WHERE ts < ?', (int(stamp)-86400,))
            old = db.execute('SELECT path FROM events WHERE id=?', (event,)).fetchone()
            if old and old[0] != path:
                raise ValueError('Event reused for another page')
            if not old:
                if db.execute('SELECT COUNT(*) FROM events').fetchone()[0] >= MAX_EVENTS:
                    raise OverflowError('Daily event capacity reached')
                db.execute('INSERT INTO events VALUES (?,?,?)', (event,path,int(stamp)))
                db.execute('''INSERT INTO counts VALUES (?,?,1)
                    ON CONFLICT(day,path) DO UPDATE SET n=n+1''', (day,path))
            result = self.stats(db,path,stamp)
        return result


class Handler(BaseHTTPRequestHandler):
    server_version = 'AIHERStats'

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass  # No per-request IP/UA or event-body logs.

    def reply(self, status, value):
        data = json.dumps(value, separators=(',',':')).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(data)))
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        self.wfile.write(data)

    def handle_request(self, write=False):
        try:
            url = urlsplit(self.path)
            if url.path != '/api/stats':
                return self.reply(404, {'error':'not_found'})
            if write:
                if self.headers.get('Origin') != ORIGIN:
                    return self.reply(403, {'error':'origin'})
                if self.headers.get('Content-Type','').split(';')[0].strip() != 'application/json':
                    return self.reply(415, {'error':'content_type'})
                length = int(self.headers.get('Content-Length','0'))
                if not 0 < length <= 1024 or self.headers.get('Transfer-Encoding'):
                    return self.reply(413, {'error':'size'})
                if re.search(r'bot|spider|crawler|slurp',self.headers.get('User-Agent',''),re.I):
                    return self.reply(403, {'error':'crawler'})
                value = json.loads(self.rfile.read(length))
                if not isinstance(value,dict) or set(value) != {'path','event'}:
                    return self.reply(400, {'error':'payload'})
                path, event = value['path'], value['event']
                if not isinstance(event,str) or not EVENT.fullmatch(event):
                    return self.reply(400, {'error':'event'})
            else:
                query = parse_qs(url.query)
                if set(query) != {'path'} or len(query['path']) != 1:
                    return self.reply(400, {'error':'query'})
                path = query['path'][0]
            if not self.server.store.valid_path(path):
                return self.reply(400, {'error':'path'})
            result = self.server.store.record(path,event) if write else self.server.store.read(path)
            return self.reply(200,result)
        except (ValueError, TypeError, UnicodeError):
            return self.reply(400, {'error':'invalid'})
        except (sqlite3.Error, OSError, OverflowError):
            return self.reply(503, {'error':'temporarily_unavailable'})

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request(True)


class Server(socketserver.ThreadingMixIn, getattr(socketserver, 'UnixStreamServer', socketserver.TCPServer)):
    daemon_threads = True
    request_queue_size = 16


def main():
    if not hasattr(socketserver, 'UnixStreamServer'):
        raise SystemExit('Production requires a Unix socket')
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', default='/var/lib/aiher-stats/stats.sqlite3')
    parser.add_argument('--socket', default='/run/aiher-stats/http.sock')
    parser.add_argument('--site', default='/www/wwwroot/ai-moe-encyclopedia/current')
    args = parser.parse_args()
    store = Store(args.database,args.site)
    socket = Path(args.socket)
    if socket.exists():
        socket.unlink()
    with Server(str(socket),Handler) as server:
        os.chmod(socket,0o660)
        server.store = store
        server.serve_forever()


if __name__ == '__main__':
    main()
