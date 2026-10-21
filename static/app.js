'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

const els = {
  docList: $('#docList'),
  workspacePath: $('#workspacePath'),
  backendBadge: $('#backendBadge'),
  btnRunAll: $('#btnRunAll'),
  btnRunPage: $('#btnRunPage'),
  btnRescan: $('#btnRescan'),
  fileInput: $('#fileInput'),
  btnZoomIn: $('#btnZoomIn'),
  btnZoomOut: $('#btnZoomOut'),
  btnFitWidth: $('#btnFitWidth'),
  btnFitPage: $('#btnFitPage'),
  zoomLabel: $('#zoomLabel'),
  btnSettings: $('#btnSettings'),
  btnModels: $('#btnModels'),
  modelPillName: $('#modelPillName'),
  modelDot: $('#modelDot'),
  modelDialog: $('#modelDialog'),
  modelClose: $('#modelClose'),
  modelList: $('#modelList'),
  modelDisk: $('#modelDisk'),
  modelEndpoint: $('#modelEndpoint'),
  modelDownload: $('#modelDownload'),
  mdName: $('#mdName'),
  mdPct: $('#mdPct'),
  mdBar: $('#mdBar'),
  mdBytes: $('#mdBytes'),
  mdSpeed: $('#mdSpeed'),
  mdCancel: $('#mdCancel'),
  modelNote: $('#modelNote'),
  btnPromptReset: $('#btnPromptReset'),
  promptDirty: $('#promptDirty'),
  promptState: $('#promptState'),
  presetList: $('#presetList'),
  promptHint: $('#promptHint'),
  viewImg: $('#viewImg'),
  viewTxt: $('#viewTxt'),
  pageImg: $('#pageImg'),
  pageTxt: $('#pageTxt'),
  imgEl: $('#imgEl'),
  polyImg: $('#polyImg'),
  polyTxt: $('#polyTxt'),
  boxesTxt: $('#boxesTxt'),
  emptyImg: $('#emptyImg'),
  emptyTxt: $('#emptyTxt'),
  imgDocName: $('#imgDocName'),
  txtStat: $('#txtStat'),
  progress: $('#progress'),
  progressBar: $('#progressBar'),
  inspector: $('#inspector'),
  inspId: $('#inspId'),
  inspBox: $('#inspBox'),
  inspText: $('#inspText'),
  inspGt: $('#inspGt'),
  lineEditor: $('#lineEditor'),
  leHead: $('#leHead'),
  leId: $('#leId'),
  leMeta: $('#leMeta'),
  leDirty: $('#leDirty'),
  leClose: $('#leClose'),
  leImage: $('#leImage'),
  leText: $('#leText'),
  leGt: $('#leGt'),
  leSave: $('#leSave'),
  leCancel: $('#leCancel'),
  toast: $('#toast'),
  dialog: $('#settingsDialog'),
  promptInput: $('#promptInput'),
  maxTokens: $('#maxTokens'),
  forceRun: $('#forceRun'),
  settingsClose: $('#settingsClose'),
  settingsSave: $('#settingsSave'),
  settingsReset: $('#settingsReset'),
  settingsCancel: $('#settingsCancel'),
};

const state = {
  docs: [],
  doc: null,
  linesById: new Map(),
  lineEls: new Map(),
  txtNodes: [],
  selected: null,
  fit: 'width',
  zoomFactor: 1,
  settings: { prompt: '', maxTokens: 160, force: false },
  job: null,
  pollTimer: null,
  fitToken: 0,
  backend: null,
  defaultPrompt: '',
  presets: [],
  editorLineId: null,
  models: [],
  modelsInfo: null,
  modelPoll: null,
  lastDownloadState: null,
};

const SETTINGS_KEY = 'medusa.settings.v2';

// --------------------------------------------------------------------- utils
async function api(url, opts = {}) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body && body.detail) message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    } catch (_) {}
    throw new Error(message);
  }
  return res.json();
}

let toastTimer = null;
function toast(message, isError = false) {
  clearTimeout(toastTimer);
  els.toast.textContent = message;
  els.toast.classList.toggle('err', !!isError);
  els.toast.hidden = false;
  toastTimer = setTimeout(() => { els.toast.hidden = true; }, isError ? 9000 : 4200);
}

function debounce(fn, ms) {
  let t = null;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// ------------------------------------------------------------------ settings
function loadSettings(defaultPrompt) {
  state.defaultPrompt = defaultPrompt;
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || 'null'); } catch (_) {}
  const prompt = saved && typeof saved.prompt === 'string' && saved.prompt.trim() ? saved.prompt : defaultPrompt;
  state.settings = {
    prompt,
    maxTokens: saved && Number(saved.maxTokens) > 0 ? Number(saved.maxTokens) : 160,
    force: !!(saved && saved.force),
  };
}

function persistSettings() {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(state.settings)); } catch (_) {}
}

function isDefaultPrompt(prompt = state.settings.prompt) {
  return (prompt || '').trim() === (state.defaultPrompt || '').trim();
}

function updatePromptUi() {
  const dirty = !isDefaultPrompt();
  const preset = presetFor(state.settings.prompt);
  const label = preset ? preset.label : '自定义';
  els.promptDirty.hidden = !dirty;
  els.btnPromptReset.hidden = !dirty;
  els.btnSettings.classList.toggle('dirty', dirty);
  els.btnSettings.title = dirty
    ? `当前提示词：${label}（点击编辑，或点“恢复默认”）`
    : '当前提示词：官方推荐（点击编辑）';
}

