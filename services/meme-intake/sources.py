"""Signal sources for the meme radar.

Adapters today:

- AIHotSource: public REST API of the family intelligence station
  (aihot.virxact.com). This is where "what is hot in AI right now" comes
  from. All three known traps from aihot-dashboard-pipeline are honoured:
  browser UA (nginx blocks default curl), sequential pagination with
  content validation (a degraded 200 has no items), and backoff retry.
- EditorialSource: the encyclopedia's own character pages, read from
  content/editorial/*.json. Used for character-name aliases and to know
  which characters already have editorial coverage.
- BaiduHotSource / DouyinHotSource: hot-search board JSON APIs. Raw
  platform counters are never trusted as scores - ranks go through
  hotsearch_score() so the board stays discriminating after radar's
  score/100 clamp.
- RSSSource: RSS 2.0 / Atom feeds, parsed with the stdlib XML parser,
  plus the named instances GcoresRSSSource / SspaiRSSSource. Feeds carry
  no popularity score, so each instance passes its tier via source_score.

Adding a source = implement fetch() returning RawItem dicts; nothing else
in the pipeline cares where an item came from.
"""
import datetime
import email.utils
import json
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from pathlib import Path

BROWSER_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
              'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

ENCYCLOPEDIA_ROOT = Path(__file__).resolve().parents[2]
EDITORIAL_DIR = ENCYCLOPEDIA_ROOT / 'content' / 'editorial'

AIHOT_BASE = 'https://aihot.virxact.com'


