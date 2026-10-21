"""Check owned local page, asset and anchor links; external sites are not fetched."""
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote,urljoin,urlparse

ROOT=Path(__file__).resolve().parents[1]/'dist'
if not (ROOT/'meme/index.html').is_file():
    raise SystemExit('Build output missing; run python tools/build.py first')
OWNED={'meme','claude','doubao','gpt','codex','deepseek','kimi','glm','grok','qwen','gemini','mistral','minimax','ernie','muse-spark','hy4-preview','sudo','waifu'}

class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.refs=[]; self.ids=set()
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if a.get('id'): self.ids.add(a['id'])
        for key in ('href','src'):
            if a.get(key): self.refs.append(a[key])

pages={}
for f in ROOT.rglob('*.html'):
    p=Links(); p.feed(f.read_text(encoding='utf-8')); pages[f.resolve()]=p
errors=[]; checked=0
for f,p in pages.items():
    page='/'+f.relative_to(ROOT).as_posix()
    for ref in p.refs:
        u=urlparse(urljoin('https://mickerbook.com'+page,ref))
        if u.netloc!='mickerbook.com' or u.path.strip('/').split('/')[0] not in OWNED: continue
        target=(ROOT/unquote(u.path.lstrip('/')))
        if u.path.endswith('/'): target=target/'index.html'
        checked+=1
        if not target.is_file(): errors.append(f'{page}: missing {ref}')
        elif u.fragment and target.suffix=='.html' and target.resolve() in pages and unquote(u.fragment) not in pages[target.resolve()].ids:
            # Existing pages contain a few action href="#" links, excluded by urlparse above.
            errors.append(f'{page}: missing anchor {ref}')
for f in ROOT.rglob('*.css'):
    for ref in re.findall(r'''url\(\s*['"]?([^'"\)]+)''',f.read_text(encoding='utf-8')):
        if ref.startswith(('data:','#','%23')): continue
        u=urlparse(urljoin('https://mickerbook.com/'+f.relative_to(ROOT).as_posix(),ref))
        if u.netloc=='mickerbook.com' and not (ROOT/u.path.lstrip('/')).is_file(): errors.append(f'{f.name}: missing {ref}')
if errors: raise SystemExit('\n'.join(errors))
print(f'Checked {checked} owned page/asset/anchor links across {len(pages)} pages')