function presetFor(text) {
  const value = (text || '').trim();
  return state.presets.find((preset) => (preset.prompt || '').trim() === value) || null;
}

function renderPresets(selected) {
  els.presetList.replaceChildren();
  const value = (selected || '').trim();
  for (const preset of state.presets) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'preset' + ((preset.prompt || '').trim() === value ? ' active' : '');
    button.dataset.id = preset.id;
    button.textContent = preset.label;
    button.title = preset.hint || '';
    button.addEventListener('click', () => {
      els.promptInput.value = preset.prompt;
      syncPromptMeta();
    });
    els.presetList.appendChild(button);
  }
}

function syncPromptMeta() {
  const text = els.promptInput.value;
  const preset = presetFor(text);
  els.promptState.textContent = preset ? preset.label : '自定义';
  els.promptHint.textContent = preset
    ? preset.hint || ''
    : '自定义提示词。建议先记下当前内容，再与官方两行版本跑同一页对比。';
  for (const button of els.presetList.children) {
    const presetDef = state.presets.find((item) => item.id === button.dataset.id);
    button.classList.toggle('active', !!presetDef && (presetDef.prompt || '').trim() === text.trim());
  }
}

function openSettings() {
  els.promptInput.value = state.settings.prompt;
  els.maxTokens.value = state.settings.maxTokens;
  els.forceRun.checked = state.settings.force;
  renderPresets(state.settings.prompt);
  syncPromptMeta();
  els.dialog.showModal();
  els.promptInput.focus();
}

function saveSettings() {
  const before = state.settings.prompt;
  const prompt = els.promptInput.value.trim();
  state.settings = {
    prompt: prompt || state.defaultPrompt,
    maxTokens: clamp(parseInt(els.maxTokens.value, 10) || 160, 16, 512),
    force: els.forceRun.checked,
  };
  persistSettings();
  els.dialog.close();
  updatePromptUi();
  if (state.settings.prompt !== before) {
    toast(isDefaultPrompt() ? '已保存官方推荐提示词' : '已保存自定义提示词');
  }
}

function resetPromptToDefault(save = false) {
  els.promptInput.value = state.defaultPrompt;
  syncPromptMeta();
  if (save) {
    state.settings.prompt = state.defaultPrompt;
    persistSettings();
    updatePromptUi();
    toast('已恢复官方推荐提示词');
  }
}

// -------------------------------------------------------------- backend badge
function renderBackend(backend) {
  if (!backend) return;
  state.backend = backend;
  let cls = '';
  let label = '模型未加载';
  if (backend.backend === 'mock') {
    cls = backend.state === 'ready' ? 'mock' : 'loading';
    label = backend.state === 'ready' ? '模拟后端' : '模拟后端…';
  } else if (backend.state === 'loading') {
    cls = 'loading';
    label = '模型加载中…';
  } else if (backend.state === 'ready') {
    cls = 'ready';
    label = '模型就绪';
  } else if (backend.state === 'error') {
    cls = 'error';
    label = '模型错误';
  } else if (backend.backend === 'model') {
    cls = '';
    label = '模型待加载';
  }
  els.backendBadge.className = `backend ${cls}`;
  els.backendBadge.querySelector('span').textContent = label;
  els.backendBadge.title = [backend.detail, backend.error, backend.modelPath].filter(Boolean).join('\n');
}

async function refreshBackend() {
  try {
    renderBackend(await api('/api/backend'));
  } catch (_) {}
}

// -------------------------------------------------------------------- models
function formatBytes(bytes) {
  const value = Number(bytes) || 0;
  if (value >= 1e9) return `${(value / 1e9).toFixed(1)} GB`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(0)} MB`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(0)} KB`;
  return `${value} B`;
}

function renderModelPill() {
  const active = state.models.find((model) => model.active);
  const download = state.modelsInfo && state.modelsInfo.download;
  const busy = download && ['starting', 'downloading', 'cancelling'].includes(download.state);
  els.modelPillName.textContent = active ? `模型 · ${active.short}` : '模型';
  els.modelDot.className = `model-dot${busy ? ' warn' : ''}`;
  els.btnModels.title = active ? `当前模型：${active.label}（点击管理 / 下载）` : '管理模型';
}

function updateModelMeta(data) {
  if (!data) return;
  els.modelDisk.textContent = `磁盘剩余 ${formatBytes(data.diskFree)}`;
  els.modelEndpoint.textContent = `下载源 ${String(data.endpoint || '').replace(/^https?:\/\//, '')}`;
  els.modelNote.textContent = `模型保存在 ${data.modelsDir}。9B 约 18.8 GB，12 GB 显存下自动使用 4-bit。`;
}

