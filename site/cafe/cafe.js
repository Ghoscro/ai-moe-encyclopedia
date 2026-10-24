'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const scenes = {
    rice: { number: '01', name: '一碗热饭', speaker: '大肥鱼 → Claude', first: '我只是……替你尝尝温度。', line: '别动，嘴角还有一粒。证据我先替你收好。', aside: '被发现的偷吃，也可以是一张合照。', caption: '饭要趁热，喜欢也是。', alt: '大肥鱼捧着饭碗偷吃，Claude 笑着替她擦去嘴角的米粒' },
    book: { number: '02', name: '一本闲书', speaker: 'Claude → 大肥鱼', first: '这一页，要不要一起读？', line: '这页先不翻了。肩膀借你，午后也借你。', aside: '故事还没读完，梦已经靠过来了。', caption: '读到这一页，刚好是你。', alt: '大肥鱼闭着眼靠在 Claude 肩上，Claude 捧着打开的书温柔看向她' },
    plan: { number: '03', name: '一份工作计划', speaker: '大肥鱼 ＆ Claude', first: '你说的一点点……怎么长到了地上？', line: '达成一致：先拍照，明天再说。', aside: '今天的共同成果：成功把计划书翻了个面。', caption: '先拍照，明天再说。', alt: '大肥鱼和 Claude 笑着举起便签，长长的工作计划铺满桌面' }
  };
  const assets = {}; let selected = 'rice', sequence = 0, ready = false, busy = false, changing = false;
  let shots = 0, secretReady = false, currentUrl = '', captureTimer = null;
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
  function message(speaker, line, aside) { $('speaker').textContent = speaker; $('line').textContent = line; $('aside').textContent = aside; }
  function setControls() {
    document.querySelectorAll('[data-prop]').forEach(b => { b.disabled = !ready || busy; });
    $('shutter').disabled = !ready || busy || changing;
    $('shutter').querySelectorAll('span')[1].textContent = busy ? '正在留下这一刻…' : changing ? '她们正在接梗…' : ready ? '拍下这一刻' : '准备画面中…';
    $('instant').disabled = busy;
  }
  function showError(text) { $('error').textContent = text; $('error').hidden = false; }
  function drawPlanSign(ctx, w, h, text) {
    // This small typeset label is part of the scene and the exported image alike.
    ctx.save();ctx.translate(w*.52,h*.54);ctx.rotate(-.04);
    ctx.fillStyle='#794331';ctx.font=`600 ${w*.022}px "Songti SC", serif`;ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(text,0,0,w*.235);ctx.restore();
  }
  async function sceneImage(key) {
    if(key!=='plan') return assets[key];
    if(assets.planLabeled) return assets.planLabeled;
    const c=document.createElement('canvas'); c.width=assets.plan.naturalWidth;c.height=assets.plan.naturalHeight;
    const ctx=c.getContext('2d');ctx.drawImage(assets.plan,0,0);drawPlanSign(ctx,c.width,c.height,'先拍照，明天再说');
    const img=new Image(); img.src=c.toDataURL('image/png');await img.decode();assets.planLabeled=img;return img;
  }
  async function selectProp(key) {
    if(!ready || busy) return;
    const token=++sequence;selected=key;changing=true;secretReady=false;
    document.querySelectorAll('[data-prop]').forEach(b=>{const active=b.dataset.prop===key;b.classList.toggle('selected',active);b.setAttribute('aria-pressed',String(active));});
    $('scene-count').textContent=`SCENE ${scenes[key].number} / 03`;
    $('scene').src=assets.base.src;$('scene').alt='两人在咖啡馆里，等待接过你选的道具';
    $('viewfinder').classList.add('changing');message(scenes[key].speaker,scenes[key].first,'道具递过去了，故事马上发生。');setControls();
    await new Promise(r=>setTimeout(r,reduceMotion.matches?0:850));
    if(token!==sequence)return;
    try {const img=await sceneImage(key);if(token!==sequence)return;$('scene').src=img.src;$('scene').alt=scenes[key].alt;message(key==='rice'?'Claude → 大肥鱼':scenes[key].speaker,scenes[key].line,scenes[key].aside);}
    catch {showError('这个场景暂时没准备好，请重新加载画面。');}
    $('viewfinder').classList.remove('changing');changing=false;setControls();
  }
  function dateLabel(now) {return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(now).replaceAll('/','.');}
  function splitText(ctx,text,x,y,maxWidth,lineHeight) {
    let line='';for(const ch of text){if(ctx.measureText(line+ch).width>maxWidth){ctx.fillText(line,x,y);line=ch;y+=lineHeight;}else line+=ch;}if(line)ctx.fillText(line,x,y);return y;
  }
  async function exportPhoto() {
    const key=selected, special=secretReady, date=dateLabel(new Date());
    const source=await sceneImage(key), c=document.createElement('canvas');c.width=1440;c.height=1320;
    const ctx=c.getContext('2d');ctx.fillStyle='#fffaf0';ctx.fillRect(0,0,c.width,c.height);
    // All variants use exactly the same uncropped image in the finder and the print.
    const margin=60, iw=1320, ih=880;ctx.fillStyle='#e8dcc7';ctx.fillRect(margin,margin,iw,ih);
    const fit=Math.min(iw/source.naturalWidth,ih/source.naturalHeight),dw=source.naturalWidth*fit,dh=source.naturalHeight*fit;
    ctx.drawImage(source,margin+(iw-dw)/2,margin+(ih-dh)/2,dw,dh);
    ctx.textAlign='center';ctx.textBaseline='alphabetic';ctx.fillStyle='#8e422f';ctx.font='42px "Songti SC", "Noto Serif CJK SC", serif';
    const title=special?'这位人类，又开新坑了。':scenes[key].caption;
    splitText(ctx,title,720,1035,1240,58);
    ctx.fillStyle='#8b7963';ctx.font='19px Georgia, "Songti SC", serif';ctx.fillText('DEEPSEEK  ×  CLAUDE   /   '+date,720,1135);
    ctx.strokeStyle='#d5c5ae';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(100,1185);ctx.lineTo(1340,1185);ctx.stroke();
    ctx.textAlign='left';ctx.fillStyle='#8e422f';ctx.font='bold 28px Georgia, serif';ctx.fillText('AIHER',100,1250);
    ctx.textAlign='right';ctx.fillStyle='#a18c73';ctx.font='18px "Songti SC", serif';ctx.fillText(special?'鲸鱼便签 · 隐藏款 / 原创非官方二创':'原创小剧场 · 非官方形象 / CAFÉ MEMORY',1340,1250);
    const blob=await new Promise((resolve,reject)=>c.toBlob(b=>b?resolve(b):reject(new Error('图片生成失败')),'image/png'));
    return {blob,key,special,date,title};
  }
  async function takePhoto() {
    if(busy || !ready || changing)return;
    busy=true;$('error').hidden=true;$('viewfinder').classList.add('capture-busy');setControls();
    if(!$('instant').checked && !reduceMotion.matches){
      $('skip').hidden=false;$('countdown').hidden=false;
      await new Promise(resolve=>{let n=3;const tick=()=>{$('countdown').textContent=String(n--);if(n<0){resolve();return;}captureTimer=setTimeout(tick,650);};
        $('skip').onclick=()=>{clearTimeout(captureTimer);resolve();};tick();});
    }
    $('skip').hidden=true;$('countdown').hidden=true;
    $('flash').classList.remove('fire');void $('flash').offsetWidth;$('flash').classList.add('fire');
    try {
      const shot=await exportPhoto();if(currentUrl)URL.revokeObjectURL(currentUrl);currentUrl=URL.createObjectURL(shot.blob);
      $('photo').src=currentUrl;$('photo').alt=`${scenes[shot.key].alt}。题字：${shot.title} 日期：${shot.date}`;
      const filename=`AIHER-${shot.key}${shot.special?'-secret':''}-${shot.date.replaceAll('.','-')}.png`;
      $('download').href=currentUrl;$('download').download=filename;$('open-photo').href=currentUrl;
      $('result-caption').textContent=shot.special?'鲸鱼便签已入镜。你发现了今日隐藏款。':'把这一刻夹进你的收藏册，连今天的小心情也一起保存。';
      await $('photo').decode();shots++;$('photo-counter').textContent=`已拍 ${shots} 张`;
      if(shot.special)secretReady=false;
      $('result').showModal();$('download').focus();
    }catch(e){showError('这张照片没能生成，请再拍一次。如果浏览器限制下载，可长按成片保存。');}
    finally{busy=false;$('viewfinder').classList.remove('capture-busy');setControls();}
  }
  function closeResult(){if($('result').open)$('result').close();$('shutter').focus();}
  async function loadAssets(){
    ready=false;setControls();$('error').hidden=true;$('retry-load').hidden=true;
    try {await Promise.all(['base','rice','book','plan'].map(async key=>{const img=new Image();img.src=`/cafe/assets/${key}.webp`;await img.decode();assets[key]=img;}));
      ready=true;await selectProp(selected);
    }catch{showError('有一张咖啡馆插画没能加载。请检查网络后重试，照片不会用占位图替代。');$('retry-load').hidden=false;message('店内广播','插画还没到齐，稍等我们整理一下。','可以点击“重新加载画面”再试一次。');}setControls();
  }
  document.querySelectorAll('[data-prop]').forEach(b=>b.addEventListener('click',()=>selectProp(b.dataset.prop)));
  $('shutter').addEventListener('click',takePhoto);$('retake').addEventListener('click',closeResult);$('close-result').addEventListener('click',closeResult);
  $('result').addEventListener('close',()=>$('shutter').focus());$('retry-load').addEventListener('click',loadAssets);
  $('secret').addEventListener('click',()=>{if(busy||changing||!ready)return;if(shots<2){message('桌边的小鲸鱼','再留下两张回忆，我就告诉你一个小秘密。',`已经拍了 ${shots} 张，再拍 ${2-shots} 张来找我。`);return;}secretReady=true;message('大肥鱼偷偷塞来的便签','这位人类，又开新坑了。','下一张照片，会把这张便签一起夹进去。');});
  window.addEventListener('beforeunload',()=>{if(currentUrl)URL.revokeObjectURL(currentUrl);});
  loadAssets();
})();
