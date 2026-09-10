import { Viewer } from './viewer.js?v=1';

const $ = (id) => document.getElementById(id);
const state = { sid: null, session: null, sel: -1 };

const viewer = new Viewer($('view'));

function toast(msg, ms = 2600) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('on');
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove('on'), ms);
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return r.json();
}

function hexOf(c) { return (c || '').toUpperCase(); }

// ------------------------------------------------------------ 会话与部件
async function newSession() {
  const j = await api('/api/sessions', { method: 'POST' });
  state.sid = j.session_id;
  return j.session_id;
}

async function refreshSession() {
  state.session = await api(`/api/sessions/${state.sid}`);
  renderParts();
  renderPalette();
}

function renderParts() {
  const s = state.session;
  const el = $('parts');
  $('pcount').textContent = s.parts.length ? `（${s.parts.length} 件）` : '';
  if (!s.parts.length) { el.innerHTML = '<span class="hint">尚未导入模型件</span>'; return; }
  el.innerHTML = '';
  for (const p of s.parts) {
    const d = document.createElement('div');
    d.className = 'part' + (p.index === state.sel ? ' sel' : '');
    d.innerHTML = `<span class="sw" style="background:${p.color || '#8a939e'}"></span>`
      + `<span class="nm">${p.name}</span><div class="meta">${p.tris.toLocaleString()} 面 · ${p.area_cm2} cm²</div>`;
    d.addEventListener('click', () => { state.sel = p.index; renderParts(); renderPalette(); });
    el.appendChild(d);
  }
}

function renderPalette() {
  const s = state.session;
  const el = $('palette');
  if (!s.palette.length) { el.innerHTML = '<span class="hint">色板将显示在这里</span>'; return; }
  el.innerHTML = '';
  for (const c of s.palette) {
    const sw = document.createElement('span');
    sw.className = 'swatch';
    sw.style.background = c.hex;
    sw.title = `${c.hex}（占比 ${(c.ratio * 100).toFixed(1)}%）` + (state.sel >= 0 ? ' — 点击涂给选中件' : '（先选中一个件）');
    sw.addEventListener('click', async () => {
      if (state.sel < 0) { toast('先在件列表里选中一个件'); return; }
      await api(`/api/sessions/${state.sid}/color/${state.sel}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ color: c.hex }),
      });
      viewer.setPartColor(state.sel, c.hex);
      await refreshSession();
      toast(`${state.session.parts[state.sel].name} → ${c.hex}`);
    });
    el.appendChild(sw);
  }
}

// ------------------------------------------------------------ 上传
$('imgfile').addEventListener('change', async (ev) => {
  const f = ev.target.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append('file', f);
  const j = await api(`/api/sessions/${state.sid}/image`, { method: 'POST', body: fd });
  $('imgname').textContent = j.image_name;
  state.session.palette = j.palette;
  renderPalette();
  toast(`提取到 ${j.palette.length} 个主色`);
});

async function uploadSTLs(files) {
  if (!files.length) return;
  if (!state.sid) await newSession();
  const fd = new FormData();
  for (const f of files) fd.append('files', f, f.name);
  toast('解析与合并中…');
  const j = await api(`/api/sessions/${state.sid}/parts`, { method: 'POST', body: fd });
  state.session = j.session;
  for (const e of j.errors) toast(e, 5000);
  await refreshSession();
  for (const p of j.added) await viewer.loadPart(state.sid, p.index, p.color);
  viewer.frameAll();
  toast(`已导入 ${j.added.length} 件`);
}

// 拖拽 + 按钮
const view = $('view');
view.addEventListener('dragover', (e) => e.preventDefault());
view.addEventListener('drop', (e) => {
  e.preventDefault();
  const stls = [...e.dataTransfer.files].filter((f) => /\.stl$/i.test(f.name));
  if (stls.length) uploadSTLs(stls);
});

// ------------------------------------------------------------ 配色与导出
$('autoassign').addEventListener('click', async () => {
  const j = await api(`/api/sessions/${state.sid}/auto`, { method: 'POST' });
  state.session = j.session;
  for (const p of state.session.parts) viewer.setPartColor(p.index, p.color);
  renderParts();
  toast(`已自动配色 ${j.assigned} 件`);
});

$('export').addEventListener('click', () => {
  window.location.href = `/api/sessions/${state.sid}/export.3mf`;
});

// 启动：?sid=xxx 恢复既有会话（服务重启后用脚本重建会话再引用），
// 否则新建空会话；加载全部件几何
const urlSid = new URLSearchParams(location.search).get('sid');
const init = urlSid ? Promise.resolve(urlSid) : newSession();
init.then((sid) => { state.sid = sid; return refreshSession(); }).then(async () => {
  $('autoassign').disabled = false;
  $('export').disabled = false;
  for (const p of state.session.parts) {
    await viewer.loadPart(state.sid, p.index, p.color);
  }
  viewer.frameAll();
  if (state.session.parts.length) toast(`已加载 ${state.session.parts.length} 件`);
}).catch((e) => toast(`初始化失败：${e.message}`));
