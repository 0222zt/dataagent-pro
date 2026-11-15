/* DataAgent Pro 前端：三栏图表工作台，原生 JS，无构建步骤。
 *
 * 两条出图路径：
 *   1. 拖字段 → /api/chart/spec → 后端确定性编译 → 沙箱执行（不调用模型）
 *   2. 自然语言 → /api/chat/stream → 模型生成 + 自愈
 *
 * 另外保留：上传与画像、Plotly 主题适配、四种导出、历史抽屉、Toast。
 */
'use strict';

const MAX_FILE_MB = 100;

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const CHANNEL_META = {
  x: { tag: 'X', placeholder: '拖入字段', hint: '分类 / 时间' },
  y: { tag: 'Y', placeholder: '拖入字段', hint: '数值指标' },
  color: { tag: '色', placeholder: '可选', hint: '按分类拆分' },
};

/* 统一的线性图标：一处定义，全站 1.5 描边，替代原先散落的 emoji */
const ICON_PATHS = {
  chart: '<path d="M3 3v18h18"/><rect x="7" y="12" width="3" height="6"/><rect x="12" y="8" width="3" height="10"/><rect x="17" y="5" width="3" height="13"/>',
  fields: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M9 9v11"/>',
  alert: '<path d="M12 3 2 20h20L12 3Z"/><path d="M12 9v5M12 17.5v.01"/>',
  moon: '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5Z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  close: '<path d="M6 6l12 12M18 6 6 18"/>',
};

function icon(name, size = 16) {
  const body = ICON_PATHS[name] || ICON_PATHS.chart;
  return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
}

/* 空态里的产品视图：一张用代码画的小柱图，只有一根柱落在重点色上 */
const EMPTY_GLYPH = `<span class="empty-glyph" aria-hidden="true"><svg viewBox="0 0 96 64" width="96" height="64" fill="none"><path d="M8 6v46h80" stroke="currentColor" stroke-opacity="0.28" stroke-width="1.5" stroke-linecap="round"/><rect x="18" y="34" width="12" height="18" fill="currentColor" fill-opacity="0.14"/><rect x="36" y="26" width="12" height="26" fill="currentColor" fill-opacity="0.14"/><rect x="54" y="30" width="12" height="22" fill="currentColor" fill-opacity="0.14"/><rect x="72" y="12" width="12" height="40" fill="var(--accent)"/></svg></span>`;

const CHART_LABELS = {
  bar: '柱状图',
  line: '折线图',
  area: '面积图',
  scatter: '散点图',
  pie: '饼图',
  box: '箱线图',
};

const AGG_LABELS = {
  none: '不聚合',
  sum: '总和',
  mean: '平均值',
  count: '计数',
  max: '最大值',
  min: '最小值',
};

const state = {
  dataset: null,
  sessionId: null,
  chartJson: '',
  chartTitle: 'DataAgent 图表',
  lastPrompt: '',
  code: '',
  busy: false,
  theme: 'light',
  spec: defaultSpec(),
  recommendations: [],
  applyTimer: null,
  specDirty: false,
  lastResult: null,
  lastResultSource: 'spec',
  isDemo: false,
  maxFileMb: MAX_FILE_MB,
};

function defaultSpec() {
  return {
    chart_type: 'bar',
    x: '',
    y: '',
    color: '',
    aggregation: 'sum',
    sort: 'none',
    top_n: 0,
  };
}

/* ------------------------------------------------------------------ */
/* 通用工具                                                             */
/* ------------------------------------------------------------------ */
function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[ch]));
}

function fmtNum(value) {
  const num = Number(value);
  return Number.isFinite(num) ? num.toLocaleString('zh-CN') : String(value ?? '');
}

