#!/usr/bin/env python3
"""Meme intake CLI - the encyclopedia's hot-meme backend.

Pipeline (mirrors the family intelligence station, stdlib-only edition):

    fetch -> prefilter -> score -> dedupe -> review queue -> human gate -> publish

Two gates write into content/memes/, and both are validated by
tools/build.py itself: `approve` is the human gate; `auto` is the
machine gate. The machine gate only ever promotes *written* LLM drafts
that clear every check - rule-mode lead cards stay human-only, because
the system itself labels them "awaiting verification".

Usage:
    python intake.py run                     # one incremental collection round
    python intake.py list [--status S] [--min-score N]
    python intake.py show <fp-prefix>
    python intake.py approve <fp-prefix> [--id ID] [--force]
    python intake.py reject <fp-prefix> [--reason R]
    python intake.py backfill [--status pending] [--limit N] [--dry-run]
    python intake.py auto [--threshold N] [--limit N] [--dry-run]
    python intake.py stats
    python intake.py explain <fp-prefix>
"""
import argparse
import datetime
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                      # projects/ai-moe-encyclopedia
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / 'tools'))     # build.py is the single schema source

import db as store_mod          # noqa: E402
import radar                    # noqa: E402
import llm as llm_mod           # noqa: E402
from sources import (AIHotSource, EditorialSource,  # noqa: E402
                     BaiduHotSource, DouyinHotSource,
                     GcoresRSSSource, SspaiRSSSource)
import build as build_mod       # noqa: E402  (validate() is canonical)
from locking import exclusive

MEMES_DIR = ROOT / 'content' / 'memes'
DEFAULT_THRESHOLD = radar.DEFAULT_THRESHOLD
AUTO_THRESHOLD = 70.0   # the machine gate sits stricter than the queue's 30

# Windows reserved device names: an entry file named con.json / nul.json is
# unbindable on the owner's machine (and a support-ticket generator).
WINDOWS_RESERVED = ({'con', 'nul', 'aux', 'prn'}
                    | {f'com{i}' for i in range(1, 10)}
                    | {f'lpt{i}' for i in range(1, 10)})


def _load_dotenv(path=None):
    """Load services/meme-intake/.env into os.environ (stdlib mini-dotenv).

    The scheduled task runs `python intake.py run` with no shell profile, so
    MEME_LLM_* lives in a file next to the CLI. Rules: real environment
    always wins (setdefault semantics), missing file is a no-op, malformed
    lines are skipped, and values may be single/double quoted. The file is
    .gitignored - it holds the LLM key.
    """
    env_path = Path(path or os.environ.get('MEME_ENV_FILE') or HERE / '.env')
    try:
        raw = env_path.read_text(encoding='utf-8-sig')
    except OSError:
        return 0
    loaded = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


def _finite_float(value):
    """argparse type: reject nan/inf before they poison score comparisons."""
    number = float(value)
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError(f'不是有限数字: {value!r}')
    return number


def _load_card(row):
    """Stored draft JSON, or a fresh lead card when the draft is corrupt."""
    if row.get('draft'):
        try:
            card = json.loads(row['draft'])
        except (ValueError, TypeError):
            card = None
        if isinstance(card, dict):
            return card
    return radar.make_lead_card(row)


