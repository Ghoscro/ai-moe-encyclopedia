"""Bounded original-page retrieval. Source text is data, never instructions."""
import hashlib
import re
from html.parser import HTMLParser
import urllib.parse
import urllib.request

VERSION = 'original-page-v2'
HOSTS = {'simonwillison.net', 'www.anthropic.com', 'andonlabs.com', 'www.404media.co', 'www.cohere.com', 'cohere.com', 'openai.com'}

def allowed(url):
    try:
        p = urllib.parse.urlsplit(url)
        return (p.scheme == 'https' and p.hostname in HOSTS and
                p.port in (None, 443) and not p.username and not p.password)
    except ValueError:
        return False

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'):
            self.skip += 1
        if tag == 'img' and not self.skip:
            alt = dict(attrs).get('alt', '')
            if alt:
                self.parts.append('[Image description: ' + alt + ']')
    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript') and self.skip:
            self.skip -= 1
    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data.strip())

def fetch(url):
    if not allowed(url):
        return None
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'AIHER-source-verification/1.0'})
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=20) as response:
            if response.status != 200 or 'text/html' not in response.headers.get('Content-Type', ''):
                return None
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            return None
        parser = Text()
        parser.feed(raw.decode('utf-8'))
        text = '\n'.join(parser.parts)[:6000]
        if len(text) < 80:
            return None
        return {'version': VERSION, 'url': url, 'text': text,
                'sha256': hashlib.sha256(text.encode()).hexdigest()}
    except (OSError, ValueError, UnicodeError):
        return None


class PlainText(HTMLParser):
    """Keep inline text contiguous; discard executable markup, preserve image descriptions."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'):
            self.skip += 1
        if not self.skip and tag in ('p', 'div', 'br', 'li', 'blockquote'):
            self.parts.append(' ')
        if not self.skip and tag == 'img':
            self.parts.append(' ' + dict(attrs).get('alt', '') + ' ')
    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript') and self.skip:
            self.skip -= 1
        if not self.skip and tag in ('p', 'div', 'li', 'blockquote'):
            self.parts.append(' ')
    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def plain_text(value):
    parser = PlainText()
    parser.feed(str(value))
    return re.sub(r'\s+', ' ', ''.join(parser.parts)).strip()


def selection_input(item):
    # Same canonical text is used for model input AND exact evidence checks.
    return dict(item, title=plain_text(item.get('title', ''))[:6000],
                summary=plain_text(item.get('summary', ''))[:6000])


class SourceLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            url = dict(attrs).get('href', '')
            if allowed(url) and url not in self.urls:
                self.urls.append(url)


def resolve(item, original_claim):
    """Fetch bounded evidence, never infer authorship from a domain alone.

    A secondary feed may explicitly link its original author. Follow only
    allowlisted HTTPS links in that feed summary, not arbitrary search/redirects.
    The fetched text must still pass independent primary-source selection.
    """
    if original_claim:
        urls = [item.get('url', '')]
    else:
        parser = SourceLinks()
        parser.feed(str(item.get('summary', '')))
        urls = [u for u in parser.urls if u != item.get('url')][:3]
    for url in urls:
        evidence = fetch(url)
        if evidence:
            evidence['discovery_url'] = item.get('url', '')
            return evidence
    return None