function fmtTime(seconds) {
  const ts = Number(seconds);
  if (!Number.isFinite(ts) || ts <= 0) return '';
  const date = new Date(ts * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function shortModel(model) {
  const text = String(model || '').trim();
  if (!text) return '未知模型';
  if (text === 'rules') return '规则编译';
  return text.includes('/') ? text.split('/').pop() : text;
}

function formatCell(value) {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

let toastTimer = null;
function toast(message, kind = 'info') {
  const el = $('#toast');
  el.textContent = message;
  el.classList.add('show');
  el.dataset.kind = kind;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), kind === 'error' ? 5200 : 2800);
}

function makeError(payload) {
  const err = new Error(payload?.message || '请求失败');
  err.code = payload?.code || '';
  err.hint = payload?.hint || '';
  err.requestId = payload?.request_id || '';
  return err;
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  if (!response.ok) {
    throw makeError(data || { message: `请求失败（HTTP ${response.status}）` });
  }
  return data;
}

/* ------------------------------------------------------------------ */
/* 主题                                                                */
/* ------------------------------------------------------------------ */
function applyTheme(theme) {
  state.theme = theme === 'dark' ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', state.theme);
  localStorage.setItem('dataagent-theme', state.theme);
  $('#themeBtn').innerHTML = icon(state.theme === 'dark' ? 'sun' : 'moon', 16);
  $('#themeBtn').title = state.theme === 'dark' ? '切换到亮色主题' : '切换到暗色主题';
}

async function toggleTheme() {
  applyTheme(state.theme === 'dark' ? 'light' : 'dark');
  if (state.chartJson) {
    await renderChart(state.chartJson).catch(() => {});
  }
}

/* ------------------------------------------------------------------ */
/* 健康检查                                                             */
/* ------------------------------------------------------------------ */
async function loadHealth() {
  const dot = $('#healthDot');
  const text = $('#healthText');
  try {
    const health = await api('/api/health');
    dot.className = health.api_key_configured ? 'ok' : 'warn';
    text.textContent = health.api_key_configured ? '就绪' : '待填 Key';
    $('#health').title = health.api_key_configured
      ? `服务正常 · ${health.provider} · 已调用 ${health.usage?.calls ?? 0} 次模型`
      : '还没有配置 API Key：打开 .env，填写 DATAAGENT_API_KEY 后重启服务';
    $('#modelPill').textContent = health.model || '未选择模型';
    $('#modelPill').title = [
      `模型链：${(health.model_chain || []).join(' → ')}`,
      `调用：${health.usage?.calls ?? 0} 次，失败 ${health.usage?.failures ?? 0} 次`,
      `会话：${health.history?.sessions ?? 0}，图表：${health.history?.charts ?? 0}`,
    ].join('\n');
    if (Number.isFinite(Number(health.max_file_mb)) && Number(health.max_file_mb) > 0) {
      state.maxFileMb = Number(health.max_file_mb);
      $('#fileSizeHint').textContent = String(state.maxFileMb);
    }
  } catch (err) {
    dot.className = 'bad';
    text.textContent = '服务离线';
    $('#health').title = err.message || '无法连接后端服务';
  }
}

let healthTimer = null;
function refreshHealthSoon() {
  clearTimeout(healthTimer);
  healthTimer = setTimeout(loadHealth, 1200);
}

/* ------------------------------------------------------------------ */
/* 数据集画像                                                           */
/* ------------------------------------------------------------------ */
function metaColumns() {
  return (state.dataset?.meta?.columns || []).slice();
}

function columnInfo(name) {
  return metaColumns().find((column) => column.name === name) || null;
}

function kindOf(name) {
  return String(columnInfo(name)?.kind || 'text');
}

function uniqueOf(name) {
  return Number(columnInfo(name)?.unique || 0);
}

function renderSummary(dataset) {
  const meta = dataset.meta || {};
  $('#summary').textContent = dataset.summary || '数据已解析，可以开始作图';
  const quality = meta.quality_score;
  if (quality != null) {
    $('#summary').textContent += ` · 质量评分 ${Math.round(quality * 100)} / 100`;
  }
}

function renderChips(meta) {
  meta = meta || {};
  const chips = [];
  if (meta.shape) {
    chips.push(`<span class="chip blue">${fmtNum(meta.shape.rows)} 行 × ${meta.shape.columns} 列</span>`);
  }
  if (meta.numeric_columns && meta.numeric_columns.length) {
    chips.push(`<span class="chip green">${meta.numeric_columns.length} 个数值列</span>`);
  }
  if (meta.missing_cells) {
    chips.push(`<span class="chip amber">${fmtNum(meta.missing_cells)} 个缺失值</span>`);
  }
  if (meta.duplicated_rows) {
    chips.push(`<span class="chip amber">${fmtNum(meta.duplicated_rows)} 行重复</span>`);
  }
  const quality = meta.quality_score;
  if (quality != null) {
    chips.push(
      `<span class="chip ${quality >= 0.85 ? 'green' : 'amber'}">质量 ${Math.round(quality * 100)} 分</span>`
    );
  }
  const range = meta.time_range;
  if (range && range.start && range.end) {
    chips.push(
      `<span class="chip blue">${esc(String(range.start).slice(0, 10))} → ${esc(String(range.end).slice(0, 10))}</span>`
    );
  }
  $('#chips').innerHTML = chips.join('');
}

function renderPreview(dataset) {
  const meta = dataset.meta || {};
  const rows = dataset.preview || [];
  const columns = (meta.columns || []).map((column) => column.name);
  const keys = columns.length ? columns : Object.keys(rows[0] || {});
  if (!keys.length || !rows.length) {
    $('#tableWrap').classList.add('hidden');
    return;
  }
  const head = keys.map((key) => `<th>${esc(key)}</th>`).join('');
  const body = rows
    .map(
      (row) =>
        `<tr>${keys
          .map((key) => {
            const text = formatCell(row[key]);
            return `<td title="${esc(text)}">${esc(text)}</td>`;
          })
          .join('')}</tr>`
    )
    .join('');
  $('#previewTable').innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body}</tbody>`;
  $('#tableWrap').classList.remove('hidden');
}

function renderFields() {
  const columns = metaColumns();
  $('#fieldCount').textContent = String(columns.length);
  const list = $('#fieldList');
  if (!columns.length) {
    list.innerHTML = '<p class="hint">还没有字段，上传数据后这里会列出所有列</p>';
    return;
  }
  list.innerHTML = columns
    .map((column) => {
      const kind = String(column.kind || 'text');
      const tag =
        kind === 'numeric' ? '123' : kind === 'datetime' ? '时' : kind === 'boolean' ? '是/否' : '文';
      const meta = [
        kind === 'numeric' ? '数值列' : kind === 'datetime' ? '时间列' : kind === 'boolean' ? '布尔列' : '文本列',
        `${column.unique ?? '?'} 个不同值`,
        column.nulls ? `${column.nulls} 个缺失` : '无缺失',
      ].join(' · ');
      return `<div class="field" draggable="true" data-field="${esc(column.name)}" data-kind="${kind}" title="${esc(meta)}">
        <span class="field-kind ${esc(kind)}">${esc(tag)}</span>
        <span class="field-name">${esc(column.name)}</span>
        <span class="field-actions">
          <button type="button" data-assign="x" title="分配到 X 轴">X</button>
          <button type="button" data-assign="y" title="分配到 Y 轴">Y</button>
          <button type="button" data-assign="color" title="分配到颜色">色</button>
        </span>
      </div>`;
    })
    .join('');
}

function renderRecommendations() {
  const box = $('#recList');
  const items = state.recommendations || [];
  $('#recCount').textContent = String(items.length);
  if (!items.length) {
    box.innerHTML = '<p class="hint">上传数据后自动推荐</p>';
    return;
  }
  box.innerHTML = items
    .map(
      (item, index) =>
        `<button class="rec" type="button" data-rec="${index}">
          <b>${esc(item.label || '推荐图表')}</b>
          <span>${esc(item.reason || '')}</span>
        </button>`
    )
    .join('');
}

function applyDataset(dataset) {
  state.dataset = dataset;
  state.sessionId = null;
  state.chartJson = '';
  state.lastPrompt = '';
  state.code = '';
  state.spec = defaultSpec();
  state.recommendations = [];
  state.lastResult = null;
  state.isDemo = Boolean(dataset.demo);
  $('#demoBar').classList.toggle('hidden', !state.isDemo);

  $('#datasetBadge').textContent = `${fmtNum(dataset.rows)} × ${dataset.columns} 列`;
  $('#datasetBadge').className = 'badge ok';
  $('#datasetPill').textContent = dataset.filename || '已加载数据';
  $('#datasetPill').title = [
    dataset.filename || '',
    `${fmtNum(dataset.rows)} 行 × ${dataset.columns} 列`,
    dataset.summary || '',
  ].join('\n');
  $('#fileName').textContent = dataset.filename || '';
  renderSummary(dataset);
  renderChips(dataset.meta);
  renderPreview(dataset);
  renderFields();
  renderRecommendations();

  if (window.Plotly) {
    try {
      Plotly.purge('chart');
    } catch {
      /* 图表尚未创建时忽略 */
    }
  }
  $('#resultBadges').innerHTML = '';
  $('#errorBox').classList.add('hidden');
  $('#codePanel').classList.add('hidden');
  $('#sessionBadge').textContent = '新会话';
  $('#sessionBadge').className = 'badge';
  setExportEnabled(false);
  showEmpty('chart', '数据已就绪', '点一个推荐图表，或把字段拖到右侧通道槽');
  $('#footnote').textContent = state.isDemo
    ? '示例数据已就绪：拖字段或点推荐图表，也可以让右侧 AI 助手解释数据'
    : `已加载 ${dataset.filename || '数据集'}，拖字段即可出图`;
  syncSpecUI();
  setBusy(false);
}

/* ------------------------------------------------------------------ */
/* 上传                                                                */
/* ------------------------------------------------------------------ */
async function handleFile(file) {
  if (!file) return;
  if (!/\.(csv|tsv|xlsx|xls|json)$/i.test(file.name)) {
    toast('只支持 CSV / TSV / Excel / JSON 文件', 'warn');
    return;
  }
  if (file.size > state.maxFileMb * 1024 * 1024) {
    toast(`文件超过 ${state.maxFileMb} MB，请先在 Excel 里抽样`, 'warn');
    return;
  }
  const form = new FormData();
  form.append('file', file);
  $('#fileName').textContent = `解析中：${file.name}`;
  $('#datasetBadge').textContent = '解析中…';
  $('#datasetBadge').className = 'badge info';
  try {
    const dataset = await api('/api/datasets', { method: 'POST', body: form });
    applyDataset(dataset);
    toast(`已解析 ${fmtNum(dataset.rows)} 行 × ${dataset.columns} 列`, 'success');
    await bootstrapSpecs();
  } catch (err) {
    $('#fileName').textContent = '还没有选择文件';
    $('#datasetBadge').textContent = '上传失败';
    $('#datasetBadge').className = 'badge warn';
    toast(err.message + (err.hint ? ` · ${err.hint}` : ''), 'error');
  }
}

async function bootstrapSpecs() {
  if (!state.dataset) return;
  try {
    const recs = await api(`/api/datasets/${encodeURIComponent(state.dataset.dataset_id)}/specs`);
    state.recommendations = recs || [];
    renderRecommendations();
    if (state.recommendations.length) {
      applyRecommendation(state.recommendations[0].spec);
    }
  } catch (err) {
    toast(`推荐图表读取失败：${err.message}`, 'warn');
  }
}

/* ------------------------------------------------------------------ */
/* 公网演示：首屏自动载入内置示例数据                                   */
/* ------------------------------------------------------------------ */
async function loadDemo() {
  showEmpty('chart', '正在载入示例数据', '马上就能拖字段出图');
  try {
    const dataset = await api('/api/demo', { method: 'POST' });
    applyDataset(dataset);
    await bootstrapSpecs();
    return true;
  } catch (err) {
    // 演示模式关闭 / 示例文件缺失时，安静退回空工作台，不打扰 HR。
    console.warn('示例数据未载入：', err.message);
    showEmpty(
      'chart',
      '上传一份表格，开始作图',
      '支持 CSV / TSV / Excel / JSON；把分类字段拖到 X 轴、数值字段拖到 Y 轴'
    );
    return false;
  }
}

/* ------------------------------------------------------------------ */
/* 视觉通道 / spec                                                      */
/* ------------------------------------------------------------------ */
function syncSpecUI() {
  const spec = state.spec;

  $$('#chartTypes button').forEach((button) => {
    button.classList.toggle('active', button.dataset.type === spec.chart_type);
  });

  ['x', 'y', 'color'].forEach((channel) => {
    const slot = $(`.slot[data-channel="${channel}"]`);
    if (!slot) return;
    const value = String(spec[channel] || '');
    const info = CHANNEL_META[channel];
    slot.classList.toggle('filled', Boolean(value));
    slot.innerHTML = `
      <span class="slot-tag">${esc(info.tag)}</span>
      <span class="slot-body">
        <b>${value ? esc(value) : esc(info.placeholder)}</b>
        <small>${value ? esc(kindLabel(value)) : esc(info.hint)}</small>
      </span>
      <button class="slot-clear ${value ? '' : 'hidden'}" type="button" data-clear="${channel}" title="移除字段">${icon('close', 14)}</button>`;
  });

  $('#aggregation').value = spec.aggregation;
  $('#sort').value = spec.sort;
  $('#topN').value = String(spec.top_n || 0);
  $('#applySpecBtn').disabled = state.busy || !state.dataset;
  renderSpecWarn();
}

function kindLabel(name) {
  const kind = kindOf(name);
  if (kind === 'numeric') return '数值列';
  if (kind === 'datetime') return '时间列';
  if (kind === 'boolean') return '布尔列';
  return '文本列';
}

function validateSpec() {
  if (!state.dataset) return '请先上传数据';
  const spec = state.spec;
  const chart = spec.chart_type;

  if (chart === 'scatter') {
    if (!spec.x || !spec.y) return '散点图需要 X 和 Y 两个字段';
    if (!['numeric', 'datetime'].includes(kindOf(spec.x))) return '散点图的 X 轴需要数值或时间字段';
    if (kindOf(spec.y) !== 'numeric') return '散点图的 Y 轴需要数值字段';
  } else if (chart === 'box') {
    if (!spec.y) return '箱线图需要 Y 轴数值字段';
    if (kindOf(spec.y) !== 'numeric') return '箱线图的 Y 轴需要数值字段';
  } else if (chart === 'pie') {
    if (!spec.x) return '饼图需要 X 轴分类字段';
    if (!spec.y && spec.aggregation !== 'count') return '饼图需要 Y 轴，或把聚合改成「计数」';
  } else {
    if (!spec.x) return '请把字段拖到 X 轴';
    if (!spec.y && spec.aggregation !== 'count') return '请把数值字段拖到 Y 轴，或把聚合改成「计数」';
  }
  return '';
}

function specWarnings() {
  if (!state.dataset) return [];
  const spec = state.spec;
  const reason = validateSpec();
  if (reason) return [reason];

  const items = [];
  if (spec.x) {
    const unique = uniqueOf(spec.x);
    if (['bar', 'pie'].includes(spec.chart_type) && unique > 40 && !spec.top_n) {
      items.push(`「${spec.x}」有 ${unique} 个取值，建议设置 Top N`);
    }
  }
  if (spec.color) {
    const unique = uniqueOf(spec.color);
    if (unique > 12) items.push(`颜色字段「${spec.color}」有 ${unique} 个取值，图例会偏多`);
  }
  if (spec.y && kindOf(spec.y) !== 'numeric' && !['none', 'count'].includes(spec.aggregation)) {
    items.push(`「${spec.y}」不是数值列，非数值行会被忽略`);
  }
  return items;
}

function renderSpecWarn() {
  const box = $('#specWarn');
  const items = specWarnings();
  if (!items.length) {
    box.classList.add('hidden');
    box.innerHTML = '';
    return;
  }
  box.classList.remove('hidden');
  box.innerHTML = items.map((item) => `· ${esc(item)}`).join('<br>');
}

function setSpec(patch, { auto = true } = {}) {
  state.spec = Object.assign({}, state.spec, patch);
  syncSpecUI();
  renderExplore();
  if (auto) scheduleApply();
}

function scheduleApply(delay = 260) {
  clearTimeout(state.applyTimer);
  state.applyTimer = setTimeout(() => applySpec(), delay);
}

function assignChannel(channel, name) {
  if (!name) return;
  const kind = kindOf(name);
  if (channel === 'y' && kind !== 'numeric' && state.spec.aggregation !== 'count') {
    toast(`「${name}」不是数值列，已切换为计数`, 'warn');
    setSpec({ y: name, aggregation: 'count' });
    return;
  }
  setSpec({ [channel]: name });
}

function smartAssign(name) {
  const kind = kindOf(name);
  const spec = state.spec;
  if (kind === 'numeric') {
    if (!spec.y) return setSpec({ y: name });
    if (!spec.x) return setSpec({ x: name });
    if (!spec.color) return setSpec({ color: name });
    return setSpec({ y: name });
  }
  if (!spec.x) return setSpec({ x: name });
  if (!spec.color && name !== spec.x) return setSpec({ color: name });
  return setSpec({ x: name });
}

function applyRecommendation(spec) {
  // 标题交给后端按当前通道生成，避免切换图表类型后还挂着旧标题。
  const { title, ...rest } = spec || {};
  state.spec = Object.assign(defaultSpec(), rest);
  syncSpecUI();
  renderExplore();
  applySpec();
}

function setChartType(type) {
  const patch = { chart_type: type };
  if (type === 'scatter' || type === 'box') patch.aggregation = 'none';
  if (type === 'pie' && state.spec.aggregation === 'none') patch.aggregation = 'sum';
  setSpec(patch);
}

function specTitle() {
  const spec = state.spec;
  const x = spec.x || '数据';
  const y = spec.y || '记录数';
  if (spec.chart_type === 'scatter') return `${y} 与 ${x} 的关系`;
  if (spec.chart_type === 'pie') return `各 ${x} 的 ${y} 占比`;
  if (spec.chart_type === 'line' || spec.chart_type === 'area') return `${y} 随 ${x} 的变化`;
  if (spec.chart_type === 'box') return spec.x ? `${y} 按 ${x} 的分布` : `${y} 的分布`;
  if (spec.aggregation === 'count') return `各 ${x} 的记录数`;
  return `各 ${x} 的 ${y}`;
}

function describeSpec() {
  const spec = state.spec;
  const parts = [CHART_LABELS[spec.chart_type] || spec.chart_type, specTitle()];
  if (spec.x) parts.push(`X=${spec.x}`);
  if (spec.y) parts.push(`Y=${spec.y}`);
  if (spec.color) parts.push(`颜色=${spec.color}`);
  if (spec.aggregation !== 'none') parts.push(`聚合=${AGG_LABELS[spec.aggregation] || spec.aggregation}`);
  if (spec.sort !== 'none') parts.push(spec.sort === 'asc' ? '排序=升序' : '排序=降序');
  if (spec.top_n) parts.push(`Top ${spec.top_n}`);
  return parts.join(' · ');
}

async function applySpec() {
  if (!state.dataset) return;
  const reason = validateSpec();
  if (reason) {
    renderSpecWarn();
    return;
  }
  if (state.busy) {
    state.specDirty = true;
    return;
  }

  clearTimeout(state.applyTimer);
  state.specDirty = false;
  hideError();
  setBusy(true, 'spec');
  $('#footnote').textContent = '正在编译视觉通道并在沙箱中执行…';

  try {
    const specPayload = Object.assign({}, state.spec);
    delete specPayload.title;
    const data = await api('/api/chart/spec', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        dataset_id: state.dataset.dataset_id,
        spec: specPayload,
        session_id: state.sessionId,
      }),
    });
    state.sessionId = data.session_id || state.sessionId;
    applyResult(data, 'spec');
  } catch (err) {
    showError(err);
    toast(err.message + (err.hint ? ` · ${err.hint}` : ''), 'error');
  } finally {
    setBusy(false);
    refreshHealthSoon();
    if (state.specDirty) {
      state.specDirty = false;
      scheduleApply(80);
    }
  }
}

function renderExplore() {
  const box = $('#exploreList');
  if (!state.dataset) {
    box.innerHTML = '<p class="hint">出图后这里会出现下一步建议</p>';
    return;
  }
  const spec = state.spec;
  const meta = state.dataset.meta || {};
  const numeric = (meta.numeric_columns || []).slice();
  const categories = (meta.category_columns || []).slice();
  const datetimes = (meta.datetime_columns || []).slice();
  const timeCol = datetimes[0] || meta.time_range?.column || '';
  const items = [];

  const freeCategory = categories.find(
    (name) =>
      name !== spec.x &&
      name !== spec.y &&
      name !== spec.color &&
      uniqueOf(name) > 1 &&
      uniqueOf(name) <= 30
  );

  if (spec.x && !spec.color && freeCategory) {
    items.push({ label: `按「${freeCategory}」拆分颜色`, patch: { color: freeCategory } });
  }
  if (spec.x && ['bar', 'pie'].includes(spec.chart_type) && !spec.top_n) {
    items.push({ label: '只看前 10 名', patch: { sort: 'desc', top_n: 10 } });
  }
  if (spec.x && spec.chart_type !== 'line' && spec.chart_type !== 'area' && timeCol === spec.x) {
    items.push({ label: '改成折线看趋势', patch: { chart_type: 'line', sort: 'none' } });
  }
  if (spec.aggregation === 'none' && spec.x && spec.y) {
    items.push({ label: '按总和聚合', patch: { aggregation: 'sum', top_n: 10, sort: 'desc' } });
  }
  if (spec.y && numeric.length >= 2 && spec.chart_type !== 'scatter') {
    const other = numeric.find((name) => name !== spec.y);
    if (other) {
      items.push({
        label: `看 ${spec.y} 与 ${other} 的相关性`,
        patch: { chart_type: 'scatter', x: spec.y, y: other, aggregation: 'none', color: '', top_n: 0 },
      });
    }
  }
  if (spec.x && spec.y && spec.chart_type !== 'pie') {
    items.push({
      label: '切换饼图看占比',
      patch: { chart_type: 'pie', aggregation: 'sum', top_n: 8 },
    });
  }

  const picked = items.slice(0, 5);
  box.innerHTML = picked.length
    ? picked
        .map(
          (item, index) =>
            `<button class="explore" type="button" data-explore="${index}">${esc(item.label)}</button>`
        )
        .join('')
    : '<p class="hint">当前配置已经比较完整，可以试试右侧 AI 助手</p>';
  box._items = picked;
}

/* ------------------------------------------------------------------ */
/* 加载态 / 错误态                                                      */
/* ------------------------------------------------------------------ */
function showEmpty(glyph, title, subtitle) {
  $('#overlay').classList.remove('hidden');
  $('#loadingState').classList.add('hidden');
  const empty = $('#emptyState');
  empty.classList.remove('hidden');
  const art = glyph === 'alert'
    ? `<span class="empty-glyph small" aria-hidden="true">${icon('alert', 28)}</span>`
    : EMPTY_GLYPH;
  empty.innerHTML = `${art}<b>${esc(title)}</b><small>${esc(subtitle)}</small>`;
}

function showLoading(text) {
  $('#overlay').classList.remove('hidden');
  $('#emptyState').classList.add('hidden');
  $('#loadingState').classList.remove('hidden');
  $('#loadingText').textContent = text || '正在准备…';
  $('#stages').innerHTML = '';
  $('#footnote').textContent = '模型正在阅读数据字典并编写绘图代码…';
}

function hideLoading() {
  $('#overlay').classList.add('hidden');
}

function showError(err) {
  const box = $('#errorBox');
  box.classList.remove('hidden');
  const parts = [
    `<b>${esc(err.code || '出错')}</b>`,
    `<p>${esc(err.message || '未知错误')}</p>`,
  ];
  if (err.hint) parts.push(`<p>提示：${esc(err.hint)}</p>`);
  if (err.requestId) parts.push(`<p>请求编号：${esc(err.requestId)}</p>`);
  box.innerHTML = parts.join('');
  if (!state.chartJson) {
    showEmpty('alert', '这次没画出来', '按上方提示调整后再试一次');
  }
  setExportEnabled(Boolean(state.chartJson));
  $('#footnote').textContent = '免费模型偶尔会限流，稍等几秒重试即可';
}

function hideError() {
  const box = $('#errorBox');
  box.classList.add('hidden');
  box.innerHTML = '';
}

/* ------------------------------------------------------------------ */
/* AI 生成（SSE）                                                       */
/* ------------------------------------------------------------------ */
const STAGE_ORDER = ['cache', 'thinking', 'generated', 'executing', 'repairing', 'repaired', 'done'];

function onStage(event) {
  const stages = $('#stages');
  $$('.stage.active', stages).forEach((el) => {
    el.classList.remove('active');
    el.classList.add('done');
  });

  if (event.stage === 'done' || event.stage === 'cache') {
    $('#loadingText').textContent =
      event.stage === 'cache' ? '命中缓存，直接复用结果…' : '图表马上出来…';
    if (event.stage === 'done') return;
  }

  if (!event.message) return;

  const item = document.createElement('li');
  item.className = 'stage active' + (event.stage === 'error' ? ' error' : '');
  item.dataset.stage = event.stage || '';
  item.innerHTML = `<i></i><span>${esc(event.message)}</span>`;
  stages.appendChild(item);
  while (stages.children.length > 8) stages.removeChild(stages.firstChild);

  if (event.stage === 'thinking') {
    $('#loadingText').textContent = '模型正在思考…';
  } else if (event.stage !== 'error') {
    $('#loadingText').textContent = '正在生成图表…';
  }
}

async function streamChat(payload) {
  const response = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const text = await response.text();
    let data = null;
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
    throw makeError(data || { message: `请求失败（HTTP ${response.status}）` });
  }
  if (!response.body) {
    throw new Error('当前浏览器不支持流式读取，请换用新版 Chrome / Edge');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  let result = null;
  let failure = null;

  const consume = (raw) => {
    const data = raw
      .split('\n')
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trim())
      .join('');
    if (!data) return;
    let event = null;
    try {
      event = JSON.parse(data);
    } catch {
      return;
    }
    if (event.type === 'stage') onStage(event);
    else if (event.type === 'result') result = event.data;
    else if (event.type === 'error') failure = event.error || { message: '生成失败' };
  };

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let index = buffer.indexOf('\n\n');
    while (index !== -1) {
      consume(buffer.slice(0, index));
      buffer = buffer.slice(index + 2);
      index = buffer.indexOf('\n\n');
    }
  }
  buffer += decoder.decode();
  if (buffer.trim()) consume(buffer);

  if (failure) throw makeError(failure);
  return result;
}

async function generate(refresh = false) {
  if (state.busy) return;
  if (!state.dataset) {
    toast('请先上传一个表格文件', 'warn');
    return;
  }
  const prompt = ($('#prompt').value || '').trim();
  if (!prompt) {
    toast('先用一句话描述你想看的图表', 'warn');
    $('#prompt').focus();
    return;
  }

  hideError();
  setBusy(true, 'ai');
  showLoading('正在连接模型…');
  state.lastPrompt = prompt;
  state.chartTitle = prompt.slice(0, 40) || 'DataAgent 图表';

  try {
    const result = await streamChat({
      dataset_id: state.dataset.dataset_id,
      prompt,
      session_id: state.sessionId,
      refresh,
    });
    if (!result) throw new Error('服务端没有返回图表结果');
    applyResult(result, 'ai');
  } catch (err) {
    showError(err);
    toast(err.message || '生成失败', 'error');
  } finally {
    setBusy(false);
    refreshHealthSoon();
  }
}

function applyResult(data, source) {
  state.chartJson = data.chart_json || '';
  state.sessionId = data.session_id || state.sessionId;
  state.lastResult = data;
  state.lastResultSource = source;

  if (source === 'spec') {
    state.chartTitle = specTitle();
    state.lastPrompt = describeSpec();
  } else {
    state.lastPrompt = ($('#prompt').value || '').trim() || state.chartTitle;
  }

  $('#sessionBadge').textContent = '多轮会话';
  $('#sessionBadge').className = 'badge ok';

  const badges = [];
  if (source === 'spec') {
    badges.push('<span class="badge ok">规则编译</span>');
  } else if (data.model) {
    badges.push(`<span class="badge info">${esc(shortModel(data.model))}</span>`);
  }
  if (data.cached) badges.push('<span class="badge ghost">缓存</span>');
  if (data.elapsed_ms != null && Number(data.elapsed_ms) > 0) {
    const ms = Number(data.elapsed_ms) || 0;
    badges.push(
      `<span class="badge ghost">${ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)}s`}</span>`
    );
  }
  if (data.points) badges.push(`<span class="badge ghost">${fmtNum(data.points)} 点</span>`);
  if (data.attempts > 1) badges.push(`<span class="badge warn">自愈 ${data.attempts - 1} 次</span>`);
  $('#resultBadges').innerHTML = badges.join('');

  renderChart(state.chartJson).catch((err) => showError({ message: err.message }));
  renderCode(data.code || '', source);
  renderExplore();

  hideError();
  setExportEnabled(true);
  $('#footnote').textContent =
    source === 'spec'
      ? '这条路径不调用模型，图表由视觉通道确定性编译'
      : '模型生成 + 沙箱执行 + 失败自愈完成';
}