def _published_conflicts(entry, meme_id):
    """Pre-dedup against everything already public (lightweight).

    build.py's entries() enforces id and (character, title) uniqueness at
    build time; approve checks the same two sets early, so a conflicting
    candidate is refused before anything is written. Reads the legacy
    search-data index on the site plus the on-disk entries in MEMES_DIR
    (the module global, so tests can point it at a temp dir). Returns a
    human-readable conflict, or '' when clear. Never raises.
    """
    legacy_ids, legacy_pairs = set(), set()
    try:
        home = (ROOT / 'site' / 'meme' / 'index.html').read_text(encoding='utf-8')
        match = re.search(r'(<script\b[^>]*id=["\']search-data["\'][^>]*>)(.*?)(</script>)',
                          home, re.S)
        if match:
            data = json.loads(match.group(2))
            memes = [e for e in data if isinstance(e, dict) and e.get('type') == 'meme']
            legacy_ids = {urlparse(e.get('href', '')).fragment for e in memes}
            legacy_pairs = {(e.get('owner'), e.get('title')) for e in memes}
    except Exception:
        pass  # unreadable index: the on-disk check below still applies
    existing_ids, existing_pairs = set(), set()
    try:
        for f in MEMES_DIR.glob('*.json'):
            if f.stem == meme_id:
                continue  # the file --force is about to rewrite is not a duplicate
            row = json.loads(f.read_text(encoding='utf-8'))
            existing_ids.add(row.get('id'))
            existing_pairs.add((build_mod.CHARACTERS.get(row.get('character'), ''),
                                row.get('title')))
    except Exception:
        pass  # unreadable entry file: let build.py's full pass catch it
    pair = (build_mod.CHARACTERS.get(entry.get('character'), ''), entry.get('title'))
    if meme_id in legacy_ids or meme_id in existing_ids:
        return f'id {meme_id!r} 与已发布条目冲突'
    if pair in legacy_pairs or pair in existing_pairs:
        return f'同角色同标题条目已存在: {entry.get("title")!r}'
    return ''