function renderModelList(data) {
  if (!data) return;
  els.modelList.replaceChildren();
  const download = data.download || {};
  const busy = ['starting', 'downloading', 'cancelling'].includes(download.state);

  for (const model of data.models) {
    const card = document.createElement('div');
    card.className = `model-card${model.active ? ' active' : ''}`;

    const info = document.createElement('div');
    info.className = 'model-info';
    const title = document.createElement('div');
    title.className = 'model-title';
    const name = document.createElement('b');
    name.textContent = model.label;
    const badge = document.createElement('span');
    const downloading = busy && download.modelId === model.id;
    badge.className = 'model-badge' + (model.active ? ' active' : model.installed ? ' installed' : '');
    badge.textContent = model.active ? '使用中' : model.installed ? '已下载' : downloading ? '下载中' : '未下载';
    title.append(name, badge);

    const sub = document.createElement('div');
    sub.className = 'model-sub';
    const sizeText = model.installed ? formatBytes(model.size) : `约 ${formatBytes(model.approxBytes)}`;
    sub.textContent = `${model.repo} · ${model.params} · ${sizeText}`;

    const desc = document.createElement('p');
    desc.className = 'model-desc';
    desc.textContent = model.note;

    info.append(title, sub, desc);

    const actions = document.createElement('div');
    actions.className = 'model-actions';
    const button = document.createElement('button');
    if (model.active) {
      button.textContent = '使用中';
      button.disabled = true;
    } else if (model.installed) {
      button.textContent = '使用此模型';
      button.className = 'primary';
      button.disabled = busy;
      button.addEventListener('click', () => activateModel(model.id));
    } else if (downloading) {
      button.textContent = '下载中…';
      button.disabled = true;
    } else {
      button.textContent = '下载';
      button.disabled = busy;
      button.addEventListener('click', () => downloadModel(model.id));
    }
    actions.appendChild(button);

    card.append(info, actions);
    els.modelList.appendChild(card);
  }
}

function renderDownload(download) {
  if (!download) return;
  const busy = ['starting', 'downloading', 'cancelling'].includes(download.state);
  const failed = download.state === 'error';

  els.modelDownload.hidden = !busy && !failed;
  els.mdCancel.hidden = !busy;
  if (busy) {
    const model = state.models.find((item) => item.id === download.modelId);
    els.mdName.textContent = `下载 ${model ? model.short : ''} ${download.currentFile || ''}`.trim();
    const percent = download.total ? Math.min(100, (download.done / download.total) * 100) : 0;
    els.mdPct.textContent = download.state === 'starting' ? '准备中…' : `${percent.toFixed(1)}%`;
    els.mdBar.style.width = `${percent}%`;
    els.mdBytes.textContent = download.total
      ? `${formatBytes(download.done)} / ${formatBytes(download.total)}`
      : formatBytes(download.done);
    els.mdSpeed.textContent = download.speed ? `${formatBytes(download.speed)}/s` : '';
    els.mdCancel.disabled = download.state === 'cancelling';
    els.mdCancel.textContent = download.state === 'cancelling' ? '正在取消…' : '取消下载';
  } else if (failed) {
    els.mdName.textContent = '下载失败';
    els.mdPct.textContent = '';
    els.mdBar.style.width = '0%';
    els.mdBytes.textContent = download.error || '未知错误';
    els.mdSpeed.textContent = '';
  }

  if (state.lastDownloadState && state.lastDownloadState !== download.state) {
    if (download.state === 'done') toast('模型下载完成');
    else if (download.state === 'error') toast(`下载失败：${download.error}`, true);
    else if (download.state === 'cancelled') toast('已取消下载');
  }
  state.lastDownloadState = download.state;
}

function updateModelPolling(download) {
  const busy = download && ['starting', 'downloading', 'cancelling'].includes(download.state);
  if (busy && !state.modelPoll) {
    state.modelPoll = setInterval(() => {
      loadModels({ silent: true }).catch(() => {});
    }, 1000);
  } else if (!busy && state.modelPoll) {
    clearInterval(state.modelPoll);
    state.modelPoll = null;
  }
}

async function loadModels() {
  const data = await api('/api/models');
  state.models = data.models || [];
  state.modelsInfo = data;
  renderModelPill();
  updateModelMeta(data);
  renderModelList(data);
  renderDownload(data.download);
  updateModelPolling(data.download);
  return data;
}

function openModelDialog() {
  els.modelDialog.showModal();
  loadModels().catch((err) => toast(err.message, true));
}

async function downloadModel(id) {
  try {
    const download = await api('/api/models/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id }),
    });
    state.lastDownloadState = download.state;
    toast('开始下载模型…');
    await loadModels();
  } catch (err) {
    toast(err.message, true);
  }
}

async function cancelModelDownload() {
  try {
    await api('/api/models/download/cancel', { method: 'POST' });
    await loadModels();
  } catch (err) {
    toast(err.message, true);
  }
}

async function activateModel(id) {
  const model = state.models.find((item) => item.id === id);
  try {
    await api('/api/models/activate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id }),
    });
    toast(`已切换到 ${model ? model.label : id}，下次识别时加载权重`);
    await loadModels();
    refreshBackend();
  } catch (err) {
    toast(err.message, true);
  }
}

// ------------------------------------------------------------------- sidebar
function renderDocs() {
  els.docList.replaceChildren();
  for (const doc of state.docs) {
    const btn = document.createElement('button');
    btn.className = 'doc' + (state.doc && state.doc.id === doc.id ? ' active' : '');
    btn.dataset.id = doc.id;

    const img = document.createElement('img');
    img.src = doc.thumb;
    img.loading = 'lazy';
    img.alt = doc.name;

    const meta = document.createElement('div');
    meta.className = 'meta';
    const dot = document.createElement('span');
    dot.className = `dot ${doc.status || 'todo'}`;
    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = doc.name;
    const count = document.createElement('span');
    count.className = 'count';
    count.textContent = `${doc.done}/${doc.lines}`;
    meta.append(dot, name, count);

    btn.append(img, meta);
    btn.title = [doc.alto, `${doc.lines} 行`, doc.error].filter(Boolean).join('\n');
    btn.addEventListener('click', () => openDoc(doc.id).catch((err) => toast(err.message, true)));
    els.docList.appendChild(btn);
  }
}