function setBusy(busy, mode = '') {
  state.busy = Boolean(busy);
  $('#generateBtn').disabled = state.busy || !state.dataset;
  $('#refreshBtn').disabled = state.busy || !state.dataset;
  $('#newSessionBtn').disabled = state.busy;
  $('#applySpecBtn').disabled = state.busy || !state.dataset;
  $('#generateBtn').textContent = state.busy && mode === 'ai' ? '生成中…' : '生成';
  $('#applySpecBtn').textContent = state.busy && mode === 'spec' ? '更新中…' : '更新图表';
}

function setExportEnabled(enabled) {
  ['#pngBtn', '#htmlBtn', '#csvBtn', '#reportBtn'].forEach((selector) => {
    $(selector).disabled = !enabled;
  });
}

function newSession() {
  state.sessionId = null;
  $('#sessionBadge').textContent = '新会话';
  $('#sessionBadge').className = 'badge';
  toast('已开启新会话，下一轮不会带上一轮的上下文', 'success');
}

/* ------------------------------------------------------------------ */
/* 图表渲染                                                             */
/* ------------------------------------------------------------------ */
function themedLayout(rawLayout) {
  const dark = state.theme === 'dark';
  const text = cssVar('--text') || '#1e1c18';
  const muted = cssVar('--text-2') || '#56534c';
  const border = cssVar('--border-strong') || 'rgba(30, 28, 24, 0.22)';
  const surface = cssVar('--surface') || '#fffefb';
  const grid = dark ? 'rgba(236, 233, 225, 0.10)' : 'rgba(30, 28, 24, 0.09)';
  const line = dark ? 'rgba(236, 233, 225, 0.22)' : 'rgba(30, 28, 24, 0.20)';

  let layout;
  try {
    layout = JSON.parse(JSON.stringify(rawLayout || {}));
  } catch {
    layout = {};
  }

  layout.paper_bgcolor = 'rgba(0,0,0,0)';
  layout.plot_bgcolor = 'rgba(0,0,0,0)';
  layout.font = Object.assign({}, layout.font, { color: text });

  if (typeof layout.title === 'string') layout.title = { text: layout.title };
  layout.title = Object.assign({}, layout.title);
  layout.title.font = Object.assign({}, layout.title.font, { color: text });

  layout.legend = Object.assign({}, layout.legend);
  layout.legend.font = Object.assign({}, layout.legend.font, { color: text });

  layout.hoverlabel = Object.assign({}, layout.hoverlabel, {
    bgcolor: surface,
    bordercolor: border,
  });
  layout.hoverlabel.font = Object.assign({}, layout.hoverlabel.font, { color: text });

  Object.keys(layout).forEach((key) => {
    if (!/^[xy]axis\d*$/.test(key)) return;
    const axis = layout[key];
    if (!axis || typeof axis !== 'object') return;
    axis.tickfont = Object.assign({}, axis.tickfont, { color: muted });
    if (axis.title && typeof axis.title === 'object') {
      axis.title = Object.assign({}, axis.title);
      axis.title.font = Object.assign({}, axis.title.font, { color: text });
    }
    axis.gridcolor = grid;
    axis.linecolor = line;
    axis.zerolinecolor = line;
  });

  return layout;
}

