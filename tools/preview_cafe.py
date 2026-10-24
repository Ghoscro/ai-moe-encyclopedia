"""One-command local cafe preview. Never restarts another service."""
import argparse
import functools
import http.server
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description='Build and preview AIHER cafe locally')
p.add_argument('--port', type=int, default=8794)
args = p.parse_args()
subprocess.run([sys.executable, str(ROOT/'tools/build.py')], cwd=ROOT, check=True)
url = f'http://127.0.0.1:{args.port}/cafe/'
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(url, timeout=2) as response:
        existing = response.read().decode('utf-8')
except Exception:
    existing = ''
if '双人拍立得 · AIHER' in existing:
    print('本地预览已在运行：' + url)
    raise SystemExit(0)
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT/'dist'))
try:
    server = http.server.ThreadingHTTPServer(('127.0.0.1', args.port), handler)
except OSError:
    raise SystemExit(f'端口 {args.port} 已被其他服务占用；请用 --port 8795。未停止任何服务。')
print('本地完成，未上线。打开 ' + url, flush=True)
try:
    server.serve_forever()
except KeyboardInterrupt:
    server.server_close()
