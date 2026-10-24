"""Bounded original-page retrieval. Source text is data, never instructions."""
import hashlib
from html.parser import HTMLParser
import urllib.parse
import urllib.request

VERSION = 'original-page-v1'
HOSTS = {'simonwillison.net', 'www.anthropic.com', 'andonlabs.com'}

def allowed(url):
    p = urllib.parse.urlsplit(url)
    return (p.scheme == 'https' and p.hostname in HOSTS and
            p.port in (None, 443) and not p.username and not p.password)

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