/* 只把 Plotly 模板的默认配色换成工作台的墨蓝 + 中性色阶。
   模型自己指定的颜色原样保留，不改变图里颜色本来的含义。 */
const PLOTLY_DEFAULTS = [
  '#636efa', '#ef553b', '#00cc96', '#ab63fa', '#ffa15a',
  '#19d3f3', '#ff6692', '#b6e880', '#ff97ff', '#fecb52',
  '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
];

function themedTraces(rawData) {
  const dark = state.theme === 'dark';
  const accent = cssVar('--accent') || '#2b5f9e';
  const palette = dark
    ? [accent, '#9aa0ab', '#b1a79a', '#7fa89b', '#c0a184']
    : [accent, '#6e7c8c', '#8a8478', '#5c8a7d', '#9a7b62'];
  const swap = (value) => {
    const hit = PLOTLY_DEFAULTS.indexOf(String(value || '').toLowerCase());
    return hit === -1 ? value : palette[hit % palette.length];
  };
  return (Array.isArray(rawData) ? rawData : []).map((raw) => {
    let trace;
    try {
      trace = JSON.parse(JSON.stringify(raw || {}));
    } catch {
      return raw;
    }
    if (trace.line && typeof trace.line === 'object' && typeof trace.line.color === 'string') {
      trace.line.color = swap(trace.line.color);
    }
    if (trace.marker && typeof trace.marker === 'object') {
      if (typeof trace.marker.color === 'string') trace.marker.color = swap(trace.marker.color);
      if (Array.isArray(trace.marker.color)) trace.marker.color = trace.marker.color.map(swap);
      if (Array.isArray(trace.marker.colors)) trace.marker.colors = trace.marker.colors.map(swap);
      if (trace.marker.line && typeof trace.marker.line.color === 'string') {
        trace.marker.line.color = swap(trace.marker.line.color);
      }
    }
    if (typeof trace.fillcolor === 'string') trace.fillcolor = swap(trace.fillcolor);
    return trace;
  });
}