async function loadDocs() {
  const data = await api('/api/docs');
  state.docs = data.docs || [];
  if (data.backend) renderBackend(data.backend);
  renderDocs();

  if (state.doc) {
    const exists = state.docs.some((d) => d.id === state.doc.id);
    if (!exists) {
      state.doc = null;
      clearDoc();
    }
  }
  if (!state.docs.length) {
    clearDoc();
    return;
  }
  if (!state.doc) await openDoc(state.docs[0].id);
}

// ------------------------------------------------------------------ document
function clearDoc() {
  state.linesById = new Map();
  state.lineEls = new Map();
  state.txtNodes = [];
  state.selected = null;
  els.polyImg.replaceChildren();
  els.polyTxt.replaceChildren();
  els.boxesTxt.replaceChildren();
  els.pageImg.classList.add('hidden');
  els.pageTxt.classList.add('hidden');
  els.emptyImg.classList.add('show');
  els.emptyTxt.classList.add('show');
  els.imgDocName.textContent = '';
  els.txtStat.textContent = '';
  showInspectorEmpty();
  refreshActiveDoc();
}

function refreshActiveDoc() {
  $$('.doc', els.docList).forEach((el) => {
    el.classList.toggle('active', !!state.doc && el.dataset.id === state.doc.id);
  });
}

const SVG_NS = 'http://www.w3.org/2000/svg';

function makeShape(line) {
  const poly = line.polygon && line.polygon.length >= 3 ? line.polygon : null;
  let shape;
  if (poly) {
    shape = document.createElementNS(SVG_NS, 'polygon');
    shape.setAttribute(
      'points',
      poly.map(([x, y]) => `${(x * 1000).toFixed(2)},${(y * 1000).toFixed(2)}`).join(' '),
    );
  } else {
    const b = line.box;
    shape = document.createElementNS(SVG_NS, 'rect');
    shape.setAttribute('x', (b.x * 1000).toFixed(2));
    shape.setAttribute('y', (b.y * 1000).toFixed(2));
    shape.setAttribute('width', (b.w * 1000).toFixed(2));
    shape.setAttribute('height', (b.h * 1000).toFixed(2));
  }
  shape.setAttribute('class', 'shape');
  shape.dataset.id = line.id;
  const title = document.createElementNS(SVG_NS, 'title');
  title.textContent = line.id + (line.gt ? `\nGT: ${line.gt}` : '');
  shape.appendChild(title);
  return shape;
}

function buildBoxes(doc) {
  els.polyImg.replaceChildren();
  els.polyTxt.replaceChildren();
  els.boxesTxt.replaceChildren();
  state.linesById = new Map();
  state.lineEls = new Map();
  state.txtNodes = [];

  const fragLeft = document.createDocumentFragment();
  const fragRight = document.createDocumentFragment();
  const fragText = document.createDocumentFragment();

  for (const line of doc.lines) {
    state.linesById.set(line.id, line);
    const hasPoly = !!(line.polygon && line.polygon.length >= 3);
    const geom = `left:${line.box.x * 100}%;top:${line.box.y * 100}%;width:${line.box.w * 100}%;height:${line.box.h * 100}%`;

    const left = makeShape(line);
    const right = makeShape(line);

    const box = document.createElement('div');
    box.className = 'box textbox' + (line.text ? '' : ' pending') + (hasPoly ? ' has-poly' : '');
    box.style.cssText = geom;
    box.dataset.id = line.id;
    box.title = line.text || '（尚未识别）';

    const frame = document.createElement('div');
    frame.className = 'txt';
    const txt = document.createElement('span');
    txt.className = 'txt-inner';
    txt.dataset.text = line.text || '';
    txt.dataset.key = '';
    txt.textContent = line.text || '';
    frame.appendChild(txt);
    box.appendChild(frame);

    const hover = (on) => {
      left.classList.toggle('hover', on);
      right.classList.toggle('hover', on);
      box.classList.toggle('hover', on);
    };
    for (const el of [left, right, box]) {
      el.addEventListener('mouseenter', () => hover(true));
      el.addEventListener('mouseleave', () => hover(false));
      el.addEventListener('click', () => selectLine(line.id));
      el.addEventListener('dblclick', (event) => {
        event.preventDefault();
        event.stopPropagation();
        openLineEditor(line, event);
      });
    }

    state.lineEls.set(line.id, { left, right, box, txt });
    state.txtNodes.push(txt);
    fragLeft.appendChild(left);
    fragRight.appendChild(right);
    fragText.appendChild(box);
  }

  els.polyImg.appendChild(fragLeft);
  els.polyTxt.appendChild(fragRight);
  els.boxesTxt.appendChild(fragText);
}

function renderDoc(doc) {
  els.emptyImg.classList.remove('show');
  els.emptyTxt.classList.remove('show');
  els.pageImg.classList.remove('hidden');
  els.pageTxt.classList.remove('hidden');
  els.imgEl.src = doc.imageUrl;
  els.imgDocName.textContent = `${doc.name} · ${doc.lines.length} 行 · ${doc.unit}`;
  els.txtStat.textContent = `${doc.done}/${doc.lines.length}`;
  buildBoxes(doc);
  layout();
  requestAnimationFrame(() => fitAllText());
}

