(() => {
  'use strict';
  const repo = 'https://github.com/Ghoscro/ai-moe-encyclopedia';
  const form = document.getElementById('contribute-form');
  const message = document.getElementById('form-message');
  const preview = document.getElementById('preview');
  let draftId;
  const get = id => document.getElementById(id).value.trim();
  function draft() {
    if (!form.reportValidity()) return null;
    const source = get('source-url');
    if (source) {
      let url;
      try { url = new URL(source); } catch { message.textContent = '请填写有效的来源链接。'; return null; }
      if (url.protocol !== 'https:' || url.username || url.password) { message.textContent = '来源请使用公开 HTTPS 链接。'; return null; }
      if (!get('source-title')) { message.textContent = '请补上出处名称。'; return null; }
    }
    if (get('kind') === 'sourced' && !source) { message.textContent = '有出处的趣事需要原始出处链接。'; return null; }
    const tags = get('tags').split(/[,，]/).map(s => s.trim()).filter(Boolean);
    if (tags.length > 8 || tags.some(t => t.length > 24)) { message.textContent = '标签最多 8 个，每个不超过 24 个字。'; return null; }
    const day = new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
    if (!draftId) draftId = `${get('character')}-${day.replaceAll('-', '')}-${crypto.randomUUID().slice(0, 8)}`;
    const row = {id:draftId, character:get('character'), title:get('title'), summary:get('summary'), body:get('body'), tags, kind:get('kind'), sources:source ? [{title:get('source-title'),url:source}] : [], contributor:get('contributor'), date:day, editor_note:get('editor-note')};
    const text = JSON.stringify(row, null, 2) + '\n';
    preview.textContent = text;
    return {row, text};
  }
  form.addEventListener('submit', event => {
    event.preventDefault();
    const data = draft();
    if (!data) return;
    const url = new URL(repo + '/new/main/content/memes');
    url.searchParams.set('filename', data.row.id + '.json');
    url.searchParams.set('value', data.text);
    if (url.href.length > 7500) { message.textContent = '这篇内容较长，请下载投稿文件，再通过 GitHub 上传并提交 PR。'; return; }
    window.open(url.href, '_blank', 'noopener,noreferrer');
    message.textContent = '已打开 GitHub 投稿页。按提示创建 Fork、保存文件并提出 Pull Request；当前内容还没有提交。';
  });
  document.getElementById('download').addEventListener('click', () => {
    const data = draft(); if (!data) return;
    const url = URL.createObjectURL(new Blob([data.text], {type:'application/json;charset=utf-8'}));
    const a = document.createElement('a'); a.href = url; a.download = data.row.id + '.json'; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    message.textContent = '投稿文件已生成。请上传到仓库的 content/memes 目录并提出 PR。';
  });
  document.getElementById('copy').addEventListener('click', async () => {
    const data = draft(); if (!data) return;
    try { await navigator.clipboard.writeText(data.text); message.textContent = '已复制投稿文件内容。'; }
    catch { message.textContent = '浏览器未允许复制，请展开下方内容手动复制。'; preview.parentElement.open = true; }
  });
  const character = new URLSearchParams(location.search).get('character');
  if ([...document.getElementById('character').options].some(o => o.value === character)) document.getElementById('character').value = character;
})();