function plotConfig() {
  return {
    responsive: true,
    displaylogo: false,
    scrollZoom: true,
    modeBarButtonsToRemove: ['lasso2d', 'select2d', 'autoScale2d', 'toggleSpikelines'],
    toImageButtonOptions: {
      format: 'png',
      filename: fileBaseName(),
      width: 1600,
      height: 1000,
      scale: 2,
    },
  };
}

async function renderChart(chartJson) {
  if (!chartJson) return;
  if (!window.Plotly) {
    throw new Error('Plotly 资源没有加载成功，请刷新页面或检查网络');
  }
  let figure;
  try {
    figure = typeof chartJson === 'string' ? JSON.parse(chartJson) : chartJson;
  } catch {
    throw new Error('图表数据不是合法 JSON，无法渲染');
  }

  const layout = themedLayout(figure.layout || {});
  await Plotly.react('chart', themedTraces(figure.data), layout, plotConfig());
  hideLoading();
  requestAnimationFrame(() => {
    try {
      Plotly.Plots.resize('chart');
    } catch {
      /* 容器尺寸变化时由 responsive 处理 */
    }
  });
}

function renderCode(code, source) {
  const panel = $('#codePanel');
  const text = String(code || '').trim();
  state.code = text;
  if (!text) {
    panel.classList.add('hidden');
    return;
  }
  $('#code').textContent = text;
  $('#toggleCode').textContent = source === 'ai' ? '查看模型代码' : '查看编译代码';
  if (!panel.classList.contains('hidden')) {
    panel.querySelector('pre').classList.remove('hidden');
  }
}

