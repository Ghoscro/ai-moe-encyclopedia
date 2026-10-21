"""Build the public static encyclopedia. Python standard library only."""
import argparse
import datetime
import html
import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
REPO = 'https://github.com/Ghoscro/ai-moe-encyclopedia'
CHARACTERS = {'claude':'Claude','doubao':'豆包','gpt':'GPT 龙娘','codex':'Codex 娘','deepseek':'DeepSeek','kimi':'Kimi','glm':'GLM','grok':'Grok','qwen':'千问','gemini':'Gemini','mistral':'Mistral','minimax':'MiniMax','ernie':'文心','muse-spark':'Muse Spark','hy4-preview':'HY4 Preview','sudo':'Sudo','waifu':'AI 陪伴'}
KINDS = {'sourced':'有出处的趣事','community':'社区梗 · 出处待补','original':'原创日常 · 虚构二创'}
FIELDS = {'id','character','title','summary','body','tags','kind','sources','contributor','date','editor_note'}

def safe_url(value):
    if not isinstance(value, str) or len(value) > 2000 or any(ord(c) < 32 for c in value):
        raise ValueError('来源链接格式错误')
    p = urlparse(value)
    if p.scheme != 'https' or not p.hostname or p.username or p.password:
        raise ValueError('来源链接必须是公开 HTTPS 地址')
    return value

def string(row, key, minimum, maximum):
    value = row.get(key)
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise ValueError(f'{key} 长度须为 {minimum}–{maximum} 字符')
    if any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError(f'{key} 含控制字符')
    return value.strip()

def validate(row, filename=None):
    if not isinstance(row,dict) or set(row)-FIELDS:
        raise ValueError('条目含未知字段或不是对象')
    ident = string(row,'id',3,80)
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',ident):
        raise ValueError('id 仅允许小写英文、数字与连字符')
    if filename and filename != ident+'.json':
        raise ValueError('文件名须与 id 一致')
    if row.get('character') not in CHARACTERS or row.get('character')=='codex' or row.get('kind') not in KINDS:
        raise ValueError('请选择有效角色与条目类型')
    for key, low, high in [('title',2,100),('summary',2,300),('body',10,6000),('contributor',1,80)]:
        string(row,key,low,high)
    if 'editor_note' in row:
        string(row,'editor_note',0,1000)
    day = string(row,'date',10,10)
    if datetime.date.fromisoformat(day).isoformat() != day:
        raise ValueError('date 须为 YYYY-MM-DD')
    tags = row.get('tags',[])
    if not isinstance(tags,list) or len(tags)>8 or any(not isinstance(t,str) or not 1<=len(t)<=24 for t in tags):
        raise ValueError('tags 最多8项，每项1–24字符')
    sources = row.get('sources',[])
    if not isinstance(sources,list) or len(sources)>10:
        raise ValueError('sources 最多10项')
    for s in sources:
        if not isinstance(s,dict) or set(s)!={'title','url'}:
            raise ValueError('每个来源需要 title 与 url')
        string(s,'title',1,160)
        safe_url(s['url'])
    if row['kind']=='sourced' and not sources:
        raise ValueError('有出处的趣事须提供至少一个原始来源')
    return row

def baseline(root=ROOT):
    home=(root/'site/meme/index.html').read_text(encoding='utf-8')
    match=re.search(r'(<script\b[^>]*id=["\']search-data["\'][^>]*>)(.*?)(</script>)',home,re.S)
    if not match:
        raise ValueError('首页搜索索引缺失')
    data=json.loads(match.group(2))
    expected=json.loads((root/'content/baseline.json').read_text(encoding='utf-8')) if (root/'content/baseline.json').exists() else None
    if expected and sum(e.get('type')=='meme' for e in data)!=expected['indexed_memes']:
        raise ValueError('历史条目索引数量与基线不符；修订后请同步 content/baseline.json')
    return data