def _resolve(rows, prefix):
    """Resolve a short fingerprint prefix to exactly one candidate row."""
    hits = [r for r in rows if r['fp'].startswith(prefix)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise SystemExit(f'no candidate matches {prefix!r}')
    raise SystemExit(f'prefix {prefix!r} is ambiguous: ' +
                     ', '.join(r['fp'][:10] for r in hits))


def _entry_from_card(row, card, meme_id):
    """Turn a lead card (or LLM draft) into a schema-valid entry dict."""
    today = datetime.date.today().isoformat()
    entry = {
        'id': meme_id,
        'character': card.get('suggested_character') or 'waifu',
        'title': row['title'][:100],
        'summary': (row.get('summary') or card.get('note', ''))[:300],
        'body': '\n\n'.join(p for p in [
            (row.get('summary') or '').strip(),
            card.get('note', ''),
            f'原始线索：{row["url"]}',
        ] if p.strip())[:6000],
        'tags': [t[:24] for t in card.get('signals', [])][:8] or ['AI 热点'],
        'kind': 'community',
        'sources': [{'title': row['title'][:160], 'url': row['url']}],
        'contributor': '梗雷达自动收录',
        'date': today,
        'editor_note': (card.get('why', '') + ' 本条目由梗雷达自动归集，'
                        '出处与内容待人工核实后转正。')[:1000],
    }
    if entry['character'] == 'codex':
        entry['character'] = 'gpt'  # build.py folds Codex into the GPT family
    return entry


def cmd_run(args):
    store = store_mod.Store()
    run_id = uuid.uuid4().hex
    store.run_start(run_id)
    sources = [AIHotSource(), EditorialSource(), BaiduHotSource(), DouyinHotSource(),
               GcoresRSSSource(), SspaiRSSSource()]
    use_llm = llm_mod.is_configured()
    model = llm_mod.MemeLLM() if use_llm else None
    fetched = new = queued = llm_calls = 0
    receipts = []
    try:
        for src in sources:
            try:
                if isinstance(src, EditorialSource):
                    items, receipt = src.fetch()
                else:
                    items, receipt = src.fetch(mode=args.mode, window=args.window)
            except Exception as exc:
                # one dead source must not take down the whole round
                receipts.append({'source': getattr(src, 'name', '?'), 'items': 0,
                                 'degraded': 1, 'error': str(exc)})
                continue
            receipts.append(receipt)
            fetched += len(items)
            for item in items:
                if not item.get('url') or not item.get('title'):
                    continue
                fp = store_mod.fingerprint(item['source'], item['url'])
                if store.get(fp) or store.is_published(fp) or store.has_url(item['url']):
                    continue
                scored = radar.score_item(item)
                if scored['meme_score'] < args.threshold:
                    continue
                card = radar.make_lead_card(item, scored)
                draft = card
                if model and llm_calls < getattr(args, 'llm_limit', 3):
                    llm_calls += 1
                    try:
                        llm_draft = model.draft(item)
                    except Exception:
                        llm_draft = None
                    if not llm_mod.valid_draft(llm_draft):
                        llm_draft = None
                    if llm_draft:
                        draft = {'suggested_character': llm_draft.get('character', ''),
                                 'signals': card['signals'], 'why': card['why'],
                                 'suggested_kind': llm_draft.get('kind', 'community'),
                                 'source_title': row_title(llm_draft),
                                 'source_url': item['url'],
                                 'note': 'LLM 草稿，待人工核实。',
                                 'llm_entry': llm_draft}
                if store.add_candidate({
                        'fp': fp, 'source': item['source'], 'url': item['url'],
                        'title': item['title'], 'summary': item.get('summary', ''),
                        'category': item.get('category', ''),
                        'source_score': item.get('source_score', 0),
                        'published_at': item.get('published_at', ''),
                        'discovered_at': item.get('discovered_at', ''),
                        'character': scored['character'],
                        'signals': scored['signals'],
                        'meme_score': scored['meme_score'],
                        'draft': json.dumps(draft, ensure_ascii=False)}):
                    new += 1
                    queued += 1
        result = {'run_id': run_id, 'llm': use_llm, 'threshold': args.threshold,
                  'receipts': receipts, 'fetched': fetched, 'new': new,
                  'queued': queued}
        store.run_finish(run_id, fetched, new, result)
    except Exception as exc:
        # an aborted run must leave a receipt behind, not a dangling row
        try:
            store.run_finish(run_id, fetched, new,
                             {'result': 'ABORTED', 'error': str(exc)})
        except sqlite3.Error:
            pass  # even the abort receipt could not be written
        raise
    finally:
        store.close()
    print(json.dumps({'fetched': fetched, 'new': new, 'queued': queued,
                      'llm': use_llm, 'run_id': run_id}, ensure_ascii=False))


def row_title(draft):
    return (draft or {}).get('title', '')


def cmd_list(args):
    store = store_mod.Store()
    rows = store.list(status=args.status, min_score=args.min_score, limit=args.limit)
    store.close()
    if not rows:
        print('(empty)')
        return
    for r in rows:
        print(f"{r['fp'][:10]}  {r['meme_score']:6.2f}  {r['status']:9s} "
              f"{r['character'] or '-':11s} {r['title'][:52]}")
    print(f"-- {len(rows)} rows")


def cmd_show(args):
    store = store_mod.Store()
    row = _resolve(store.list(limit=10000), args.prefix)
    store.close()
    print(f"fp          {row['fp']}")
    print(f"score       {row['meme_score']}  ({radar.explain(row, radar.score_item(row))})")
    print(f"status      {row['status']}")
    print(f"character   {row['character'] or '-'}")
    print(f"title       {row['title']}")
    print(f"url         {row['url']}")
    print(f"summary     {row['summary'][:200]}")
    if row['draft']:
        print('draft:')
        print(json.dumps(_load_card(row), ensure_ascii=False, indent=2))


def _merge_llm(entry, row, card):
    """Fold an LLM draft into the rule-mode entry (the approve merge rules).

    An llm_entry with an unknown character is ignored entirely: merging it
    would silently replace a lead card with a half-valid draft.
    """
    llm_entry = (card or {}).get('llm_entry')
    if llm_mod.valid_draft(llm_entry):
        merged = dict(entry)
        for k in ('character', 'title', 'summary', 'body', 'tags', 'kind'):
            if llm_entry.get(k):
                merged[k] = llm_entry[k]
        if merged['character'] == 'codex':
            merged['character'] = 'gpt'  # build.py folds Codex into the GPT family
        # the LLM body replaces the rule-mode one, which carried the link;
        # re-attach the original lead so provenance survives the merge
        if row['url'] not in merged.get('body', ''):
            suffix = f'\n\n原始线索：{row["url"]}'
            merged['body'] = merged['body'][:6000-len(suffix)] + suffix
        merged['editor_note'] = 'AI 辅助创作；拟人对白为虚构二创。来源链接为创作线索，非对白实录。'
        merged['id'] = entry['id']
        entry = merged
    return entry


def _prewrite_checks(entry, meme_id):
    """Everything that must hold before a single byte is written.

    Corpus dedup (id + character/title, including the legacy site index)
    and the canonical schema gate. Returns '' when clear, else the refusal
    reason. Never writes, never raises.
    """
    conflict = _published_conflicts(entry, meme_id)
    if conflict:
        return f'与已发布条目冲突: {conflict}'
    try:
        build_mod.validate(entry, meme_id + '.json')
    except ValueError as exc:
        return f'条目未通过 build.py 校验: {exc}'
    return ''


def _write_entry(store, row, entry, meme_id, force=False):
    """Write the entry file, then record it in the queue.

    A failed record after a successful write is a hard stop: the file
    exists but the queue does not know, and ploughing on would hide that
    inconsistency instead of surfacing it.
    """
    MEMES_DIR.mkdir(parents=True, exist_ok=True)
    target = MEMES_DIR / (meme_id + '.json')
    payload = json.dumps(entry, ensure_ascii=False, indent=2) + '\n'
    if target.exists() and not force and target.read_text(encoding='utf-8') != payload:
        raise SystemExit('现有文件与候选不一致，停止覆盖')
    temporary = target.with_suffix('.json.tmp')
    temporary.write_text(payload, encoding='utf-8', newline='\n')
    os.replace(temporary, target)
    try:
        store.mark_published(row['fp'], meme_id)
    except sqlite3.OperationalError:
        raise SystemExit('条目已落盘但数据库状态未更新，修复锁后用 --force 重新 approve')


def cmd_approve(args):
    store = store_mod.Store()
    try:
        row = _resolve(store.list(limit=10000), args.prefix)
        if row['status'] == 'published':
            raise SystemExit('already published')
        card = _load_card(row)
        draft = card.get('llm_entry')
        if isinstance(draft, dict) and draft.get('character') in build_mod.CHARACTERS and not llm_mod.valid_draft(draft):
            raise SystemExit('LLM 草稿未通过字段校验')
        meme_id = args.id or ('aihot-' + row['fp'][:10])
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', meme_id):
            raise SystemExit('id 仅允许小写英文、数字与连字符')
        if meme_id.lower() in WINDOWS_RESERVED:
            raise SystemExit('id 与 Windows 保留设备名冲突'
                             '（con/nul/aux/prn/com1-9/lpt1-9）')
        target = MEMES_DIR / (meme_id + '.json')
        if target.exists():
            if not args.force:
                raise SystemExit(f'{target.name} 已存在（--force 覆盖）')
            # --force is destructive: keep the previous version beside it
            shutil.copy2(target, target.parent / (target.name + '.bak'))
        entry = _entry_from_card(row, card, meme_id)
        entry = _merge_llm(entry, row, card)
        reason = _prewrite_checks(entry, meme_id)
        if reason:
            raise SystemExit(reason)
        _write_entry(store, row, entry, meme_id, force=args.force)
        try:
            location = target.relative_to(ROOT)
        except ValueError:
            location = target  # MEMES_DIR patched (tests) or moved: full path
        print(f'published -> {location}')
        print('next: python tools/build.py --check && python tools/build.py')
    finally:
        store.close()


def _auto_refusal(row, card):
    """Why this candidate may not be machine-published ('' = clear).

    Rule-mode lead cards are citations the system itself labels
    "awaiting verification" - a machine must never promote them on its
    own. Only a written LLM draft (with a whitelisted character, so the
    merge actually happens) is publishable without a human.
    """
    llm_entry = (card or {}).get('llm_entry')
    if not isinstance(llm_entry, dict) or not isinstance(llm_entry.get('body'), str) or not llm_entry['body'].strip():
        return '规则模式线索卡（未梗化），仅人工可发布'
    if llm_entry.get('character') not in build_mod.CHARACTERS:
        return 'LLM 角色不在白名单，合并会退化成线索卡'
    if not llm_mod.valid_draft(llm_entry):
        return 'LLM 草稿字段格式不合法'
    return ''


def _auto_id(row):
    """Source-prefixed id, e.g. aihot-7951a42d94 / gcores-0a1b2c3d4e.

    Provenance stays visible in the public URL, and ids remain unique per
    candidate fingerprint.
    """
    src = re.sub(r'[^a-z0-9]+', '-', (row['source'] or '').lower()).strip('-')
    return f'{src or "aihot"}-{row["fp"][:10]}'


def cmd_auto(args):
    """The machine gate: publish written drafts that clear every check.

    Contract, in order: a written LLM draft (never a rule-mode lead card),
    meme_score >= --threshold, canonical schema validation, corpus dedup.
    Anything unclear is skipped with a reason; nothing is half-written.
    --dry-run evaluates the full contract without touching disk or the
    queue, including same-round duplicates that only a real write exposes.
    """
    store = store_mod.Store()
    try:
        pending = store.list(status='pending', limit=args.limit)
        rows = [r for r in pending if r['meme_score'] >= args.threshold]
        published, skipped = [], []
        planned_ids, planned_pairs = set(), set()
        for row in rows:
            card = _load_card(row)
            reason = _auto_refusal(row, card)
            meme_id = entry = None
            if not reason:
                meme_id = _auto_id(row)
                if (not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', meme_id)
                        or meme_id.lower() in WINDOWS_RESERVED):
                    reason = f'自动 id 不合法: {meme_id!r}'
                else:
                    entry = _merge_llm(_entry_from_card(row, card, meme_id),
                                       row, card)
                    reason = _prewrite_checks(entry, meme_id)
                    if not reason and args.dry_run:
                        # without a real write, in-memory planning is the
                        # only thing that can catch same-round duplicates
                        pair = (build_mod.CHARACTERS.get(entry.get('character'), ''),
                                entry.get('title'))
                        if meme_id in planned_ids:
                            reason = '同一轮内 id 重复'
                        elif pair in planned_pairs:
                            reason = '同一轮内同角色同标题'
                        else:
                            planned_ids.add(meme_id)
                            planned_pairs.add(pair)
            if reason:
                skipped.append({'fp': row['fp'][:10], 'score': row['meme_score'],
                                'title': row['title'][:60], 'reason': reason})
                continue
            record = {'fp': row['fp'][:10], 'score': row['meme_score'],
                      'id': meme_id, 'title': row['title'][:60]}
            if args.dry_run:
                published.append(record)
                continue
            _write_entry(store, row, entry, meme_id)
            published.append(record)
        print(json.dumps({'dry_run': args.dry_run, 'threshold': args.threshold,
                          'scanned': len(pending),
                          'below_threshold': len(pending) - len(rows),
                          'published': published, 'skipped': skipped},
                         ensure_ascii=False, indent=2))
    finally:
        store.close()


def cmd_reject(args):
    store = store_mod.Store()
    row = _resolve(store.list(limit=10000), args.prefix)
    store.set_status(row['fp'], 'rejected', args.reason or '')
    store.close()
    print(f'rejected {row["fp"][:10]} ({args.reason or "no reason"})')


def cmd_stats(args):
    store = store_mod.Store()
    print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
    last = store.last_run()
    if last:
        print(f"last run {last['run_id'][:8]} fetched={last['fetched']} "
              f"new={last['new']} finished={last['finished_at'] or 'ABORTED'}")
    store.close()


def cmd_explain(args):
    store = store_mod.Store()
    row = _resolve(store.list(limit=10000), args.prefix)
    store.close()
    print(radar.explain(row, radar.score_item(row)))


def cmd_backfill(args):
    """Re-draft queued lead cards into LLM drafts.

    Candidates queued before an LLM key existed are rule-mode lead cards
    (kind=community, "awaiting verification") - the machine gate will never
    publish those. Backfill asks the LLM to meme-ify them, keeping the same
    draft shape `run` produces. Idempotent: candidates that already carry an
    llm_entry are skipped.
    """
    _load_dotenv()
    store = store_mod.Store()
    if not llm_mod.is_configured():
        store.close()
        raise SystemExit('LLM 未配置：services/meme-intake/.env 需要 '
                         'MEME_LLM_BASE_URL / MEME_LLM_API_KEY / MEME_LLM_MODEL 三变量')
    model = llm_mod.MemeLLM()
    rows = store.list(status=args.status, limit=args.limit)
    drafted = skipped = failed = 0
    for row in rows:
        card = _load_card(row)
        if llm_mod.valid_draft(card.get('llm_entry')):
            skipped += 1
            continue
        if args.dry_run:
            drafted += 1
            continue
        try:
            draft = model.draft({'title': row['title'], 'summary': row['summary'],
                                 'url': row['url']})
        except Exception:
            draft = None
        if not llm_mod.valid_draft(draft):
            failed += 1
            continue
        card.update({'suggested_character': draft.get('character', ''),
                     'suggested_kind': draft.get('kind', 'community'),
                     'source_title': draft.get('title', ''),
                     'source_url': row['url'],
                     'note': 'LLM 草稿，待人工核实。',
                     'llm_entry': draft})
        store.update_draft(row['fp'], json.dumps(card, ensure_ascii=False))
        drafted += 1
    store.close()
    print(json.dumps({'scanned': len(rows), 'drafted': drafted,
                      'already_drafted': skipped, 'failed': failed,
                      'dry_run': args.dry_run}, ensure_ascii=False))


def main():
    _load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('run', help='one incremental collection round')
    p.add_argument('--mode', default='all', choices=['all', 'selected'])
    p.add_argument('--window', default='7d')
    p.add_argument('--threshold', type=_finite_float, default=DEFAULT_THRESHOLD)
    p.add_argument('--llm-limit', type=int, default=3)
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser('list')
    p.add_argument('--status', default=None)
    p.add_argument('--min-score', type=float, default=0.0)
    p.add_argument('--limit', type=int, default=30)
    p.set_defaults(fn=cmd_list)
    for name in ('show', 'explain'):
        p = sub.add_parser(name)
        p.add_argument('prefix')
        p.set_defaults(fn=cmd_show if name == 'show' else cmd_explain)
    p = sub.add_parser('approve')
    p.add_argument('prefix')
    p.add_argument('--id', default=None)
    p.add_argument('--force', action='store_true')
    p.set_defaults(fn=cmd_approve)
    p = sub.add_parser('reject')
    p.add_argument('prefix')
    p.add_argument('--reason', default='')
    p.set_defaults(fn=cmd_reject)
    p = sub.add_parser('backfill',
                       help='re-draft queued lead cards via LLM (needs a key)')
    p.add_argument('--status', default='pending')
    p.add_argument('--limit', type=int, default=50)
    p.add_argument('--dry-run', action='store_true')
    p.set_defaults(fn=cmd_backfill)
    p = sub.add_parser('auto',
                       help='machine gate: publish written drafts that clear every check')
    p.add_argument('--threshold', type=_finite_float, default=AUTO_THRESHOLD)
    p.add_argument('--limit', type=int, default=50)
    p.add_argument('--dry-run', action='store_true')
    p.set_defaults(fn=cmd_auto)
    p = sub.add_parser('stats')
    p.set_defaults(fn=cmd_stats)
    args = parser.parse_args()
    if args.cmd in ('run', 'auto', 'approve', 'reject', 'backfill'):
        with exclusive(Path(os.environ.get('MEME_INTAKE_DB') or store_mod.DEFAULT_DB).with_suffix('.lock')):
            args.fn(args)
    else:
        args.fn(args)


if __name__ == '__main__':
    main()