/* ------------------------------------------------------------------ */
/* 导出                                                                */
/* ------------------------------------------------------------------ */
function fileBaseName() {
  const base = (state.lastPrompt || state.chartTitle || 'chart')
    .replace(/[\\/:*?"<>|\s]+/g, '-')
    .slice(0, 30);
  return base || 'chart';
}

function triggerDownload(url, filename) {
  const anchor = document.createElement('a');
  anchor.href = url;
  if (filename) anchor.download = filename;
  anchor.rel = 'noopener';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

async function fetchExport(kind) {
  return api('/api/export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      chart_json: state.chartJson,
      kind,
      title: state.chartTitle,
      prompt: state.lastPrompt,
      meta: state.dataset ? state.dataset.meta : null,
    }),
  });
}

async function exportPNG() {
  if (!state.chartJson) return toast('先生成一张图', 'warn');
  try {
    await Plotly.downloadImage($('#chart'), {
      format: 'png',
      filename: fileBaseName(),
      width: 1600,
      height: 1000,
      scale: 2,
    });
    toast('PNG 已开始下载', 'success');
  } catch {
    try {
      const result = await fetchExport('png');
      triggerDownload(result.url, result.filename);
      toast('PNG 已由服务端生成', 'success');
    } catch (err) {
      toast(err.message + (err.hint ? ` · ${err.hint}` : ''), 'error');
    }
  }
}

