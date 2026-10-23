"""Meme intake storage. SQLite, stdlib only.

Three tables:
- candidates: one row per collected signal, with meme_score and review status.
- published:  fingerprint of everything ever approved; the anti-duplicate gate.
- runs:       one receipt per pipeline run (fetch counters, result json).

Design notes (carried over from the intelligence-station playbook):
- Fingerprints are source+url based (sha1 of both), never row ids, so a
  re-fetch of the same item is idempotent across DB rebuilds.
- runs.result is written only after the run finishes; an aborted run leaves
  the previous row on disk. Never read it as this run's outcome.
- WAL + busy_timeout: the daily scheduled run and a human `approve` can
  overlap; without them a second writer gets an instant "database is
  locked". WAL failure degrades quietly (some filesystems refuse it).
"""
import datetime
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / 'data' / 'intake.db'

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    fp           TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    url          TEXT NOT NULL,
    title        TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT '',
    category     TEXT NOT NULL DEFAULT '',
    source_score INTEGER NOT NULL DEFAULT 0,
    published_at TEXT NOT NULL DEFAULT '',
    discovered_at TEXT NOT NULL,
    character    TEXT NOT NULL DEFAULT '',
    signals      TEXT NOT NULL DEFAULT '[]',
    meme_score   REAL NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','approved','rejected','published')),
    draft        TEXT NOT NULL DEFAULT '',
    reject_reason TEXT NOT NULL DEFAULT '',
    reviewed_at  TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidates(status, meme_score DESC);
CREATE TABLE IF NOT EXISTS published (
    fp           TEXT PRIMARY KEY,
    meme_id      TEXT NOT NULL,
    published_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '',
    fetched     INTEGER NOT NULL DEFAULT 0,
    new         INTEGER NOT NULL DEFAULT 0,
    result      TEXT NOT NULL DEFAULT ''
);
"""


def fingerprint(source: str, url: str) -> str:
    return hashlib.sha1((source + '\x00' + url).encode('utf-8')).hexdigest()


def canonical_url(url):
    parts = urlsplit(url)
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                   if not k.lower().startswith('utm_') and k.lower() not in ('fbclid', 'gclid'))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or '/',
                       urlencode(query), ''))


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds')


class Store:
    def __init__(self, path=None):
        # Resolved at call time, not import time: tests point MEME_INTAKE_DB
        # at a temp file in setUp, after this module has been imported.
        env_path = os.environ.get('MEME_INTAKE_DB')
        self.path = Path(path or env_path or DEFAULT_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # timeout + busy_timeout: a scheduled run and a manual approve can
        # overlap; wait for the lock instead of failing instantly.
        self.db = sqlite3.connect(str(self.path), timeout=30)
        self.db.row_factory = sqlite3.Row
        for pragma in ('PRAGMA journal_mode=WAL;', 'PRAGMA busy_timeout=30000;'):
            try:
                self.db.execute(pragma)
            except sqlite3.Error:
                pass  # WAL unavailable on this filesystem: degrade quietly
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- runs -----------------------------------------------------------
    def run_start(self, run_id):
        self.db.execute(
            'INSERT INTO runs (run_id, started_at) VALUES (?, ?)',
            (run_id, now_iso()))
        self.db.commit()

    def run_finish(self, run_id, fetched, new, result):
        """Result is persisted only now; an aborted run keeps the old row."""
        self.db.execute(
            "UPDATE runs SET finished_at=?, fetched=?, new=?, result=? WHERE run_id=?",
            (now_iso(), fetched, new, json.dumps(result, ensure_ascii=False), run_id))
        self.db.commit()

    def last_run(self):
        row = self.db.execute(
            'SELECT * FROM runs ORDER BY started_at DESC LIMIT 1').fetchone()
        return dict(row) if row else None

    # ---- candidates -----------------------------------------------------
    def add_candidate(self, row: dict) -> bool:
        """Insert one candidate. Returns False when the fingerprint exists."""
        fp = row['fp']
        try:
            self.db.execute(
                """INSERT INTO candidates
                   (fp, source, url, title, summary, category, source_score,
                    published_at, discovered_at, character, signals, meme_score,
                    draft, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (fp, row['source'], row['url'], row['title'], row.get('summary', ''),
                 row.get('category', ''), int(row.get('source_score', 0)),
                 row.get('published_at', ''), row.get('discovered_at', now_iso()),
                 row.get('character', ''), json.dumps(row.get('signals', []), ensure_ascii=False),
                 float(row.get('meme_score', 0)), row.get('draft', ''), now_iso()))
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def update_draft(self, fp, draft_json: str) -> bool:
        """Replace a candidate's draft (backfill: lead card -> LLM draft).

        Returns False when the fingerprint does not exist. Never touches
        status: a backfilled candidate stays in whatever gate state it was.
        """
        cur = self.db.execute('UPDATE candidates SET draft=? WHERE fp=?',
                              (draft_json, fp))
        self.db.commit()
        return cur.rowcount > 0

    def is_published(self, fp) -> bool:
        return self.db.execute(
            'SELECT 1 FROM published WHERE fp=?', (fp,)).fetchone() is not None

    def published_fingerprints(self):
        return {r['fp'] for r in self.db.execute('SELECT fp FROM published')}

    def get(self, fp):
        row = self.db.execute('SELECT * FROM candidates WHERE fp=?', (fp,)).fetchone()
        return dict(row) if row else None

    def has_url(self, url):
        canonical = canonical_url(url)
        return any(canonical_url(r['url']) == canonical
                   for r in self.db.execute('SELECT url FROM candidates'))

    def list(self, status=None, min_score=0.0, limit=50):
        sql = 'SELECT * FROM candidates WHERE meme_score>=?'
        args = [min_score]
        if status:
            sql += ' AND status=?'
            args.append(status)
        sql += ' ORDER BY meme_score DESC, created_at DESC LIMIT ?'
        args.append(limit)
        return [dict(r) for r in self.db.execute(sql, args)]

    def set_status(self, fp, status, reason=''):
        self.db.execute(
            "UPDATE candidates SET status=?, reject_reason=?, reviewed_at=? WHERE fp=?",
            (status, reason, now_iso(), fp))
        self.db.commit()

    def mark_published(self, fp, meme_id):
        self.db.execute(
            'INSERT OR IGNORE INTO published (fp, meme_id, published_at) VALUES (?,?,?)',
            (fp, meme_id, now_iso()))
        self.db.execute("UPDATE candidates SET status='published' WHERE fp=?", (fp,))
        self.db.commit()

    def stats(self):
        out = {}
        for r in self.db.execute('SELECT status, COUNT(*) n FROM candidates GROUP BY status'):
            out[r['status']] = r['n']
        out['published_total'] = self.db.execute('SELECT COUNT(*) n FROM published').fetchone()['n']
        return out
