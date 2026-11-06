"""Public player posts: bounded discovery and independently refetched originals.

Search/RSS results are leads only. No login, browser bypass, or model tools.
"""
import datetime as dt
import hashlib
import json
import re
import urllib.parse as up
import urllib.request as ur
import xml.etree.ElementTree as ET
from pathlib import Path
import origin

ALIASES = {
    'claude': ('Claude',), 'doubao': ('豆包', 'Doubao'),
    'gpt': ('ChatGPT', 'GPT', 'Codex'), 'deepseek': ('DeepSeek',),
    'kimi': ('Kimi', '月之暗面'), 'glm': ('GLM', '智谱', 'ChatGLM'),
    'grok': ('Grok',), 'qwen': ('Qwen', '通义千问', '千问'),
    'gemini': ('Gemini',), 'mistral': ('Mistral', 'Le Chat'),
    'minimax': ('MiniMax', '海螺'), 'ernie': ('ERNIE', '文心一言', '文心'),
    'muse-spark': ('Muse Spark', 'Meta AI'),
    'hy4-preview': ('Hunyuan', '混元', '腾讯元宝'),
}
ATOM = {'a': 'http://www.w3.org/2005/Atom'}


def get(url, json_body=False):
    # All callers construct URLs from fixed hosts and escaped identifiers.
    req = ur.Request(url, headers={'User-Agent': 'AIHER-player-story-intake/1.0'})
    with ur.build_opener(origin.NoRedirect()).open(req, timeout=12) as r:
        raw = r.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('oversized source')
    text = raw.decode('utf-8')
    return json.loads(text) if json_body else text


def matches(character, text):
    return any(re.search(r'(?<![a-zA-Z])' + re.escape(a) + r'(?![a-zA-Z])', text, re.I)
               for a in ALIASES[character])


def reddit_id(url):
    p = up.urlsplit(url)
    if p.scheme != 'https' or p.hostname != 'www.reddit.com' or p.port not in (None, 443) or p.username:
        return None
    m = re.fullmatch(r'/r/[A-Za-z0-9_]+/comments/([a-z0-9]+)(?:/[^/]*)?/?', p.path)
    return m.group(1) if m else None


def parse_reddit(xml):
    rows = []
    for e in ET.fromstring(xml).findall('a:entry', ATOM):
        link = e.find('a:link', ATOM)
        url = link.get('href', '') if link is not None else ''
        if not reddit_id(url):
            continue
        rows.append({'url': url, 'title': e.findtext('a:title', '', ATOM),
                     'summary': origin.plain_text(e.findtext('a:content', '', ATOM)),
                     'published_at': e.findtext('a:published', '', ATOM),
                     'author': e.findtext('a:author/a:name', '', ATOM)})
    return rows


def supported(url):
    try:
        p = up.urlsplit(url)
        if p.scheme != 'https' or p.port not in (None, 443) or p.username or p.password:
            return False
        return bool(reddit_id(url) or
            (p.hostname == 'www.v2ex.com' and re.fullmatch(r'/t/\d+', p.path)) or
            (p.hostname == 'news.ycombinator.com' and p.path == '/item' and
             re.fullmatch(r'id=\d+', p.query)))
    except ValueError:
        return False


def evidence(url):
    """Return only the exact post/comment, never neighboring replies or search text."""
    if not supported(url):
        return None
    try:
        p = up.urlsplit(url)
        if reddit_id(url):
            rows = parse_reddit(get('https://www.reddit.com' + p.path.rstrip('/') + '/.rss'))
            row = next((r for r in rows if reddit_id(r['url']) == reddit_id(url)), None)
            if not row:
                return None
        elif p.hostname == 'www.v2ex.com':
            result = get('https://www.v2ex.com/api/topics/show.json?id=' + p.path.split('/')[-1], True)
            row0 = result[0] if isinstance(result, list) and len(result) == 1 else {}
            if str(row0.get('id')) != p.path.split('/')[-1]:
                return None
            row = {'title': row0.get('title', ''), 'summary': row0.get('content', ''),
                   'published_at': dt.datetime.fromtimestamp(row0['created'], dt.timezone.utc).isoformat(),
                   'author': row0.get('member', {}).get('username', '')}
        else:
            ident = up.parse_qs(p.query)['id'][0]
            result = get('https://hacker-news.firebaseio.com/v0/item/' + ident + '.json', True)
            if not result or str(result.get('id')) != ident or result.get('deleted') or result.get('dead'):
                return None
            # Only comments/self posts. External news titles are not personal stories.
            if result.get('type') not in ('comment', 'story') or not result.get('text'):
                return None
            row = {'title': result.get('title', 'Player comment'), 'summary': origin.plain_text(result['text']),
                   'published_at': dt.datetime.fromtimestamp(result['time'], dt.timezone.utc).isoformat(),
                   'author': result.get('by', '')}
        text = origin.plain_text(row['title'] + ' ' + row['summary'])[:6000]
        if len(text) < 80 or not row.get('published_at'):
            return None
        return {'version': origin.VERSION, 'url': url, 'text': text,
                'published_at': row['published_at'], 'author': row.get('author', ''),
                'sha256': hashlib.sha256(text.encode()).hexdigest()}
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError):
        return None


