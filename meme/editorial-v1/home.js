(() => {
  'use strict';
  const entries = JSON.parse(document.getElementById('search-data').textContent);
  const forms = [...document.querySelectorAll('form.search')];
  const results = document.getElementById('results');
  const list = document.getElementById('results-list');
  const count = document.getElementById('result-count');
  const toast = document.getElementById('toast');
  let timer;
  function say(message) { toast.textContent = message; toast.classList.add('show'); clearTimeout(timer); timer = setTimeout(() => toast.classList.remove('show'), 3000); }
  function search(query, scroll = true) {
    query = query.trim().slice(0, 120);
    for (const form of forms) form.elements.q.value = query;
    list.replaceChildren();
    if (!query) { results.hidden = true; return; }
    const terms = query.toLocaleLowerCase().split(/\s+/).filter(Boolean);
    const matches = entries.filter(e => terms.every(t => `${e.title} ${e.owner} ${e.text} ${e.keywords || ''}`.toLocaleLowerCase().includes(t)));
    results.hidden = false;
    count.textContent = matches.length ? `找到 ${matches.length} 条与「${query}」有关的内容` : `没有找到「${query}」。试试角色名，或换个关键词。`;
    for (const entry of matches.slice(0, 80)) {
      const a = document.createElement('a'); a.className = 'result-item'; a.href = entry.href;
      const small = document.createElement('small'); small.textContent = entry.owner;
      const h3 = document.createElement('h3'); h3.textContent = entry.title;
      const p = document.createElement('p'); p.textContent = entry.text.slice(0, 110);
      a.append(small, h3, p); list.append(a);
    }
    if (matches.length > 80) { const p = document.createElement('p'); p.textContent = '已展示前 80 条，请增加关键词缩小范围。'; list.append(p); }
    if (scroll) results.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start' });
  }
  for (const form of forms) form.addEventListener('submit', e => { e.preventDefault(); search(form.elements.q.value); });
  for (const button of document.querySelectorAll('[data-query]')) button.addEventListener('click', () => search(button.dataset.query));
  document.getElementById('clear-search').addEventListener('click', () => { search(''); document.getElementById('hero-query').focus(); });
  function random() {
    const candidates = entries.filter(e => e.type === 'meme');
    if (!candidates.length) { say('暂时没有可随机浏览的条目。'); return; }
    const entry = candidates[Math.floor(Math.random() * candidates.length)];
    location.href = entry.href;
  }
  for (const button of document.querySelectorAll('[data-random]')) button.addEventListener('click', random);
  document.addEventListener('keydown', e => {
    if (e.ctrlKey || e.metaKey || e.altKey || e.isComposing || e.target.isContentEditable || /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
    if (e.key === '/') { e.preventDefault(); document.getElementById('hero-query').focus(); }
    else if (e.key.toLowerCase() === 'r') random();
  });
  const query = new URLSearchParams(location.search).get('q');
  if (query) search(query, false);
})();