def _as_text(value):
    """Coerce a hostile payload field to a clean string.

    Third-party APIs drift: a title arrives as None or a number, a date as
    an epoch int. Garbage reads as '' - the pipeline's prefilter drops
    URL/title-less items and freshness treats a blank date as "recent".
    """
    return value.strip() if isinstance(value, str) else ''


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _epoch_to_iso(value):
    """Epoch seconds -> ISO string; anything unparseable -> ''."""
    try:
        when = datetime.datetime.fromtimestamp(float(value), datetime.timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return ''
    return when.isoformat()


def hotsearch_score(rank, n, is_top=False):
    """Hot-search board rank -> source_score (0-100).

    Shared by the hot-search adapters (Baidu / Douyin / Bilibili boards):
    the leader scores 95, decaying linearly to 40 at the tail of the
    board ("it was on the board" keeps a floor), and a pinned (is_top)
    entry scores 100. rank is 1-based; a single-entry board scores 95
    (no division by zero). Out-of-range rank is clamped into [1, n].

    Callers must NOT pass raw platform counters: radar clamps
    score/100 into [0, 1], so a Douyin-style hot_value in the millions
    would flatten to a perfect 1.0 and destroy discrimination. Per-item
    normalisation should still be wrapped in try/except, as _parse_feed
    does, so one dirty entry cannot take down the batch.
    """
    if is_top:
        return 100.0
    if n <= 1:
        return 95.0
    rank = max(1, min(int(rank), n))  # dirty board data must not blow up
    return max(40.0, 95.0 - (rank - 1) * 55.0 / (n - 1))


def _get(url, timeout=45, tries=3):
    """Fetch JSON with backoff. Returns parsed body or None (never raises)."""
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': BROWSER_UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    continue
                body = json.loads(resp.read().decode('utf-8'))
                # Degraded-response guard: a valid payload has items or says
                # it is the last page; anything else is treated as failure.
                if isinstance(body, dict) and ('items' in body or 'report' in body):
                    return body
        except Exception:
            pass
        if i < tries - 1:
            time.sleep(1.2 * (2 ** i))
    return None


def _get_json(url, timeout=30, tries=3, extra_headers=None):
    """Fetch any JSON body with backoff - no payload-shape guard.

    _get carries the aihot-specific degraded-response guard (items/report
    keys); board APIs have their own shapes (data.cards / data.word_list),
    so they use this plain variant and validate the payload themselves.
    extra_headers covers board APIs that insist on provenance: Douyin's
    WAF answers 200-with-empty-body unless Referer says /hot. Returns the
    parsed body or None; never raises.
    """
    headers = {'User-Agent': BROWSER_UA}
    if extra_headers:
        headers.update(extra_headers)
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    continue
                return json.loads(resp.read().decode('utf-8'))
        except Exception:
            pass
        if i < tries - 1:
            time.sleep(1.2 * (2 ** i))
    return None


def _get_text(url, timeout=30, tries=3):
    """Fetch raw text with backoff (feeds are XML, not JSON).

    Same browser-UA discipline as _get: the nginx in front of most feed
    hosts blocks the default urllib UA. Returns the body or None; never
    raises.
    """
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': BROWSER_UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    continue
                return resp.read().decode('utf-8', errors='replace')
        except Exception:
            pass
        if i < tries - 1:
            time.sleep(1.2 * (2 ** i))
    return None


class AIHotSource:
    name = 'aihot'

    def fetch(self, mode='all', window='7d', limit=100, max_pages=40):
        """Sequential pagination (concurrency is what triggers degradation).

        Returns (items, receipt). Items are normalised RawItem dicts.
        """
        items, cursor, pages, degraded = [], None, 0, 0
        while pages < max_pages:
            url = f'{AIHOT_BASE}/api/public/items?mode={mode}&window={window}&limit={limit}'
            if cursor:
                url += '&cursor=' + urllib.request.quote(cursor)
            body = _get(url)
            pages += 1
            if not body:
                degraded += 1
                break
            page_items = body.get('items') or []
            page = body.get('page') or {}
            if not page_items and page.get('hasMore'):
                degraded += 1  # short non-final page = degraded response
                break
            for it in page_items:
                if not isinstance(it, dict):
                    continue  # hostile payload: a non-object entry is skipped
                items.append({
                    'source': 'AIHOT',
                    'url': _as_text(it.get('url') or it.get('permalink')),
                    'title': _as_text(it.get('title')),
                    'summary': _as_text(it.get('summary')),
                    'category': _as_text(it.get('category')),
                    'source_score': _as_int(it.get('score') or 0),
                    'published_at': _as_text(it.get('publishedAt')),
                    'discovered_at': _as_text(it.get('discoveredAt')),
                })
            if not page.get('hasMore') or not page.get('nextCursor'):
                break
            cursor = page['nextCursor']
            time.sleep(0.4)  # stay gentle; station robots.txt notes aihot_api_ip_rl=60r/m
            # (an earlier comment said 600/min from the API docs; the robots
            # note is the conservative figure - calibrate to it before raising frequency)
        receipt = {'source': self.name, 'mode': mode, 'pages': pages,
                   'items': len(items), 'degraded': degraded}
        return items, receipt


class BaiduHotSource:
    """Baidu realtime hot-search board (JSON API).

    Verified shape: data.cards[].content[].content[] with word / url /
    isTop - the card's content is a LIST of groups, each wrapping the
    word list (a bare dict is tolerated for robustness). There is no
    popularity counter, so ranks go through hotsearch_score() - rank is
    cumulative across cards, isTop pins at 100. A 200 with no words
    counts as degraded (a real board never is).
    """
    name = 'baidu'
    URL = 'https://top.baidu.com/api/board?platform=wise&tab=realtime'

    def fetch(self, timeout=30, tries=3, **_pipeline_kwargs):
        body = _get_json(self.URL, timeout=timeout, tries=tries)
        entries = []
        if isinstance(body, dict):
            data = body.get('data')
            cards = data.get('cards') if isinstance(data, dict) else None
            if isinstance(cards, list):
                for card in cards:
                    if not isinstance(card, dict):
                        continue  # bad card is skipped, not fatal
                    content = card.get('content')
                    if isinstance(content, dict):
                        groups = [content]
                    elif isinstance(content, list):
                        groups = content
                    else:
                        continue
                    for group in groups:
                        if not isinstance(group, dict):
                            continue
                        for it in group.get('content') or []:
                            if not isinstance(it, dict):
                                continue
                            word = _as_text(it.get('word'))
                            url = _as_text(it.get('url'))
                            if word and url:
                                entries.append((word, url, bool(it.get('isTop'))))
        n = len(entries)
        items = [{
            'source': 'Baidu',
            'url': url,
            'title': word,
            'summary': '',
            'category': 'hotsearch',
            'source_score': hotsearch_score(i + 1, n, is_top),
            'published_at': '',
            'discovered_at': '',
        } for i, (word, url, is_top) in enumerate(entries)]
        receipt = {'source': self.name, 'items': n, 'degraded': 0 if n else 1}
        return items, receipt


class DouyinHotSource:
    """Douyin hot-search board (JSON API).

    Verified shape: data.word_list[] with word / position / event_time.
    The raw hot_value is a millions-scale counter that radar's score/100
    clamp would flatten to a perfect 1.0 - position goes through
    hotsearch_score() instead. event_time is epoch seconds -> ISO.
    trending_list is deliberately not consumed (pinned editorial picks,
    not a ranked board). HEADERS: the WAF answers 200-with-empty-body
    unless Referer says /hot - verified against the live API.
    """
    name = 'douyin'
    URL = ('https://www.douyin.com/aweme/v1/web/hot/search/list/'
           '?device_platform=webapp&aid=6383')
    HEADERS = {'Referer': 'https://www.douyin.com/hot'}

    def fetch(self, timeout=30, tries=3, **_pipeline_kwargs):
        body = _get_json(self.URL, timeout=timeout, tries=tries,
                         extra_headers=self.HEADERS)
        words = []
        if isinstance(body, dict):
            data = body.get('data')
            listed = data.get('word_list') if isinstance(data, dict) else None
            if isinstance(listed, list):
                words = [it for it in listed if isinstance(it, dict)]
        entries = []
        for position, it in enumerate(words, start=1):
            word = _as_text(it.get('word'))
            if not word:
                continue
            rank = _as_int(it.get('position'), position)  # dirty rank: list order
            entries.append((word, rank, _epoch_to_iso(it.get('event_time'))))
        n = len(entries)
        items = [{
            'source': 'Douyin',
            'url': 'https://www.douyin.com/search/' + urllib.parse.quote(word),
            'title': word,
            'summary': '',
            'category': 'hotsearch',
            'source_score': hotsearch_score(rank, n),
            'published_at': published,
            'discovered_at': '',
        } for word, rank, published in entries]
        receipt = {'source': self.name, 'items': n, 'degraded': 0 if n else 1}
        return items, receipt


class EditorialSource:
    name = 'editorial'

    def fetch(self):
        rows = []
        if not EDITORIAL_DIR.is_dir():
            return rows, {'source': self.name, 'items': 0}
        for f in sorted(EDITORIAL_DIR.glob('*.json')):
            try:
                doc = json.loads(f.read_text(encoding='utf-8'))
            except Exception:
                continue
            slug = f.stem.split('-')[0]
            rows.append({
                'source': 'editorial',
                'url': f'https://aiher.ai/{slug}/',
                'title': doc.get('title') or f.stem,
                'summary': doc.get('summary') or '',
                'category': 'editorial',
                'source_score': 0,
                'published_at': '',
                'discovered_at': '',
            })
        return rows, {'source': self.name, 'items': len(rows)}


# ---- RSS / Atom (stdlib XML parsing) ---------------------------------------

# Feeds carry no popularity score of their own, so every RSS item gets a
# neutral prior. 50 is calibrated against the intelligence station's
# measured 0-100 band (32-62, mean ~47); tier a feed up or down with
# source_score= at construction. NOTE: radar clamps score/100 into [0, 1],
# so this value must already be normalised to 0-100 (raw platform counters
# like Douyin's hot_value would clamp to a flat 1.0 - pre-scale them).
DEFAULT_SOURCE_SCORE = 50


def _local(tag):
    """Strip the XML namespace: '{http://www.w3.org/2005/Atom}entry' -> 'entry'."""
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else ''


def _child_text(elem, name):
    for child in elem:
        if _local(child.tag) == name:
            return ''.join(child.itertext()).strip()
    return ''


def _link_of(elem):
    """Entry permalink. Atom keeps it in the href attribute, RSS in text.

    Atom feeds also carry rel=self / rel=enclosure links that are not the
    article, so alternate wins and anything else is only a last resort.
    """
    fallback = ''
    for child in elem:
        if _local(child.tag) != 'link':
            continue
        rel = (child.get('rel') or 'alternate').strip().lower()
        href = (child.get('href') or '').strip() or ''.join(child.itertext()).strip()
        if not href:
            continue
        if rel == 'alternate':
            return href
        if rel not in ('self', 'enclosure', 'replies', 'edit', 'hub', 'via') and not fallback:
            fallback = href
    if fallback:
        return fallback
    for child in elem:  # RSS 2.0 guid, when it is a permalink
        if _local(child.tag) == 'guid':
            text = ''.join(child.itertext()).strip()
            if text.startswith('http'):
                return text
    return ''


def _category_of(elem):
    """RSS keeps the category in text, Atom in the term attribute."""
    for child in elem:
        if _local(child.tag) == 'category':
            term = (child.get('term') or '').strip() or ''.join(child.itertext()).strip()
            if term:
                return term
    return ''


def _to_iso(raw):
    """RFC 822 (pubDate) or ISO 8601 (updated/published) -> ISO string.

    An unparseable date is not an error: freshness scoring already treats
    an empty published_at as "assume recent", so the item stays in play.
    """
    if not raw:
        return ''
    raw = raw.strip()
    when = None
    try:
        when = email.utils.parsedate_to_datetime(raw)  # Tue, 21 Oct 2025 ...
    except (TypeError, ValueError):
        pass
    if when is None:
        try:
            when = datetime.datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except ValueError:
            return ''
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return when.isoformat()


def _normalise_entry(elem, source_name, source_score=DEFAULT_SOURCE_SCORE):
    """One feed entry -> RawItem dict, or None when it is unusable."""
    title = _child_text(elem, 'title')
    url = _link_of(elem)
    if not title or not url:
        return None  # no title or no permalink = not a lead
    summary = (_child_text(elem, 'description') or _child_text(elem, 'summary')
               or _child_text(elem, 'content') or _child_text(elem, 'encoded'))
    published = (_child_text(elem, 'pubDate') or _child_text(elem, 'published')
                 or _child_text(elem, 'updated') or _child_text(elem, 'date'))
    return {
        'source': source_name,
        'url': url,
        'title': title,
        'summary': summary,
        'category': _category_of(elem),
        'source_score': source_score,
        'published_at': _to_iso(published),
        'discovered_at': '',
    }


def _parse_feed(xml_text, source_name, source_score=DEFAULT_SOURCE_SCORE):
    """Parse RSS 2.0 / Atom (RSS 1.0 as a bonus) into RawItem dicts.

    Returns None when the payload is not readable XML at all (the caller
    counts that as a degraded fetch, same philosophy as the station's
    degraded-response guard), [] when the feed is legitimately empty.
    A single malformed entry is skipped, never fatal.
    """
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return None
    root_name = _local(root.tag)
    if root_name == 'feed':                      # Atom
        entries = [e for e in root if _local(e.tag) == 'entry']
    elif root_name == 'rss':                     # RSS 2.0
        entries = [e for c in root if _local(c.tag) == 'channel'
                   for e in c if _local(e.tag) == 'item']
    else:                                        # RSS 1.0 / RDF and friends
        entries = [e for e in root.iter() if _local(e.tag) in ('item', 'entry')]
    items = []
    for entry in entries:
        try:
            item = _normalise_entry(entry, source_name, source_score)
        except Exception:
            continue  # one broken entry must not take down the batch
        if item:
            items.append(item)
    return items


class RSSSource:
    """RSS 2.0 / Atom feed adapter (protocol layer: point it at any URL).

    fetch() downloads the feed, parses it with the stdlib XML parser and
    normalises entries into the same RawItem shape AIHotSource returns.
    Feeds have no popularity score, so items get a neutral source_score
    prior (DEFAULT_SOURCE_SCORE); tier a feed with source_score= at
    construction. No third-party dependencies; a feed that will not parse
    counts as degraded, mirroring the station's degraded-response guard.
    """
    name = 'rss'

    def __init__(self, feed_url, source_name='rss', source_score=DEFAULT_SOURCE_SCORE):
        self.feed_url = feed_url
        self.name = source_name or 'rss'
        self.source_score = source_score

    def fetch(self, timeout=30, tries=2, **_pipeline_kwargs):
        """Return (items, receipt). Accepts and ignores the pipeline's
        mode/window kwargs so the adapter can join cmd_run's source list
        without special-casing."""
        receipt = {'source': self.name, 'items': 0, 'degraded': 0}
        text = _get_text(self.feed_url, timeout=timeout, tries=tries)
        if text is None:
            receipt['degraded'] = 1
            return [], receipt
        items = _parse_feed(text, self.name, self.source_score)
        if items is None:
            receipt['degraded'] = 1
            return [], receipt
        receipt['items'] = len(items)
        return items, receipt


class GcoresRSSSource(RSSSource):
    """机核 RSS - 二次元/游戏文化社区，梗密度最高，tier 60."""
    name = 'gcores'
    URL = 'https://www.gcores.com/rss'

    def __init__(self):
        super().__init__(self.URL, self.name, source_score=60)


class SspaiRSSSource(RSSSource):
    """少数派 RSS - 数码效率内容，中性档（对齐情报站均值），tier 50."""
    name = 'sspai'
    URL = 'https://sspai.com/feed'

    def __init__(self):
        super().__init__(self.URL, self.name, source_score=50)
