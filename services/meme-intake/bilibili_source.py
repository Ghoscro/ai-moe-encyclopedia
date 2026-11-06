"""Read-only Bilibili original video metadata. No login, comments, or publishing."""
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
import urllib.parse as up

def video_id(url):
    try:
        p=up.urlsplit(url)
        if p.scheme!='https' or p.hostname!='www.bilibili.com' or p.port not in (None,443) or p.username or p.password or p.query or p.fragment:
            return None
        m=re.fullmatch(r'/video/(BV[0-9A-Za-z]{10})/?',p.path)
        return m.group(1) if m else None
    except ValueError:return None

def evidence(url):
    import community,origin
    ident=video_id(url)
    if not ident:return None
    try:
        raw=community.get('https://api.bilibili.com/x/web-interface/view?bvid='+ident,True)
        data=raw.get('data') or {}
        if raw.get('code')!=0 or data.get('bvid')!=ident:return None
        title=data.get('title');owner=(data.get('owner') or {}).get('name')
        if not isinstance(title,str) or len(title)<10 or not isinstance(owner,str) or not owner:return None
        stamp=dt.datetime.fromtimestamp(data['pubdate'],dt.timezone.utc)
        if stamp>dt.datetime.now(dt.timezone.utc):return None
        text=origin.plain_text('原标题：'+title+'\n投稿账号：'+owner+'\n作者填写的视频简介：'+(data.get('desc') or '【原投稿简介为空】')+'\n证据范围：仅原投稿标题和简介，未读取视频画面、音频或评论。')
        return {'version':origin.VERSION,'url':url,'text':text,'published_at':stamp.isoformat(),'author':owner,
                'evidence_scope':'original_video_metadata_only','sha256':hashlib.sha256(text.encode()).hexdigest()}
    except (OSError,ValueError,TypeError,KeyError):return None

def discover(store,runtime,counts,target=10):
    import db, bilibili_related
    path=Path(runtime)/'bilibili-discovery.json'
    old=json.loads(path.read_text()) if path.exists() else {}
    now=dt.datetime.now(dt.timezone.utc)
    results={}
    seeds=json.loads(Path(__file__).with_name('bilibili-seeds.json').read_text(encoding='utf-8'))
    pending_path=Path(runtime)/'bilibili-leads.json'
    leads=json.loads(pending_path.read_text(encoding='utf-8')) if pending_path.exists() else {}
    for seed in seeds + bilibili_related.discover(seeds,runtime):
        if video_id(seed.get('url','')):
            leads[seed['url']]=seed
    temporary=pending_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(leads,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(pending_path)
    attempted=0
    for seed in sorted(leads.values(),key=lambda x:(counts.get(x['character'],0),old.get(x['url'],{}).get('checked_epoch',0))):
        url=seed['url'];role=seed['character']
        if counts.get(role,0)>=target or store.has_url(url):continue
        if now.timestamp()-old.get(url,{}).get('checked_epoch',0)<3600:continue
        if attempted>=8:break
        attempted+=1
        e=evidence(url)
        record={'character':role,'checked_epoch':now.timestamp(),'status':'unavailable','added':False}
        if e:
            source='bilibili-community-'+role
            record['added']=store.add_candidate({'fp':db.fingerprint(source,url),'source':source,'url':url,
                'title':seed['title'],'summary':e['text'],'published_at':e['published_at'],'character':role,'meme_score':40,
                'draft':json.dumps({'note':'用户选题方向线索；原投稿元数据，待独立选题与审稿。'},ensure_ascii=False)})
            record['status']='metadata_fetched'
        old[url]=record;results[url]=record
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(old,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
    return results