async function openDoc(docId, keepView = false) {
  closeLineEditor();
  const scrollTop = keepView ? els.viewImg.scrollTop : 0;
  const scrollLeft = keepView ? els.viewImg.scrollLeft : 0;
  const doc = await api(`/api/docs/${docId}`);
  state.doc = doc;
  if (!keepView) {
    state.selected = null;
    showInspectorEmpty();
  }
  renderDoc(doc);
  refreshActiveDoc();
  scrollBoth(scrollTop, scrollLeft);
  if (state.selected && state.linesById.has(state.selected)) {
    selectLine(state.selected, false);
  } else if (!state.selected) {
    showInspectorEmpty();
  } else {
    state.selected = null;
    showInspectorEmpty();
  }
}

// --------------------------------------------------------------------- layout
function layout() {
  const doc = state.doc;
  if (!doc) return;
  const pad = 36;
  const vw = Math.max(120, els.viewImg.clientWidth - pad);
  const vh = Math.max(120, els.viewImg.clientHeight - pad);
  const base = state.fit === 'page'
    ? Math.min(vw / doc.width, vh / doc.height)
    : vw / doc.width;
  const scale = clamp(base * state.zoomFactor, 0.01, 8);
  const w = Math.max(40, Math.round(doc.width * scale));
  const h = Math.max(40, Math.round(doc.height * scale));

  for (const page of [els.pageImg, els.pageTxt]) {
    page.style.width = `${w}px`;
    page.style.height = `${h}px`;
  }
  els.zoomLabel.textContent = `${Math.round(state.zoomFactor * 100)}%`;
  els.btnFitWidth.classList.toggle('on', state.fit === 'width' && state.zoomFactor === 1);
  els.btnFitPage.classList.toggle('on', state.fit === 'page' && state.zoomFactor === 1);
  fitAllText();
}

function fitAllText() {
  const token = ++state.fitToken;
  const nodes = state.txtNodes;
  for (const node of nodes) {
    node.style.transform = 'none';
    node.dataset.key = '';
  }
  let index = 0;
  const step = () => {
    if (token !== state.fitToken) return;
    const end = Math.min(nodes.length, index + 140);
    for (; index < end; index += 1) fitText(nodes[index]);
    if (index < nodes.length) requestAnimationFrame(step);
  };
  step();
}

function fitText(node) {
  const box = node.parentElement;
  if (!box) return;
  const width = box.clientWidth;
  const height = box.clientHeight;
  if (width < 3 || height < 3) return;

  // Keep the transcription on one line: collapse newlines / repeated spaces.
  const raw = node.dataset.text || '';
  const display = raw.replace(/\s+/g, ' ').trim();
  if (node.textContent !== display) node.textContent = display;

  const key = `${width}x${height}|${display}`;
  if (node.dataset.key === key) return;
  node.dataset.key = key;
  node.style.transform = 'none';
  node.style.transformOrigin = 'top left';

  if (!display) {
    node.style.fontSize = '6px';
    return;
  }

  // Fill the box height, then compress horizontally instead of wrapping.
  let fontSize = clamp(height * 0.9, 2.5, 72);
  node.style.fontSize = `${fontSize}px`;
  let natural = node.scrollWidth;
  const usable = Math.max(2, width - 3);

  // Beyond ~3.3x compression, shrink the font first so it stays legible.
  const MIN_SQUASH = 0.3;
  let squash = natural > usable ? usable / natural : 1;
  if (squash < MIN_SQUASH) {
    fontSize = Math.max(2, fontSize * (squash / MIN_SQUASH));
    node.style.fontSize = `${fontSize}px`;
    natural = node.scrollWidth;
    squash = natural > usable ? usable / natural : 1;
  }

  if (squash < 0.999) {
    node.style.transform = `scaleX(${squash.toFixed(4)})`;
  }
}

// ---------------------------------------------------------------- interaction
function showInspectorEmpty() {
  els.inspId.textContent = '未选中行';
  els.inspBox.textContent = '点击任意行查看完整转写';
  els.inspText.classList.add('placeholder');
  els.inspText.textContent = '选中一行后，这里显示完整转写；双击行可放大编辑。';
  els.inspGt.textContent = '';
}

function selectLine(lineId, scroll = true) {
  if (!state.linesById.has(lineId)) return;
  state.selected = lineId;
  for (const [id, refs] of state.lineEls) {
    const active = id === lineId;
    refs.left.classList.toggle('active', active);
    refs.right.classList.toggle('active', active);
    refs.box.classList.toggle('active', active);
  }
  const line = state.linesById.get(lineId);
  els.inspector.hidden = false;
  document.body.classList.add('inspector-open');
  els.inspId.textContent = line.id;
  els.inspBox.textContent = `${Math.round(line.px.x)}, ${Math.round(line.px.y)} · ${Math.round(line.px.w)}×${Math.round(line.px.h)} px` + (line.ms ? ` · ${line.ms} ms` : '');
  els.inspText.classList.remove('placeholder');
  els.inspText.textContent = line.text || '（尚未识别）';
  els.inspGt.textContent = line.gt ? `GT: ${line.gt}` : '';
  if (scroll) {
    const refs = state.lineEls.get(lineId);
    if (refs) refs.box.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }
}

function closeInspector() {
  state.selected = null;
  for (const refs of state.lineEls.values()) {
    refs.left.classList.remove('active');
    refs.right.classList.remove('active');
    refs.box.classList.remove('active');
  }
  showInspectorEmpty();
}

