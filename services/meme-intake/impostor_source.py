"""Public game transcripts, explicitly fictional identities; bounded exact paths."""
import datetime as dt
import hashlib
from html.parser import HTMLParser
import re

_URL = re.compile(r"https://impostorbench\.ai/en/games/(main-([0-9]{6})-r[0-9]{2}-g[0-9]{4})/")

def supported(url):
    return isinstance(url, str) and _URL.fullmatch(url) is not None

class Transcript(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.section = None
        self.skip = 0
        self.parts = {}
        self.all_text = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style', 'noscript'): self.skip += 1
        if tag == 'section':
            self.section = attrs.get('id') or ('players' if 'castw' in attrs.get('class', '').split() else None)
        if tag in ('li', 'p', 'div', 'h2') and self.section:
            self.parts.setdefault(self.section, []).append(' ')
    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript') and self.skip: self.skip -= 1
        if tag == 'section': self.section = None
    def handle_data(self, data):
        if self.skip: return
        self.all_text.append(data)
        if self.section: self.parts.setdefault(self.section, []).append(data)

def evidence(url):
    import community, origin
    if not supported(url): return None
    try:
        parser = Transcript(); parser.feed(community.get(url))
        all_text = ' '.join(parser.all_text)
        match = re.search(r'(20[0-9]{2}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}) UTC', all_text)
        if not match or _URL.fullmatch(url).group(1) not in all_text: return None
        stamp = dt.datetime.strptime(match[1], '%Y-%m-%d %H:%M').replace(tzinfo=dt.timezone.utc)
        if stamp > dt.datetime.now(dt.timezone.utc) or stamp.strftime('%y%m%d') != _URL.fullmatch(url).group(2): return None
        parts = {k: re.sub(r'\s+', ' ', ''.join(v)).strip() for k,v in parser.parts.items()}
        if not all(parts.get(k) for k in ('players','ph3','ph4','result')): return None
        text = '\n'.join(['Original game: '+_URL.fullmatch(url).group(1), 'Original UTC time: '+stamp.isoformat(),
             'Scope: public AI roleplay game transcript. All players are models; human biographies and anecdotes are roleplay, not verified real human experiences. Model names are attributed by the game publisher. Excerpts: cast, phase 3, final statements, result. No private reasoning reproduced.',
             *[parts[k] for k in ('players','ph3','ph4','result')]])
        if len(text)>12000: return None
        return dict(version=origin.VERSION, url=url, text=text, published_at=stamp.isoformat(),
                    author='ImpostorBench', evidence_scope='public_ai_roleplay_game_excerpt',
                    sha256=hashlib.sha256(text.encode()).hexdigest())
    except (OSError, ValueError, TypeError, AttributeError): return None