async function exportFile(kind) {
  if (!state.chartJson) return toast('先生成一张图', 'warn');
  toast(`正在导出 ${kind.toUpperCase()}…`);
  try {
    const result = await fetchExport(kind);
    triggerDownload(result.url, result.filename);
    toast('导出完成', 'success');
  } catch (err) {
    toast(err.message + (err.hint ? ` · ${err.hint}` : ''), 'error');
  }
}

async function copyCode() {
  const code = $('#code').textContent;
  if (!code.trim()) return toast('还没有可复制的代码', 'warn');
  try {
    await navigator.clipboard.writeText(code);
    toast('代码已复制', 'success');
  } catch {
    const range = document.createRange();
    range.selectNodeContents($('#code'));
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    try {
      document.execCommand('copy');
      toast('代码已复制', 'success');
    } catch {
      toast('复制失败，请手动选中代码', 'error');
    }
  }
}

/* ------------------------------------------------------------------ */
/* 历史抽屉                                                             */
/* ------------------------------------------------------------------ */
async function openHistory() {
  $('#scrim').classList.remove('hidden');
  $('#drawer').classList.add('open');
  await loadHistory();
}

function closeHistory() {
  $('#drawer').classList.remove('open');
  $('#scrim').classList.add('hidden');
}

async function loadHistory() {
  const chartList = $('#chartList');
  const sessionList = $('#sessionList');
  chartList.innerHTML = '<p class="hint">加载中…</p>';
  sessionList.innerHTML = '<p class="hint">加载中…</p>';

  try {
    const [charts, sessions] = await Promise.all([
      api('/api/charts?limit=24'),
      api('/api/sessions?limit=16'),
    ]);

    $('#chartCount').textContent = String(charts.length);
    $('#sessionCount').textContent = String(sessions.length);

    chartList.innerHTML = charts.length
      ? charts
          .map(
            (item) => `
        <button class="history-item" type="button" data-chart-id="${esc(item.id)}">
          <b>${esc(item.prompt || '未命名图表')}</b>
          <span>${esc(shortModel(item.model))} · ${esc(fmtTime(item.created_at))}${
            item.elapsed_ms ? ` · ${(item.elapsed_ms / 1000).toFixed(1)}s` : ''
          }</span>
        </button>`
          )
          .join('')
      : '<p class="hint">还没有历史图表，先生成一张吧</p>';

    sessionList.innerHTML = sessions.length
      ? sessions
          .map(
            (item) => `
        <button class="history-item" type="button" data-session-id="${esc(item.session_id)}">
          <b>${esc(item.title || '未命名会话')}</b>
          <span>${item.message_count || 0} 轮 · ${esc(fmtTime(item.updated_at))}</span>
        </button>`
          )
          .join('')
      : '<p class="hint">还没有会话</p>';
  } catch (err) {
    chartList.innerHTML = `<p class="hint">读取失败：${esc(err.message)}</p>`;
    sessionList.innerHTML = '';
  }
}

function restoreSession(session) {
  if (state.dataset && session.dataset_id === state.dataset.dataset_id) {
    state.sessionId = session.session_id;
    $('#sessionBadge').textContent = '多轮会话';
    $('#sessionBadge').className = 'badge ok';
    return true;
  }
  state.sessionId = null;
  $('#sessionBadge').textContent = '新会话';
  $('#sessionBadge').className = 'badge';
  return false;
}

function showHistoryBadges(model, timestamp, label) {
  $('#resultBadges').innerHTML = [
    model ? `<span class="badge info">${esc(shortModel(model))}</span>` : '',
    timestamp ? `<span class="badge ghost">${esc(fmtTime(timestamp))}</span>` : '',
    `<span class="badge ok">${esc(label)}</span>`,
  ].join('');
}

async function openHistoryChart(messageId) {
  const detail = await api(`/api/charts/${encodeURIComponent(messageId)}`);
  if (!detail.chart_json) throw new Error('这条记录没有图表数据');

  state.chartJson =
    typeof detail.chart_json === 'string' ? detail.chart_json : JSON.stringify(detail.chart_json);
  state.lastPrompt = detail.prompt || '';
  state.chartTitle = (detail.prompt || '历史图表').slice(0, 40);
  $('#prompt').value = detail.prompt || '';
  renderCode(detail.code || '', detail.model === 'rules' ? 'spec' : 'ai');
  showHistoryBadges(detail.model, detail.created_at, '历史');
  setExportEnabled(true);
  hideError();

  try {
    const session = await api(`/api/sessions/${encodeURIComponent(detail.session_id)}`);
    if (!restoreSession(session)) {
      toast('已载入历史图表；继续对话请先上传对应的数据文件', 'warn');
    }
  } catch {
    state.sessionId = null;
  }

  await renderChart(state.chartJson);
  hideLoading();
  closeHistory();
}

async function openHistorySession(sessionId) {
  const session = await api(`/api/sessions/${encodeURIComponent(sessionId)}`);
  const withChart = (session.messages || []).filter((message) => message.chart_json);
  const last = withChart[withChart.length - 1];

  if (last) {
    state.chartJson =
      typeof last.chart_json === 'string' ? last.chart_json : JSON.stringify(last.chart_json);
    state.lastPrompt = last.prompt || '';
    state.chartTitle = (last.prompt || '历史图表').slice(0, 40);
    $('#prompt').value = last.prompt || '';
    renderCode(last.code || '', last.model === 'rules' ? 'spec' : 'ai');
    showHistoryBadges(last.model, session.updated_at, '历史');
    setExportEnabled(true);
    hideError();
    await renderChart(state.chartJson);
    hideLoading();
  } else {
    toast('这个会话还没有成功的图表', 'warn');
  }

  if (restoreSession(session)) {
    toast('已恢复会话，可以继续追问', 'success');
  } else if (state.dataset) {
    toast('该会话属于另一个数据集，请重新上传对应文件', 'warn');
  }
  closeHistory();
}