// --------------------------------------------------------------- line editor
function placeLineEditor(clientX, clientY) {
  const editor = els.lineEditor;
  const width = editor.offsetWidth || 520;
  const height = editor.offsetHeight || 360;
  let left = clientX + 26;
  let top = clientY + 18;
  if (left + width > innerWidth - 12) left = clientX - width - 26;
  left = clamp(left, 12, Math.max(12, innerWidth - width - 12));
  top = clamp(top, 60, Math.max(60, innerHeight - height - 12));
  editor.style.left = `${left}px`;
  editor.style.top = `${top}px`;
}

function openLineEditor(line, event) {
  if (!state.doc) return;
  if (state.selected !== line.id) selectLine(line.id, false);
  state.editorLineId = line.id;
  els.leId.textContent = line.id;
  els.leMeta.textContent = `${Math.round(line.px.w)}×${Math.round(line.px.h)} px`;
  els.leDirty.hidden = true;
  els.leImage.src = `/api/docs/${state.doc.id}/lines/${encodeURIComponent(line.id)}/crop?t=${Date.now()}`;
  els.leImage.alt = line.id;
  els.leText.value = line.text || '';
  els.leText.dataset.original = els.leText.value;
  els.leGt.textContent = line.gt ? `GT: ${line.gt}` : '';
  els.leGt.title = line.gt || '';
  els.lineEditor.hidden = false;
  placeLineEditor(event.clientX, event.clientY);
  els.leText.focus();
  const end = els.leText.value.length;
  els.leText.setSelectionRange(end, end);
}

function closeLineEditor() {
  state.editorLineId = null;
  els.lineEditor.hidden = true;
  els.leDirty.hidden = true;
}

