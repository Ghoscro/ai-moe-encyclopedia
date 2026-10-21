"""Copy checked static output into the separately checked-out site branch."""
import json
import os
import re
import shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
target=ROOT/'.publish'
if target.is_symlink() or not (target/'.git').exists():
    raise SystemExit('Expected an isolated site-branch checkout')
for p in target.iterdir():
    if p.name=='.git': continue
    if p.is_symlink() or p.is_file(): p.unlink()
    else: shutil.rmtree(p)
shutil.copytree(ROOT/'dist',target,dirs_exist_ok=True)
sha=os.environ['SOURCE_COMMIT']
if not re.fullmatch(r'[0-9a-f]{40}',sha): raise SystemExit('Invalid commit')
receipt=json.dumps({'source_commit':sha,'repository':'Ghoscro/ai-moe-encyclopedia'},indent=2)+'\n'
(target/'release.json').write_text(receipt,encoding='utf-8')
(target/'meme/release.json').write_text(receipt,encoding='utf-8')
