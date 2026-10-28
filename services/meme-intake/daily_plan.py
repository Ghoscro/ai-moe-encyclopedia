"""Daily per-character coverage from verified receipts, plus bounded archive discovery."""
import datetime as dt
import json
import re
import urllib.request
from pathlib import Path
import origin

TZ = dt.timezone(dt.timedelta(hours=8))
ROSTER = {'claude':'claude','doubao':'doubao','gpt':'openai','deepseek':'deepseek',
          'kimi':'kimi','glm':'glm','grok':'grok','qwen':'qwen','gemini':'gemini',
          'mistral':'mistral','minimax':'minimax','ernie':'ernie',
          'muse-spark':'muse','hy4-preview':'hunyuan'}

def today():
    return dt.datetime.now(TZ).date().isoformat()

def live_counts(runtime, date=None):
    date = date or today()
    receipts=[]
    for p in sorted(Path(runtime).glob('release-*.json'))+[Path(runtime)/'pipeline.json']:
        if p.exists():
            receipts.append(json.loads(p.read_text(encoding='utf-8')))
    withdrawn=set()
    for r in receipts:
        withdrawn.update(r.get('withdrawn_ids', []))
        if r.get('withdrawn_at'):
            withdrawn.update(e['id'] for e in r.get('entries', []))
    seen=set();counts={c:0 for c in ROSTER}
    for r in receipts:
        stamp=r.get('verified_at')
        if r.get('stage')!='live' or not stamp:
            continue
        if dt.datetime.fromisoformat(stamp.replace('Z','+00:00')).astimezone(TZ).date().isoformat()!=date:
            continue
        for e in r.get('entries', []):
            if e['id'] not in seen|withdrawn and e['character'] in counts:
                counts[e['character']]+=1;seen.add(e['id'])
    return counts

def write_coverage(runtime, rows):
    counts=live_counts(runtime)
    data={'date':today(),'timezone':'Asia/Shanghai','target_per_character':1,'characters':{}}
    for c in ROSTER:
        pending=[r for r in rows if r.get('character')==c and r.get('status')=='pending']
        data['characters'][c]={'live':counts[c],'target':1,'pending_candidates':len(pending),
            'status':'complete' if counts[c]>=1 else ('awaiting_review' if pending else 'source_gap')}
    target=Path(runtime)/'daily-coverage.json';tmp=target.with_suffix('.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(target)
    return data

def discover_archives(store, runtime, limit_per_character=2):
    """Author-written archives are leads, never automatically approved content.

    At most two previously unseen pages per missing character per day. Empty tag
    pages are recorded as source gaps; tags do not prove authorship or humor.
    """
    import db
    report_path=Path(runtime)/'archive-discovery.json'
    old = None
    if report_path.exists():
        old=json.loads(report_path.read_text(encoding='utf-8'))
        if old.get('date')!=today(): old = None
    report=old or {'date':today(),'characters':{}}
    attempted=0
    counts=live_counts(runtime)
    for character,tag in ROSTER.items():
        if character in report['characters']: continue
        if attempted>=3: break
        if counts[character]>=1:
            report['characters'][character]={'status':'daily_target_met','added':0};continue
        attempted+=1
        url='https://simonwillison.net/tags/'+tag+'/'
        added=0;examined=0
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'AIHER-source-verification/1.0'})
            with urllib.request.build_opener(origin.NoRedirect()).open(req,timeout=15) as response:
                raw=response.read(2000001)
            if len(raw)>2000000:raise ValueError('oversized archive')
            html=raw.decode('utf-8')
            links=re.findall(r'<a[^>]*href="(/\d{4}/[A-Za-z]+/\d{1,2}/[^"/]+/)"[^>]*rel="bookmark"[^>]*>(.*?)</a>',html,re.S)
            for path,title in links:
                original='https://simonwillison.net'+path
                if origin.plain_text(title)=='#': title=path.strip('/').split('/')[-1].replace('-', ' ')
                if store.has_url(original): continue
                if examined>=limit_per_character: break
                examined+=1
                evidence=origin.fetch(original)
                if not evidence: continue
                date=dt.datetime.strptime('/'.join(path.strip('/').split('/')[:3]),'%Y/%b/%d').date().isoformat()
                source='author-archive-'+character
                card={'suggested_character':character,'source_title':origin.plain_text(title),
                      'source_url':original,'note':'作者实验档案，待原文选题与四格审核；非当日新闻。',
                      'origin':dict(evidence,discovery_url=original)}
                if store.add_candidate({'fp':db.fingerprint(source,original),'source':source,
                    'url':original,'title':origin.plain_text(title),'summary':evidence['text'],
                    'character':character,'published_at':date,'meme_score':30,
                    'draft':json.dumps(card,ensure_ascii=False)}):added+=1
            report['characters'][character]={'status':'candidates_added' if added else 'no_new_source',
                                            'added':added,'examined':examined,'archive_url':url}
        except (OSError,ValueError) as exc:
            report['characters'][character]={'status':'source_unavailable','added':added,'error':type(exc).__name__}
    tmp=report_path.with_suffix('.tmp');tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(report_path)
    return report
