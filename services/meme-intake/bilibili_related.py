"""Bounded public related-video leads; callers must refetch and review originals.

No database writes, credentials, login, model calls, or publishing. Metadata is
an untrusted discovery hint, never evidence that a story passed review.
"""
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import bilibili_source
import community

MAX_SEEDS = 2
MAX_SECONDS = 60


def _fetch(url, timeout):
    # A child deadline also bounds slow-drip HTTP bodies, unlike a socket idle
    # timeout alone. It inherits no intake credential/configuration variables.
    env = {k: v for k, v in os.environ.items() if not k.startswith('MEME_') and k != 'GH_TOKEN'}
    script = 'import community,json,sys;print(json.dumps(community.get(sys.argv[1],True)))'
    result = subprocess.run([sys.executable, '-c', script, url],
        cwd=Path(__file__).resolve().parent, env=env, capture_output=True,
        timeout=timeout, check=True)
    return json.loads(result.stdout)


def character_for(title, owner):
    """Prefer one explicit account identity; never guess among multiple roles."""
    if not isinstance(title, str) or not isinstance(owner, str):
        return None
    roles = [c for c in community.ALIASES if community.matches(c, owner)]
    if len(roles) == 1:
        return roles[0]
    if len(roles) > 1:
        return None
    roles = [c for c in community.ALIASES if community.matches(c, title)]
    return roles[0] if len(roles) == 1 else None


def discover(seeds, runtime, now=None):
    """Return pending seed dictionaries (character/title/url) from <=2 videos.

    ``seeds`` is a list of dictionaries with exact public Bilibili ``url``.
    Successful parents cool down 4h, failures 1h. State is private to this
    adapter; no existing candidate/approval state is altered.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    deadline = time.monotonic() + MAX_SECONDS
    path = Path(runtime) / 'bilibili-related.json'
    state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'seeds': {}}
    records = state.setdefault('seeds', {})
    due = {}
    for seed in seeds:
        ident = bilibili_source.video_id(seed.get('url', '')) if isinstance(seed, dict) else None
        if not ident:
            continue
        old = records.get(ident, {})
        interval = 3600 if old.get('status') == 'error' else 14400
        if not old or now.timestamp() - old.get('checked_epoch', 0) >= interval:
            due[ident] = old.get('checked_epoch', 0)
    found = {}
    for ident in sorted(due, key=lambda key: (due[key], key))[:MAX_SEEDS]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        receipt = {'checked_epoch': now.timestamp(), 'status': 'error', 'leads': 0}
        try:
            response = _fetch('https://api.bilibili.com/x/web-interface/archive/related?bvid=' + ident,
                              timeout=min(20, remaining))
            if not isinstance(response, dict) or response.get('code') != 0 or not isinstance(response.get('data'), list):
                raise ValueError('invalid public API response')
            for row in response['data'][:80]:
                if time.monotonic() >= deadline:
                    break
                if not isinstance(row, dict):
                    continue
                bv = row.get('bvid')
                if not isinstance(bv, str):
                    continue
                url = 'https://www.bilibili.com/video/' + bv + '/'
                if bilibili_source.video_id(url) != bv:
                    continue
                owner = row.get('owner') or {}
                role = character_for(row.get('title'), owner.get('name') if isinstance(owner, dict) else None)
                if role and bv != ident:
                    found[url] = {'character': role, 'title': row['title'], 'url': url}
                    receipt['leads'] += 1
            receipt['status'] = 'ok'
        except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
            # Exception messages/response bodies may carry arbitrary source text.
            receipt['error_type'] = type(exc).__name__
        records[ident] = receipt
    state['checked_at'] = now.isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)
    return list(found.values())