def entries(root=ROOT):
    rows=[]
    seen=set()
    base=baseline(root)
    legacy_ids={urlparse(e.get('href','')).fragment for e in base if e.get('type')=='meme'}
    legacy_titles={(e.get('owner'),e.get('title')) for e in base if e.get('type')=='meme'}
    for f in sorted((root/'content/memes').glob('*.json')):
        if f.is_symlink() or f.stat().st_size>40000:
            raise ValueError('条目文件过大或为符号链接')
        row=validate(json.loads(f.read_text(encoding='utf-8')),f.name)
        if row['id'] in seen or row['id'] in legacy_ids:
            raise ValueError('条目 id 重复: '+row['id'])
        key=(CHARACTERS[row['character']],row['title'])
        if key in legacy_titles:
            raise ValueError('同角色同标题条目已存在: '+row['title'])
        legacy_titles.add(key)
        seen.add(row['id'])
        rows.append(row)
    return rows

def card(row):
    e=html.escape
    paragraphs=''.join('<p>'+e(p).replace('\n','<br>')+'</p>' for p in row['body'].split('\n\n') if p.strip())
    sources=''.join('<a href="'+e(s['url'],quote=True)+'" rel="noopener noreferrer" target="_blank">'+e(s['title'])+' ↗</a> ' for s in row.get('sources',[]))
    tags=' '.join('#'+e(t) for t in row.get('tags',[]))
    note='<p class="community-note">'+e(row.get('editor_note',''))+'</p>' if row.get('editor_note') else ''
    return f'<article class="community-entry" id="{e(row["id"])}"><small>{KINDS[row["kind"]]} · {e(row["date"])}</small><h3>{e(row["title"])}</h3><p class="community-summary">{e(row["summary"])}</p>{paragraphs}{note}<p>{tags}</p><div class="community-credit">投稿：{e(row["contributor"])} · {sources}</div></article>'

def strip():
    return '<aside class="community-strip" aria-label="社区投稿"><span>有新梗？来补一页。</span><a href="/meme/contribute/">投稿新梗 ↗</a><a href="'+REPO+'" target="_blank" rel="noopener noreferrer">GitHub 仓库</a></aside>'

def build(root=ROOT, output=None):
    allowed={'.html','.css','.js','.json','.svg','.webp','.png','.jpg','.jpeg','.woff2','.txt','.ico'}
    for f in (root/'site').rglob('*'):
        if f.is_symlink() or (f.is_file() and (f.suffix.lower() not in allowed or f.stat().st_size>10_000_000)):
            raise ValueError('非法静态文件: '+str(f.relative_to(root)))
    output=Path(output or root/'dist').resolve()
    if output==root.resolve() or root.resolve() not in output.parents:
        raise ValueError('输出目录必须是项目子目录')
    if output.exists():
        if output.is_symlink():
            raise ValueError('输出目录不可为符号链接')
        shutil.rmtree(output)
    shutil.copytree(root/'site',output)
    rows=entries(root)
    base=baseline(root)
    for slug in ('meme',*CHARACTERS):
        p=output/slug/'index.html'
        doc=p.read_text(encoding='utf-8')
        doc=doc.replace('</head>','<link rel="stylesheet" href="/meme/community.css"></head>',1)
        doc=re.sub(r'(<main\b[^>]*>)',lambda m:m.group(1)+strip(),doc,count=1)
        own=[r for r in rows if r['character']==slug]
        if own:
            section='<section class="community-submissions" aria-labelledby="community-title"><h2 id="community-title">社区补档</h2>'+''.join(card(r) for r in own)+'</section>'
            doc=re.sub(r'(<main\b[^>]*>.*?</aside>)',lambda m:m.group(1)+section,doc,count=1,flags=re.S)
        if slug=='meme':
            additions=[{'type':'meme','owner':CHARACTERS[r['character']],'title':r['title'],'text':r['summary']+' '+r['body'],'href':'/'+r['character']+'/#'+r['id'],'keywords':' '.join(r.get('tags',[]))} for r in rows]
            # Escape < so user text can never terminate the application/json script.
            data=json.dumps(base+additions,ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
            doc=re.sub(r'(<script\b[^>]*id=["\']search-data["\'][^>]*>).*?(</script>)',lambda m:m.group(1)+data+m.group(2),doc,count=1,flags=re.S)
        p.write_text(doc,encoding='utf-8',newline='\n')
    total=sum(e.get('type')=='meme' for e in base)+len(rows)
    (output/'meme/community-manifest.json').write_text(json.dumps({'legacy_entries':total-len(rows),'community_entries':len(rows),'total_entries':total},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'Built {total} entries ({len(rows)} community submissions) into {output.name}')
    return output

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    if args.check:
        print(f'Content valid: {len(entries())} community submissions')
    else:
        build()
