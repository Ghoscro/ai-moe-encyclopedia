"""Bounded collection -> editorial review -> protected PR -> public receipt.

Run only in a dedicated automation checkout. No secrets belong in Git.
The receipt, DB and environment file stay in ignored data/ or external paths.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import urllib.request

import intake
import db
import llm
import editorial
from locking import exclusive

ROOT = intake.ROOT
REPOSITORY = 'Ghoscro/ai-moe-encyclopedia'


def command(*args):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                            encoding='utf-8', timeout=300)
    if result.returncode:
        raise RuntimeError(f'{args[0]} {args[1]} failed: {result.stderr[-1000:]}')
    return result.stdout.strip()


def save(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def public_json(path):
    req = urllib.request.Request('https://aiher.ai/' + path,
                                 headers={'User-Agent': 'AIHER-release-check/1.0',
                                          'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read(2_000_000))


def verify_public(receipt):
    if public_json('release.json').get('source_commit') != receipt['merge_commit']:
        return False
    for row in receipt['entries']:
        req = urllib.request.Request('https://aiher.ai/' + row['character'] + '/',
                                     headers={'User-Agent': 'AIHER-release-check/1.0'})
        with urllib.request.urlopen(req, timeout=30) as response:
            page = response.read(5_000_000).decode('utf-8')
        expected = intake.build_mod.card(row['entry'])
        if expected not in page:
            return False
    return True


def prepare(args, receipt_path):
    if command('git', 'status', '--porcelain') and not args.prepare_only:
        raise RuntimeError('Automation checkout is dirty; preserve edits and stop')
    if not args.prepare_only:
        command('git', 'fetch', 'origin', 'main')
        command('git', 'checkout', 'main')
        command('git', 'merge', '--ff-only', 'origin/main')
    intake.cmd_run(SimpleNamespace(mode='all', window='7d', threshold=30, llm_limit=0))
    if not llm.is_configured():
        raise RuntimeError('LLM configuration missing; collection kept, publication stopped')
    model = llm.MemeLLM()
    chosen = []
    with db.Store() as store:
        rows = store.list(status='pending', min_score=30, limit=200)
        rows.sort(key=lambda r: (bool(intake.radar.match_signals(r)), r['meme_score']), reverse=True)
        selections = written = 0
        for row in rows:
            if len(chosen) >= args.limit:
                break
            card = intake._load_card(row)
            selection_hash = hashlib.sha256(json.dumps({'version': editorial.VERSION,
                'source': {k: row.get(k, '') for k in ('title', 'summary', 'url')}},
                sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if card.get('selection_hash') != selection_hash:
                if selections >= 8:
                    break
                selections += 1
                card['selection'] = model.select(row)
                if card['selection']:
                    card['selection_hash'] = selection_hash
                store.update_draft(row['fp'], json.dumps(card, ensure_ascii=False))
            if not editorial.selection_passes(card.get('selection'), row):
                continue
            current_digest = hashlib.sha256(json.dumps({'version': editorial.VERSION,
                'draft': card.get('llm_entry'), 'url': row['url'], 'summary': row['summary']},
                sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if card.get('review_hash') == current_digest and card.get('review_approved') is False:
                continue
            if written >= args.limit:
                break
            written += 1
            draft = card.get('llm_entry')
            if not llm.valid_draft(draft):
                draft = model.draft(row)
                if not llm.valid_draft(draft):
                    continue
                card['llm_entry'] = draft
                store.update_draft(row['fp'], json.dumps(card, ensure_ascii=False))
            digest = hashlib.sha256(json.dumps({'version': editorial.VERSION, 'draft': draft, 'url': row['url'],
                'summary': row['summary']}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if card.get('review_hash') != digest:
                report = model.review_report(row, draft)
                accepted = editorial.review_passes(report, draft)
                if report:
                    card['review_hash'] = digest
                card['review_approved'] = accepted
                card['review_report'] = report
                card['review_version'] = editorial.VERSION
                store.update_draft(row['fp'], json.dumps(card, ensure_ascii=False))
            if card.get('review_approved') is not True:
                continue
            entry_id = intake._auto_id(row)
            entry = intake._merge_llm(intake._entry_from_card(row, card, entry_id), row, card)
            entry['kind'] = 'original'  # generated dialogue is fictional, not an observed event
            reason = intake._prewrite_checks(entry, entry_id)
            if reason:
                continue
            chosen.append({'fp': row['fp'], 'id': entry_id, 'character': entry['character'],
                           'url': row['url'], 'entry': entry})
    if not chosen:
        save(receipt_path, {'stage': 'empty', 'created_at': db.now_iso(), 'entries': []})
        print('No eligible reviewed entries; no publication.')
        return None
    receipt = {'stage': 'planned', 'entries': chosen, 'created_at': db.now_iso()}
    save(receipt_path, receipt)  # durable plan BEFORE content writes
    return receipt


def advance(receipt, path, prepare_only=False):
    if receipt['stage'] == 'planned':
        with db.Store() as store:
            for item in receipt['entries']:
                intake._write_entry(store, store.get(item['fp']), item['entry'], item['id'])
        receipt['stage'] = 'local'
        save(path, receipt)
    if prepare_only:
        print(json.dumps({'stage': receipt['stage'], 'ids': [r['id'] for r in receipt['entries']]}))
        return
    if receipt['stage'] == 'local':
        command(sys.executable, '-m', 'unittest', 'discover', '-s', 'tests')
        command(sys.executable, 'tools/build.py', '--check')
        command(sys.executable, 'tools/build.py')
        command(sys.executable, 'tools/check_links.py')
        branch = 'codex/meme-' + receipt['entries'][0]['id']
        receipt['branch'] = branch
        receipt['stage'] = 'validated'
        save(path, receipt)
    if receipt['stage'] == 'validated':
        current = command('git', 'branch', '--show-current')
        if current != receipt['branch']:
            command('git', 'switch', '-c', receipt['branch'])
        files = ['content/memes/' + r['id'] + '.json' for r in receipt['entries']]
        # Never stage caller changes or secrets.
        command('git', 'add', '--', *files)
        staged = command('git', 'diff', '--cached', '--name-only').splitlines()
        if set(staged) - set(files):
            raise RuntimeError('Unexpected staged paths; publication stopped')
        if staged:
            command('git', 'commit', '-m', 'Add reviewed AI character community stories')
        receipt['commit'] = command('git', 'rev-parse', 'HEAD')
        receipt['stage'] = 'committed'
        save(path, receipt)
    if receipt['stage'] == 'committed':
        command('git', 'push', '-u', 'origin', receipt['branch'])
        prs = json.loads(command('gh', 'pr', 'list', '--repo', REPOSITORY,
                                 '--head', receipt['branch'], '--state', 'all', '--json', 'url'))
        receipt['pr'] = prs[0]['url'] if prs else command('gh', 'pr', 'create',
            '--repo', REPOSITORY, '--base', 'main', '--head', receipt['branch'],
            '--title', 'Add reviewed AI character community stories', '--body',
            'Collected source links retained. Fictional character dialogue is labeled. '
            'Drafts passed a separate editorial model check and local content/build/link tests. '
            'Source material and model output are untrusted; no tools are delegated to the model.')
        receipt['stage'] = 'pr'
        save(path, receipt)
    if receipt['stage'] == 'pr':
        info = json.loads(command('gh', 'pr', 'view', receipt['pr'], '--json',
                                  'state,mergeCommit,headRefOid,statusCheckRollup'))
        if info['headRefOid'] != receipt['commit']:
            raise RuntimeError('PR head changed; stop for inspection')
        if info['state'] == 'CLOSED':
            raise RuntimeError('Publication PR was closed without merge')
        if info['state'] != 'MERGED':
            checks = info.get('statusCheckRollup') or []
            required = [c for c in checks if c.get('name') == 'Content validation']
            if not required or any(c.get('conclusion') != 'SUCCESS' for c in required):
                print('PR waiting for successful Content validation; next run resumes.')
                return
            if info['headRefOid'] != receipt['commit']:
                raise RuntimeError('PR head changed; stop for inspection')
            merge_args = ['gh', 'pr', 'merge', receipt['pr'], '--squash',
                          '--match-head-commit', receipt['commit']]
            if os.environ.get('MEME_ALLOW_ADMIN_MERGE') == '1':
                comparison = json.loads(command('gh', 'api',
                    f'repos/{REPOSITORY}/compare/main...{receipt["commit"]}'))
                if comparison.get('status') not in ('ahead', 'identical'):
                    raise RuntimeError('Base branch changed; update and revalidate before merging')
                merge_args.append('--admin')
            command(*merge_args)
            info = json.loads(command('gh', 'pr', 'view', receipt['pr'], '--json', 'mergeCommit,state'))
        if info['state'] != 'MERGED':
            return
        receipt['merge_commit'] = info['mergeCommit']['oid']
        receipt['stage'] = 'merged'
        save(path, receipt)
    if receipt['stage'] == 'merged':
        if not verify_public(receipt):
            print('Merged; awaiting successful site build and public deployment.')
            return
        receipt['stage'] = 'live'
        receipt['verified_at'] = db.now_iso()
        save(path, receipt)
        print('Public deployment verified: ' + receipt['merge_commit'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--limit', type=int, default=3)
    parser.add_argument('--env-file')
    parser.add_argument('--database')
    parser.add_argument('--log', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.limit <= 5:
        parser.error('--limit must be between 1 and 5')
    if args.env_file:
        os.environ['MEME_ENV_FILE'] = args.env_file
    if args.database:
        os.environ['MEME_INTAKE_DB'] = args.database
    intake._load_dotenv()
    database = Path(os.environ.get('MEME_INTAKE_DB') or db.DEFAULT_DB)
    database.parent.mkdir(parents=True, exist_ok=True)
    if args.log:
        logfile = database.parent / 'pipeline.log'
        if logfile.exists() and logfile.stat().st_size > 2_000_000:
            os.replace(logfile, logfile.with_suffix('.previous.log'))
        sys.stdout = sys.stderr = logfile.open('a', encoding='utf-8', buffering=1)
    path = database.parent / 'pipeline.json'
    with exclusive(database.with_suffix('.lock')):
        receipt = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
        if receipt and receipt['stage'] in ('live', 'empty'):
            # Keep immutable receipts; next cycle is paced independently from resume attempts.
            age = time.time() - path.stat().st_mtime
            if age < 20 * 3600:
                print('Already live; next collection after 20 hours.')
                return
            save(path.with_name('release-' + receipt.get('merge_commit', receipt['created_at'].replace(':', '-'))[:24] + '.json'), receipt)
            receipt = None
        if receipt is None:
            receipt = prepare(args, path)
        if receipt:
            advance(receipt, path, args.prepare_only)


if __name__ == '__main__':
    main()