def discover(store, runtime, counts, target=10, now=None):
    """Rotate four underfilled roles per run; each role refreshed every four hours.

    Up to 100 Reddit + 50 HN leads/role, plus a shared Chinese community feed.
    Persist retrieval receipts even on errors; failed sources retry after one hour.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    path = Path(runtime) / 'community-discovery.json'
    state = json.loads(path.read_text('utf-8')) if path.exists() else {'characters': {}}
    roles = state.setdefault('characters', {})
    def due(c):
        old = roles.get(c, {})
        age = now.timestamp() - old.get('checked_epoch', 0)
        return counts.get(c, 0) < target and age >= (3600 if old.get('failed') else 14400)
    missing = sorted((c for c in ALIASES if due(c)), key=lambda c: (roles.get(c, {}).get('checked_epoch', 0), counts.get(c, 0)))[:4]
    if not missing:
        return state
    v2rows = []
    v2error = None
    try:
        for r in get('https://www.v2ex.com/api/topics/latest.json', True):
            v2rows.append({'url': 'https://www.v2ex.com/t/' + str(int(r['id'])),
                'title': r.get('title', ''), 'summary': r.get('content', ''),
                'published_at': dt.datetime.fromtimestamp(r['created'], dt.timezone.utc).isoformat()})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        v2error = type(exc).__name__
    import db
    for character in missing:
        queries = ' OR '.join('"' + a + '"' for a in ALIASES[character])
        receipts = []; leads = list(v2rows)
        for name, url in (
            ('reddit', 'https://www.reddit.com/search.rss?' + up.urlencode({'q': queries, 'sort': 'new', 't': 'month', 'limit': 100})),
            ('v2ex-search', 'https://www.sov2ex.com/api/search?' + up.urlencode({'q': {'doubao':'豆包','ernie':'文心一言','hy4-preview':'混元','muse-spark':'Meta AI'}.get(character, ALIASES[character][0]), 'sort': 'created', 'size': 50})),
            ('hn', 'https://hn.algolia.com/api/v1/search_by_date?' + up.urlencode({'query': ALIASES[character][0], 'tags': 'comment', 'hitsPerPage': 50}))):
            if name == 'reddit' and now.timestamp() < state.get('reddit_after', 0):
                receipts.append({'source': name, 'status': 'cooldown'})
                continue
            try:
                if name == 'reddit':
                    rows = parse_reddit(get(url))
                    state['reddit_after'] = now.timestamp() + 90
                elif name == 'v2ex-search':
                    # Search index is discovery only; original API is fetched again before review.
                    rows = []
                    for hit in get(url, True).get('hits', []):
                        r = hit['_source']
                        rows.append({'url': 'https://www.v2ex.com/t/' + str(int(r['id'])),
                            'title': r.get('title', ''), 'summary': r.get('content', ''),
                            'published_at': dt.datetime.fromtimestamp(float(hit['sort'][0])/1000, dt.timezone.utc).isoformat()})
                else:
                    rows = [{'url': 'https://news.ycombinator.com/item?id=' + str(r['objectID']),
                             'title': r.get('story_title') or 'Player comment',
                             'summary': origin.plain_text(r.get('comment_text') or ''),
                             'published_at': r.get('created_at', '')} for r in get(url, True).get('hits', [])]
                leads.extend(rows)
                receipts.append({'source': name, 'received': len(rows), 'status': 'ok'})
            except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as exc:
                status = getattr(exc, 'code', None)
                if name == 'reddit':
                    state['reddit_after'] = now.timestamp() + 3600
                receipts.append({'source': name, 'status': 'unavailable', 'error': type(exc).__name__, 'http_status': status})
        added = 0
        for row in leads:
            text = row['title'] + ' ' + row['summary']
            if not matches(character, text) or len(row['summary']) < 80 or not supported(row['url']) or store.has_url(row['url']):
                continue
            # Unknown dates cannot enter the original-story queue.
            try:
                stamp = dt.datetime.fromisoformat(row['published_at'].replace('Z', '+00:00'))
                if stamp.tzinfo is None or stamp > now:
                    continue
            except (ValueError, TypeError):
                continue
            source = 'player-community-' + character
            card = {'suggested_character': character, 'source_url': row['url'],
                    'source_title': row['title'], 'note': '玩家原帖线索；待重新抓取原文与独立审核。'}
            if store.add_candidate(dict(row, fp=db.fingerprint(source, row['url']), source=source,
                    character=character, meme_score=40, draft=json.dumps(card, ensure_ascii=False))):
                added += 1
        roles[character] = {'checked_epoch': now.timestamp(), 'checked_at': now.isoformat(),
                            'added': added, 'sources': receipts, 'v2ex_error': v2error,
                            'failed': any(r['status'] != 'ok' for r in receipts)}
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), 'utf-8'); tmp.replace(path)
    return state