/* ------------------------------------------------------------------ */
/* 事件绑定                                                             */
/* ------------------------------------------------------------------ */
function bindEvents() {
  const fileInput = $('#fileInput');
  fileInput.addEventListener('change', (event) => {
    const file = event.target.files && event.target.files[0];
    if (file) handleFile(file);
    fileInput.value = '';
  });

  const drop = $('#drop');
  ['dragenter', 'dragover'].forEach((name) => {
    drop.addEventListener(name, (event) => {
      event.preventDefault();
      drop.classList.add('dragover');
    });
  });
  ['dragleave', 'drop'].forEach((name) => {
    drop.addEventListener(name, (event) => {
      event.preventDefault();
      drop.classList.remove('dragover');
    });
  });
  drop.addEventListener('drop', (event) => {
    const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
    if (file) handleFile(file);
  });
  window.addEventListener('dragover', (event) => event.preventDefault());
  window.addEventListener('drop', (event) => event.preventDefault());

  /* 字段：点击分配 / 拖拽 */
  $('#fieldList').addEventListener('click', (event) => {
    const action = event.target.closest('[data-assign]');
    const field = event.target.closest('.field');
    if (!field) return;
    const name = field.dataset.field;
    if (action) {
      assignChannel(action.dataset.assign, name);
    } else {
      smartAssign(name);
    }
  });

  $('#fieldList').addEventListener('dragstart', (event) => {
    const field = event.target.closest('.field');
    if (!field) return;
    event.dataTransfer.setData('text/plain', field.dataset.field);
    event.dataTransfer.effectAllowed = 'copy';
    field.classList.add('dragging');
  });

  $('#fieldList').addEventListener('dragend', (event) => {
    const field = event.target.closest('.field');
    if (field) field.classList.remove('dragging');
  });

  /* 通道槽：拖放 / 清除 */
  $$('.slot').forEach((slot) => {
    slot.addEventListener('dragover', (event) => {
      event.preventDefault();
      event.dataTransfer.dropEffect = 'copy';
      slot.classList.add('over');
    });
    slot.addEventListener('dragleave', () => slot.classList.remove('over'));
    slot.addEventListener('drop', (event) => {
      event.preventDefault();
      slot.classList.remove('over');
      const name = event.dataTransfer.getData('text/plain');
      assignChannel(slot.dataset.channel, name);
    });
    slot.addEventListener('click', (event) => {
      const clear = event.target.closest('[data-clear]');
      if (clear) {
        event.stopPropagation();
        setSpec({ [clear.dataset.clear]: '' });
        return;
      }
      const name = slot.querySelector('.slot-body b')?.textContent || '';
      if (name && name !== '拖入字段' && name !== '可选') {
        toast(`「${name}」已分配到 ${CHANNEL_META[slot.dataset.channel].tag} 通道`, 'info');
      } else {
        toast('把左侧字段拖到这个槽位', 'info');
      }
    });
  });

  /* 图表类型 */
  $('#chartTypes').addEventListener('click', (event) => {
    const button = event.target.closest('[data-type]');
    if (button) setChartType(button.dataset.type);
  });

  /* 推荐图表 */
  $('#recList').addEventListener('click', (event) => {
    const button = event.target.closest('[data-rec]');
    if (!button) return;
    const item = state.recommendations[Number(button.dataset.rec)];
    if (item) applyRecommendation(item.spec);
  });

  /* 探索路径 */
  $('#exploreList').addEventListener('click', (event) => {
    const button = event.target.closest('[data-explore]');
    if (!button) return;
    const items = $('#exploreList')._items || [];
    const item = items[Number(button.dataset.explore)];
    if (item) setSpec(item.patch);
  });

  /* 控件 */
  $('#aggregation').addEventListener('change', (event) => setSpec({ aggregation: event.target.value }));
  $('#sort').addEventListener('change', (event) => setSpec({ sort: event.target.value }));
  $('#topN').addEventListener('change', (event) => {
    const value = Math.max(0, Math.min(200, Number(event.target.value) || 0));
    setSpec({ top_n: value });
  });
  $('#applySpecBtn').addEventListener('click', () => applySpec());
  $('#clearChannels').addEventListener('click', () => setSpec({ x: '', y: '', color: '' }));

  /* AI 对话 */
  $('#generateBtn').addEventListener('click', () => generate(false));
  $('#refreshBtn').addEventListener('click', () => generate(true));
  $('#newSessionBtn').addEventListener('click', newSession);
  $('#prompt').addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
      event.preventDefault();
      generate(false);
    }
  });

  /* 主题 / 历史 */
  $('#themeBtn').addEventListener('click', toggleTheme);
  $('#historyBtn').addEventListener('click', openHistory);
  $('#closeDrawer').addEventListener('click', closeHistory);
  $('#scrim').addEventListener('click', closeHistory);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeHistory();
  });

  $('#chartList').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-chart-id]');
    if (!button) return;
    try {
      await openHistoryChart(button.dataset.chartId);
    } catch (err) {
      toast(err.message || '载入失败', 'error');
    }
  });

  $('#sessionList').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-session-id]');
    if (!button) return;
    try {
      await openHistorySession(button.dataset.sessionId);
    } catch (err) {
      toast(err.message || '载入失败', 'error');
    }
  });

  /* 导出 / 代码 */
  $('#pngBtn').addEventListener('click', exportPNG);
  $('#htmlBtn').addEventListener('click', () => exportFile('html'));
  $('#csvBtn').addEventListener('click', () => exportFile('csv'));
  $('#reportBtn').addEventListener('click', () => exportFile('report'));
  $('#copyCode').addEventListener('click', copyCode);

  const toggleCodePanel = () => {
    const panel = $('#codePanel');
    const hidden = panel.classList.toggle('hidden');
    if (!hidden) panel.querySelector('pre').classList.remove('hidden');
    $('#toggleCode').textContent = hidden ? '查看执行代码' : '收起代码';
  };
  $('#toggleCode').addEventListener('click', toggleCodePanel);
  $('#toggleCodeInline').addEventListener('click', toggleCodePanel);
}

/* ------------------------------------------------------------------ */
/* 初始化                                                               */
/* ------------------------------------------------------------------ */
function init() {
  const saved = localStorage.getItem('dataagent-theme');
  applyTheme(saved === 'dark' ? 'dark' : 'light');
  bindEvents();
  renderFields();
  renderRecommendations();
  syncSpecUI();
  setBusy(false);
  setExportEnabled(false);
  loadHealth();
  setInterval(loadHealth, 60_000);
  loadDemo();
}

init();