async function saveLineEditor() {
  if (!state.doc || !state.editorLineId) return;
  const lineId = state.editorLineId;
  const text = els.leText.value;
  els.leSave.disabled = true;
  try {
    const result = await api(`/api/docs/${state.doc.id}/lines/${encodeURIComponent(lineId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    applyUpdates({ [lineId]: text });
    const line = state.linesById.get(lineId);
    if (line) line.text = text;
    if (state.doc && typeof result.done === 'number') {
      state.doc.done = result.done;
      els.txtStat.textContent = `${result.done}/${state.doc.lines.length}`;
      const row = els.docList.querySelector(`.doc[data-id="${state.doc.id}"]`);
      if (row) {
        row.querySelector('.count').textContent = `${result.done}/${state.doc.lines.length}`;
        row.querySelector('.dot').className = `dot ${result.status}`;
      }
      const entry = state.docs.find((item) => item.id === state.doc.id);
      if (entry) {
        entry.done = result.done;
        entry.status = result.status;
      }
    }
    toast('已保存修改');
    closeLineEditor();
  } catch (err) {
    toast(err.message, true);
  } finally {
    els.leSave.disabled = false;
  }
}

function bindEditorDrag() {
  let drag = null;
  els.leHead.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || event.target.closest('button')) return;
    const rect = els.lineEditor.getBoundingClientRect();
    drag = { id: event.pointerId, x: event.clientX, y: event.clientY, left: rect.left, top: rect.top };
    try { els.leHead.setPointerCapture(event.pointerId); } catch (_) {}
    event.preventDefault();
  });
  els.leHead.addEventListener('pointermove', (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    const maxLeft = Math.max(8, innerWidth - els.lineEditor.offsetWidth - 8);
    const maxTop = Math.max(8, innerHeight - els.lineEditor.offsetHeight - 8);
    const left = clamp(drag.left + event.clientX - drag.x, 8, maxLeft);
    const top = clamp(drag.top + event.clientY - drag.y, 8, maxTop);
    els.lineEditor.style.left = `${left}px`;
    els.lineEditor.style.top = `${top}px`;
  });
  const end = (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    drag = null;
    try { els.leHead.releasePointerCapture(event.pointerId); } catch (_) {}
  };
  els.leHead.addEventListener('pointerup', end);
  els.leHead.addEventListener('pointercancel', end);
}

function applyUpdates(updates) {
  for (const [lineId, text] of Object.entries(updates)) {
    const line = state.linesById.get(lineId);
    const refs = state.lineEls.get(lineId);
    if (!line || !refs) continue;
    line.text = text;
    refs.txt.dataset.text = text;
    refs.txt.dataset.key = '';
    refs.txt.textContent = text;
    refs.box.classList.toggle('pending', !text);
    refs.box.title = text || '（尚未识别）';
    fitText(refs.txt);
    if (state.selected === lineId) {
      els.inspText.textContent = text || '（尚未识别）';
      els.inspBox.textContent = `${Math.round(line.px.x)}, ${Math.round(line.px.y)} · ${Math.round(line.px.w)}×${Math.round(line.px.h)} px` + (line.ms ? ` · ${line.ms} ms` : '');
    }
  }
}

// --------------------------------------------------------------------- jobs
function setBusy(busy) {
  els.btnRunPage.disabled = busy;
  els.btnRunAll.disabled = busy;
  document.body.classList.toggle('busy', busy);
}

function progressShow(job) {
  const active = job && (job.status === 'queued' || job.status === 'running');
  els.progress.classList.toggle('show', !!active);
  if (!active) {
    els.progressBar.style.width = '0%';
    return;
  }
  const ratio = job.total ? job.done / job.total : 0;
  els.progressBar.style.width = `${Math.round(ratio * 100)}%`;
  els.txtStat.textContent = `${job.done}/${job.total}`;
  els.txtStat.title = job.message || '';
}

async function startJob(docIds) {
  if (state.job && (state.job.status === 'queued' || state.job.status === 'running')) {
    toast('已有任务正在运行');
    return;
  }
  const body = {
    docIds,
    prompt: state.settings.prompt,
    maxNewTokens: state.settings.maxTokens,
    force: state.settings.force,
  };
  try {
    const job = await api('/api/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    state.job = job;
    setBusy(true);
    progressShow(job);
    pollJob(job.id);
  } catch (err) {
    toast(err.message, true);
  }
}

async function pollJob(jobId) {
  clearTimeout(state.pollTimer);
  let job;
  try {
    job = await api(`/api/jobs/${jobId}`);
  } catch (err) {
    toast(err.message, true);
    setBusy(false);
    return;
  }
  state.job = job;
  progressShow(job);

  for (const [docId, updates] of Object.entries(job.updates || {})) {
    if (state.doc && docId === state.doc.id) applyUpdates(updates);
  }

  if (job.status === 'done' || job.status === 'error' || job.status === 'cancelled') {
    setBusy(false);
    els.progress.classList.remove('show');
    if (job.status === 'done') toast(`识别完成：${job.done} 行`);
    else if (job.status === 'error') toast(job.error || '识别失败', true);
    else toast('任务已取消');

    try { await loadDocs(); } catch (_) {}
    if (state.doc) {
      try { await openDoc(state.doc.id, true); } catch (_) {}
    }
    return;
  }
  state.pollTimer = setTimeout(() => pollJob(jobId), 900);
}

// ---------------------------------------------------------------- zoom / sync
function cancelPendingZoom() {
  if (zoomRaf) cancelAnimationFrame(zoomRaf);
  zoomRaf = 0;
  pendingZoom = null;
}

function setFit(mode) {
  cancelPendingZoom();
  state.fit = mode;
  state.zoomFactor = 1;
  layout();
  scrollBoth(0, 0);
}

function scrollBoth(top, left) {
  els.viewImg.scrollTop = top;
  els.viewTxt.scrollTop = top;
  els.viewImg.scrollLeft = left;
  els.viewTxt.scrollLeft = left;
}

function applyZoomAt(clientX, clientY, factor) {
  if (!state.doc) return;
  const rect = els.viewTxt.getBoundingClientRect();
  const inText = clientX >= rect.left && clientX <= rect.right && clientY >= rect.top && clientY <= rect.bottom;
  const viewport = inText ? els.viewTxt : els.viewImg;
  const page = inText ? els.pageTxt : els.pageImg;
  const before = page.getBoundingClientRect();
  if (before.width < 2 || before.height < 2) return;

  const fx = clamp((clientX - before.left) / before.width, 0, 1);
  const fy = clamp((clientY - before.top) / before.height, 0, 1);

  const next = clamp(state.zoomFactor * factor, 0.4, 12);
  if (Math.abs(next - state.zoomFactor) < 0.0005) return;
  state.zoomFactor = next;
  layout();

  const after = page.getBoundingClientRect();
  const view = viewport.getBoundingClientRect();
  const left = after.left - view.left + viewport.scrollLeft + fx * after.width - (clientX - view.left);
  const top = after.top - view.top + viewport.scrollTop + fy * after.height - (clientY - view.top);
  scrollBoth(top, left);
}

let zoomRaf = 0;
let pendingZoom = null;

function queueZoom(clientX, clientY, factor) {
  if (!pendingZoom) pendingZoom = { x: clientX, y: clientY, factor: 1 };
  pendingZoom.factor *= factor;
  pendingZoom.x = clientX;
  pendingZoom.y = clientY;
  if (zoomRaf) return;
  zoomRaf = requestAnimationFrame(() => {
    zoomRaf = 0;
    const request = pendingZoom;
    pendingZoom = null;
    if (request) applyZoomAt(request.x, request.y, request.factor);
  });
}

function nudgeZoom(factor) {
  cancelPendingZoom();
  const rect = els.viewImg.getBoundingClientRect();
  applyZoomAt(rect.left + rect.width / 2, rect.top + rect.height / 2, factor);
}

function bindWheel(viewport) {
  viewport.addEventListener(
    'wheel',
    (event) => {
      if (!state.doc) return;
      if (event.shiftKey) return; // keep shift+wheel for native scrolling
      event.preventDefault();
      let delta = event.deltaY;
      if (event.deltaMode === 1) delta *= 16;
      else if (event.deltaMode === 2) delta *= viewport.clientHeight;
      const factor = clamp(Math.pow(1.0015, -delta), 0.55, 1.8);
      queueZoom(event.clientX, event.clientY, factor);
    },
    { passive: false },
  );
}

let suppressClickUntil = 0;

function bindPan(viewport) {
  let pan = null;

  viewport.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || !state.doc) return;
    pan = {
      id: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      top: viewport.scrollTop,
      left: viewport.scrollLeft,
      moved: false,
      captured: false,
    };
  });

  viewport.addEventListener('pointermove', (event) => {
    if (!pan || event.pointerId !== pan.id) return;
    const dx = event.clientX - pan.x;
    const dy = event.clientY - pan.y;
    if (!pan.moved && Math.hypot(dx, dy) < 4) return;
    if (!pan.moved) {
      pan.moved = true;
      // Capture only once the gesture is really a drag: capturing on
      // pointerdown would retarget the following click to the viewport and
      // swallow line selection.
      viewport.classList.add('panning');
      try {
        viewport.setPointerCapture(event.pointerId);
        pan.captured = true;
      } catch (_) {}
    }
    event.preventDefault();
    scrollBoth(pan.top - dy, pan.left - dx);
  });

  const finish = (event) => {
    if (!pan || event.pointerId !== pan.id) return;
    const moved = pan.moved;
    const captured = pan.captured;
    pan = null;
    viewport.classList.remove('panning');
    if (captured) {
      try { viewport.releasePointerCapture(event.pointerId); } catch (_) {}
    }
    if (moved) suppressClickUntil = performance.now() + 250;
  };

  viewport.addEventListener('pointerup', finish);
  viewport.addEventListener('pointercancel', finish);
  viewport.addEventListener('dragstart', (event) => event.preventDefault());
}

function syncScroll(source, target) {
  let locked = false;
  source.addEventListener('scroll', () => {
    if (locked) return;
    locked = true;
    target.scrollTop = source.scrollTop;
    target.scrollLeft = source.scrollLeft;
    requestAnimationFrame(() => { locked = false; });
  });
}

// --------------------------------------------------------------------- wiring
function wire() {
  els.btnZoomIn.addEventListener('click', () => nudgeZoom(1.18));
  els.btnZoomOut.addEventListener('click', () => nudgeZoom(1 / 1.18));
  els.btnFitWidth.addEventListener('click', () => setFit('width'));
  els.btnFitPage.addEventListener('click', () => setFit('page'));

  els.btnRunPage.addEventListener('click', () => {
    if (!state.doc) return toast('先选择一个页面', true);
    startJob([state.doc.id]);
  });
  els.btnRunAll.addEventListener('click', () => {
    if (!state.docs.length) return toast('工作区里没有页面', true);
    startJob(state.docs.map((d) => d.id));
  });
  els.btnRescan.addEventListener('click', async () => {
    try {
      await api('/api/rescan', { method: 'POST' });
      await loadDocs();
      toast(`工作区：${state.docs.length} 个页面`);
    } catch (err) {
      toast(err.message, true);
    }
  });

  els.fileInput.addEventListener('change', async (event) => {
    const files = Array.from(event.target.files || []);
    if (!files.length) return;
    const form = new FormData();
    for (const file of files) form.append('files', file, file.name);
    toast(`正在上传 ${files.length} 个文件…`);
    try {
      const result = await api('/api/upload', { method: 'POST', body: form });
      toast(`已保存 ${result.saved.length} 个文件到 ${result.folder}`);
      await loadDocs();
    } catch (err) {
      toast(err.message, true);
    } finally {
      event.target.value = '';
    }
  });

  els.btnSettings.addEventListener('click', openSettings);
  els.btnModels.addEventListener('click', openModelDialog);
  els.modelClose.addEventListener('click', () => els.modelDialog.close());
  els.mdCancel.addEventListener('click', cancelModelDownload);
  els.modelDialog.addEventListener('click', (event) => {
    if (event.target === els.modelDialog) els.modelDialog.close();
  });
  els.settingsSave.addEventListener('click', saveSettings);
  els.settingsClose.addEventListener('click', () => els.dialog.close());
  els.settingsCancel.addEventListener('click', () => els.dialog.close());
  els.settingsReset.addEventListener('click', () => resetPromptToDefault(false));
  els.promptInput.addEventListener('input', syncPromptMeta);
  els.btnPromptReset.addEventListener('click', () => resetPromptToDefault(true));
  els.dialog.addEventListener('click', (event) => {
    if (event.target === els.dialog) els.dialog.close();
  });

  els.leClose.addEventListener('click', closeLineEditor);
  els.leCancel.addEventListener('click', closeLineEditor);
  els.leSave.addEventListener('click', saveLineEditor);
  els.leText.addEventListener('input', () => {
    els.leDirty.hidden = els.leText.value === els.leText.dataset.original;
  });
  els.leText.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
      event.preventDefault();
      saveLineEditor();
    }
  });
  bindEditorDrag();

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      if (els.dialog.open || els.modelDialog.open) return;
      if (!els.lineEditor.hidden) {
        closeLineEditor();
        return;
      }
      if (!els.inspector.hidden) closeInspector();
    }
  });

  els.zoomLabel.addEventListener('click', () => {
    state.zoomFactor = 1;
    layout();
  });

  bindWheel(els.viewImg);
  bindWheel(els.viewTxt);
  bindPan(els.viewImg);
  bindPan(els.viewTxt);

  document.addEventListener('click', (event) => {
    if (performance.now() < suppressClickUntil) {
      suppressClickUntil = 0;
      event.stopPropagation();
      event.preventDefault();
    }
  }, true);

  syncScroll(els.viewImg, els.viewTxt);
  syncScroll(els.viewTxt, els.viewImg);
  window.addEventListener('resize', debounce(() => layout(), 140));
  els.imgEl.addEventListener('load', () => fitAllText());
}

// ----------------------------------------------------------------------- boot
async function boot() {
  wire();
  document.body.classList.add('inspector-open');
  showInspectorEmpty();
  try {
    const config = await api('/api/config');
    els.workspacePath.textContent = config.workspace;
    els.workspacePath.title = config.workspace;
    state.presets = config.promptPresets || [];
    loadSettings(config.defaultPrompt);
    updatePromptUi();
    renderBackend(config.backend);
    await loadDocs();
    loadModels().catch(() => {});
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(() => fitAllText()).catch(() => {});
    }
  } catch (err) {
    toast(`无法连接服务：${err.message}`, true);
  }
  refreshBackend();
  setInterval(refreshBackend, 5000);
}

boot();
