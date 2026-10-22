/* One event per visible document; retries use the same random id. No cookies. */
(() => {
  'use strict';
  const panel = document.querySelector('[data-pageviews]');
  if (!panel || location.origin !== 'https://aiher.ai') return;
  const path = new URL(document.querySelector('link[rel="canonical"]').href).pathname;
  const event = crypto.randomUUID();
  let started = false;
  async function count() {
    if (started || document.visibilityState !== 'visible') return;
    started = true;
    document.removeEventListener('visibilitychange', count);
    for (let attempt = 0; attempt < 2; attempt++) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 4000);
      try {
        const response = await fetch('/api/stats', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({path, event}), cache: 'no-store',
          signal: controller.signal, credentials: 'omit'
        });
        if (!response.ok) throw new Error('stats unavailable');
        const data = await response.json();
        if (!['total', 'today', 'page'].every(key => Number.isSafeInteger(data[key]) && data[key] >= 0)) throw new Error('invalid stats');
        for (const key of ['total', 'today', 'page']) panel.querySelector(`[data-count="${key}"]`).textContent = data[key].toLocaleString('zh-CN');
        panel.title = `自 ${data.started_at.slice(0, 10)} 起统计页面浏览次数；今日按北京时间计算`;
        panel.querySelector('[data-stats-note]').textContent = `自 ${data.started_at.slice(0, 10)} 起统计`;
        return;
      } catch (_) {
        if (attempt === 1) {
          panel.title = '浏览量暂时不可用';
          panel.querySelector('[data-stats-note]').textContent = '统计暂不可用';
        }
      } finally { clearTimeout(timer); }
    }
  }
  document.addEventListener('visibilitychange', count);
  count();
})();
