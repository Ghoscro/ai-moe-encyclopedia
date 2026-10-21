(() => {
  'use strict';
  const root = document.querySelector('#archive[data-archive-collection]');
  if (!root || root.hasAttribute('data-archive-managed')) return;
  const query = root.querySelector('#archive-query');
  const results = root.querySelector('#archive-results');
  const disclosure = root.querySelector('details.archive-disclosure');
  if (!query || !results || !disclosure) return;

  const batch = 12;
  const cards = [...root.querySelectorAll('article.archive-entry[id][data-meme]')];
  const normalize = value => value.normalize('NFKC').toLocaleLowerCase().replace(/\s+/g, ' ').trim();
  const index = cards.map(card => ({
    card,
    theme: card.dataset.archiveTheme,
    text: normalize(card.dataset.archiveSearch || `${card.dataset.meme} ${card.querySelector('.archive-body')?.textContent || ''}`)
  }));
  const topics = [...root.querySelectorAll('[data-archive-topic]')];
  const more = root.querySelector('[data-archive-more]');
  const showAll = root.querySelector('[data-archive-show-all]');
  const clear = root.querySelector('[data-archive-clear]');
  const random = root.querySelector('[data-archive-random]');
  const empty = root.querySelector('.archive-empty');
  const visibleLabels = [...root.querySelectorAll('[data-archive-visible]')];
  const spotlights = root.querySelector('.archive-spotlights');
  let topic = 'all';
  let limit = batch;
  let matches = [];
  let toastTimer;
  let scrollVersion = 0;

  function focusCard(card) {
    card.setAttribute('tabindex', '-1');
    card.focus({ preventScroll: true });
  }

  function render() {
    const term = normalize(query.value);
    matches = index.filter(row => (topic === 'all' || row.theme === topic) && row.text.includes(term));
    const shown = new Set(matches.slice(0, limit).map(row => row.card));
    cards.forEach(card => { card.hidden = !shown.has(card); });
    const active = topic !== 'all' || Boolean(term);
    results.textContent = active ? `找到 ${matches.length} 条收藏` : `共 ${cards.length} 条收藏`;
    visibleLabels.forEach(label => { label.textContent = `已展示 ${shown.size} / ${matches.length} 条`; });
    topics.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.archiveTopic === topic)));
    if (empty) empty.hidden = matches.length !== 0;
    if (clear) clear.hidden = !active;
    if (more) more.hidden = shown.size >= matches.length;
    if (showAll) showAll.hidden = shown.size >= matches.length;
    if (random) random.disabled = matches.length === 0;
    if (spotlights) spotlights.hidden = active;
    const focusedCard = document.activeElement?.closest('.archive-entry');
    if (focusedCard && root.contains(focusedCard) && focusedCard.hidden) query.focus({ preventScroll: true });
  }

  function reset() {
    query.value = '';
    topic = 'all';
    limit = batch;
    render();
    query.focus({ preventScroll: true });
  }

  function hashTarget(hash) {
    let id;
    try { id = decodeURIComponent(hash.slice(1)); } catch { return null; }
    const target = id && document.getElementById(id);
    if (!target || (target !== root && !root.contains(target))) return null;
    const card = target.closest('article.archive-entry');
    return target === root ? { target: root, card: null } : card && cards.includes(card) ? { target: card, card } : null;
  }

  function scrollTo(target) {
    const version = ++scrollVersion;
    requestAnimationFrame(() => {
      if (version !== scrollVersion) return;
      // The page has html{scroll-behavior:smooth}; hash correction must be immediate,
      // including for reduced-motion readers and after native fragment scrolling.
      const style = document.documentElement.style;
      const previous = style.getPropertyValue('scroll-behavior');
      const priority = style.getPropertyPriority('scroll-behavior');
      style.setProperty('scroll-behavior', 'auto', 'important');
      target.scrollIntoView({ behavior: 'auto', block: 'start' });
      if (previous) style.setProperty('scroll-behavior', previous, priority);
      else style.removeProperty('scroll-behavior');
    });
  }

  function reveal(hash, focus) {
    const found = hashTarget(hash);
    if (!found) return false;
    query.value = '';
    topic = 'all';
    limit = found.card ? Math.ceil((cards.indexOf(found.card) + 1) / batch) * batch : batch;
    disclosure.open = true;
    render();
    if (found.card) found.card.querySelectorAll('details.archive-story').forEach(story => { story.open = true; });
    if (focus) focusCard(found.target);
    scrollTo(found.target);
    return true;
  }

  function navigate(card) {
    const hash = `#${encodeURIComponent(card.id)}`;
    if (location.hash === hash) reveal(hash, true);
    else location.hash = hash;
  }

  function say(message) {
    const toast = document.getElementById('toast');
    if (!toast) return;
    clearTimeout(toastTimer);
    toast.textContent = message;
    toast.classList.add('visible');
    toastTimer = setTimeout(() => toast.classList.remove('visible'), 3000);
  }

  function copyText(card) {
    const body = card.querySelector('.archive-body');
    const clone = body?.cloneNode(true);
    if (clone) {
      clone.querySelectorAll('.entry-actions, [data-archive-copy], button, input, textarea, select, script, style').forEach(node => node.remove());
      clone.querySelectorAll('br').forEach(node => node.replaceWith('\n'));
      clone.querySelectorAll('p, div, li, blockquote, figcaption').forEach(node => node.append('\n'));
    }
    const title = card.querySelector('h3')?.textContent.trim() || card.dataset.meme;
    const status = card.querySelector('.archive-status, [data-archive-status]')?.textContent.trim() || '';
    const content = clone?.textContent.trim() || '';
    const sources = [...new Set([...(body?.querySelectorAll('a[href]') || [])].map(link => link.href).filter(href => /^https?:\/\//i.test(href)))];
    return [title, status, content, ...sources, `${location.origin}${location.pathname}#${encodeURIComponent(card.id)}`].filter(Boolean).join('\n');
  }

  query.addEventListener('input', () => { limit = batch; render(); });
  root.addEventListener('click', async event => {
    const button = event.target.closest('button');
    if (!button || !root.contains(button)) return;
    if (button.hasAttribute('data-archive-topic')) {
      topic = button.dataset.archiveTopic;
      limit = batch;
      render();
    } else if (button.hasAttribute('data-archive-clear') || button.hasAttribute('data-archive-reset')) {
      reset();
    } else if (button.hasAttribute('data-archive-more') || button.hasAttribute('data-archive-show-all')) {
      const previousCount = Math.min(limit, matches.length);
      limit = button.hasAttribute('data-archive-show-all') ? matches.length : limit + batch;
      render();
      if (button.hidden && matches[previousCount]) focusCard(matches[previousCount].card);
    } else if (button.hasAttribute('data-archive-random')) {
      if (matches.length) navigate(matches[Math.floor(Math.random() * matches.length)].card);
    } else if (button.hasAttribute('data-archive-copy')) {
      const card = button.closest('article.archive-entry');
      if (!card) return;
      try {
        if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
        await navigator.clipboard.writeText(copyText(card));
        say('已复制全文、出处和这一页的链接。');
      } catch {
        say('浏览器未允许复制，请展开条目后选中文字手动复制。');
      }
    }
  });

  document.addEventListener('click', event => {
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const anchor = event.target.closest('a[href]');
    if (!anchor || anchor.hasAttribute('download') || (anchor.target && anchor.target !== '_self')) return;
    const url = new URL(anchor.href, document.baseURI);
    if (url.origin !== location.origin || url.pathname !== location.pathname || url.search !== location.search || !hashTarget(url.hash)) return;
    event.preventDefault();
    if (url.hash === location.hash) reveal(url.hash, true);
    else location.hash = url.hash;
  });
  window.addEventListener('hashchange', () => { reveal(location.hash, true); });
  window.addEventListener('load', () => {
    const found = hashTarget(location.hash);
    if (found && !found.target.hidden) scrollTo(found.target);
  }, { once: true });

  render();
  reveal(location.hash, false);
  root.setAttribute('data-archive-managed', '');
})();
