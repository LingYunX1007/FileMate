const API = '/api/filemate';
const PAGE_SIZE = 50;
const EXCERPT_SIZE = 4000;
const POLL_INTERVAL = 2500;

export async function api(path, {method = 'GET', body} = {}) {
  const options = {method, credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}};
  if (method === 'POST') options.headers['Content-Type'] = 'application/json';
  if (body !== undefined) {
    options.body = JSON.stringify(body);
  }
  const response = await fetch(`${API}${path}`, options);
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail;
    const fallback = response.status === 401 ? '请先登录 dsh 再打开 FileMate。' : `请求失败（HTTP ${response.status}）`;
    const message = detail?.message ?? (typeof detail === 'string' ? detail : payload?.message);
    const code = detail?.code ?? payload?.code;
    const error = new Error(message ? `${message}${code ? `（${code}）` : ''}` : fallback);
    error.detail = detail;
    throw error;
  }
  if (payload === null) throw new Error('服务返回了无法识别的响应。');
  return payload;
}

export function planBinding(plan) {
  return {version: plan.version, hash: plan.hash};
}

export function bindingMatches(plan, other) {
  return Boolean(plan && other && plan.id === other.id && plan.version === other.version && plan.hash === other.hash);
}

function evidenceStart(evidence) {
  const location = evidence?.location;
  const valid = (value) => Number.isSafeInteger(value) && value >= 0;
  if (valid(evidence?.start)) return evidence.start;
  if (!valid(location?.start)) return null;
  // Legacy page and block citations recorded an offset within the chunk.
  if (valid(location.local_start) || !['page', 'paragraph', 'table'].includes(location.kind)) return location.start;
  return null;
}

export function getExcerpt(fileId, {start = 0, evidence} = {}) {
  const offset = evidenceStart(evidence) ?? start;
  return api(`${path('files', fileId)}/excerpt?start=${offset}&limit=${EXCERPT_SIZE}`);
}

const $ = (id) => document.getElementById(id);
const objects = (value) => Array.isArray(value) ? value : [];
const path = (group, id) => `/${group}/${encodeURIComponent(id)}`;
const content = (value) => typeof value === 'string' ? value : JSON.stringify(value, null, 2);
const dateFormat = new Intl.DateTimeFormat('zh-CN', {dateStyle: 'medium', timeStyle: 'short'});
const numeric = new Intl.NumberFormat('zh-CN');

function element(tag, {className, text, attrs, children = []} = {}) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = String(text);
  for (const [key, value] of Object.entries(attrs ?? {})) node.setAttribute(key, value);
  node.append(...children);
  return node;
}

function button(text, onClick, {className, disabled = false} = {}) {
  const node = element('button', {className, text, attrs: {type: 'button'}});
  node.disabled = disabled;
  node.addEventListener('click', onClick);
  return node;
}

function rowButton(title, detail, status, onClick, selected = false) {
  const node = button('', onClick, {className: 'entry'});
  node.append(element('span', {className: 'entry-top', children: [element('span', {className: 'entry-title', text: title}), ...(status ? [badge(status)] : [])]}));
  if (detail) node.append(element('span', {className: 'entry-meta path', text: detail}));
  if (selected) node.setAttribute('aria-current', 'true');
  return node;
}

const labels = {
  draft: '待审核', approved: '已批准', queued: '排队中', running: '执行中', executing: '执行中',
  completed: '已完成', succeeded: '已完成', failed: '失败', partial: '部分失败', partial_failure: '部分失败',
  recovery_required: '待恢复', needs_recovery: '待恢复', cancelled: '已取消', pending: '待处理',
  prepared: '已准备', skipped: '已跳过', submitted: '已提交',
};

function badge(status) {
  const tone = {approved: 'good', completed: 'good', succeeded: 'good', queued: 'warning', running: 'warning',
    executing: 'warning', prepared: 'warning', partial: 'bad', partial_failure: 'bad', failed: 'bad',
    recovery_required: 'bad', needs_recovery: 'bad'}[status];
  return element('span', {text: labels[status] ?? status ?? '未知', className: tone ? `badge ${tone}` : 'badge'});
}

function facts(pairs) {
  const group = element('dl', {className: 'facts'});
  for (const [name, value, className] of pairs) {
    if (value === undefined || value === null || value === '') continue;
    group.append(element('dt', {text: name}), element('dd', {className, text: value}));
  }
  return group;
}

const actions = (...children) => element('div', {className: 'actions', children});
const empty = (text) => element('p', {className: 'empty', text});
const hint = (text, warning = false) => element('p', {className: `hint${warning ? ' warning' : ''}`, text});

function time(value) {
  if (value === undefined || value === null || value === '') return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : dateFormat.format(date);
}

function size(value) {
  if (!Number.isFinite(value)) return '';
  if (value < 1024) return `${numeric.format(value)} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 ** 2).toFixed(1)} MB`;
}

function modified(file) {
  return file.mtime_ns == null ? time(file.modified_at) : time(Number(file.mtime_ns) / 1e6);
}

function locationText(location) {
  if (!location) return '';
  if (location.kind === 'page') return `第 ${location.page} 页`;
  if (location.kind === 'lines') return location.line_end && location.line_end !== location.line
    ? `第 ${location.line}–${location.line_end} 行` : `第 ${location.line} 行`;
  if (location.kind === 'paragraph') return `第 ${location.block} 个正文块（段落）`;
  if (location.kind === 'table') return `第 ${location.block} 个正文块（表格）`;
  return content(location);
}

function feedback(message, error = false) {
  const node = $('notice');
  node.hidden = !message;
  node.classList.toggle('bad', error);
  node.setAttribute('role', error ? 'alert' : 'status');
  node.textContent = message;
}

async function run(control, callback) {
  if (control?.disabled) return;
  if (control) control.disabled = true;
  feedback('');
  try {
    return await callback();
  } catch (error) {
    feedback(error.message, true);
  } finally {
    if (control) control.disabled = false;
  }
}

const state = {
  view: 'files', roots: [], search: null, file: null, fileEvidence: null, excerpt: null,
  plans: [], plan: null, edits: [], validation: null, confirmed: false, planBusy: false,
  jobs: [], job: null, jobBusy: false, poll: null,
};

function rootName(id) {
  const root = state.roots.find((entry) => entry.id === id);
  if (!root) return id;
  return root.label && root.label !== root.path ? `${root.label} · ${root.path}` : root.path;
}

function routeHref(view, id) {
  const params = new URLSearchParams();
  if (id) params.set({files: 'file', plans: 'plan', jobs: 'job'}[view], id);
  else if (view !== 'files') params.set('view', view);
  return `/filemate${params.size ? `?${params}` : ''}`;
}

function currentRoute() {
  const params = new URLSearchParams(window.location.search);
  for (const [view, key] of [['plans', 'plan'], ['jobs', 'job'], ['files', 'file']]) {
    const id = params.get(key) || params.get(`${key}_id`);
    if (id && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(id)) return {view, id};
  }
  const view = params.get('view');
  return {view: view === 'plans' || view === 'jobs' ? view : 'files', id: null};
}

function updateUrl(view, id, mode = 'push') {
  const target = routeHref(view, id);
  if (`${window.location.pathname}${window.location.search}` !== target) history[`${mode}State`](null, '', target);
}

function activate(view) {
  state.view = view;
  for (const section of ['files', 'plans', 'jobs']) $(`view-${section}`).hidden = section !== view;
  for (const link of document.querySelectorAll('[data-view]')) {
    if (link.dataset.view === view) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  schedulePoll();
}

async function openView(view, id, historyMode) {
  if (view === 'plans' && id && (state.planBusy || (id !== state.plan?.id && !mayReplacePlan()))) {
    if (!historyMode) updateUrl(state.view, selectedId(state.view), 'replace');
    return;
  }
  activate(view);
  if (historyMode && !id) updateUrl(view, selectedId(view), historyMode);
  if (view === 'files' && id) await showFile(id);
  if (view === 'plans') {
    const open = id && id !== state.plan?.id ? loadPlan(id) : null;
    await Promise.all([loadPlans(), open]);
  }
  if (view === 'jobs') {
    const open = id && id !== state.job?.id ? loadJob(id) : null;
    await Promise.all([loadJobs(), open]);
  }
  if (historyMode && id && state.view === view && selectedId(view) === id) updateUrl(view, id, historyMode);
}

function selectedId(view) {
  return {files: state.file?.id, plans: state.plan?.id, jobs: state.job?.id}[view];
}

const scanning = new Set();

function renderRoots() {
  const list = $('root-list');
  const select = $('search-root');
  const lastValue = select.value;
  list.replaceChildren();
  select.replaceChildren(element('option', {text: '选择目录', attrs: {value: ''}}));
  for (const root of state.roots) {
    select.append(element('option', {text: rootName(root.id), attrs: {value: root.id}}));
    const inspect = button('查看概况', () => run(inspect, async () => {
      renderRootSummary(await api(`${path('roots', root.id)}/inspect`));
    }));
    const scan = button(scanning.has(root.id) ? '扫描中…' : '扫描', () => run(scan, async () => {
      if (scanning.has(root.id)) return;
      scanning.add(root.id);
      scan.textContent = '扫描中…';
      feedback('正在扫描目录并更新索引…');
      let result;
      try {
        result = await api(`${path('roots', root.id)}/scan`, {method: 'POST'});
      } finally {
        scanning.delete(root.id);
        renderRoots();
      }
      const errors = objects(result.errors);
      const count = result.error_count ?? errors.length;
      const report = element('div', {className: 'stack', children: [
        element('h4', {text: `扫描完成 · ${root.label || root.path}`}),
        hint(`已扫描 ${result.scanned} 个文件${count ? `，${count} 条异常。` : '。'}`, count > 0),
      ]});
      if (errors.length) report.append(issueList(errors, (issue) => issue.relative_path));
      if (count > errors.length) report.append(hint(`已显示 ${errors.length} 条异常。`));
      showRootSummary(report);
      await loadRoots();
      feedback(`已更新 ${root.label || root.path} 的索引。`);
    }), {disabled: scanning.has(root.id)});
    list.append(element('div', {className: 'root-entry', children: [
      element('p', {className: 'entry-title', text: root.label || root.path}),
      ...(root.label && root.label !== root.path ? [element('p', {className: 'path entry-meta', text: root.path})] : []),
      hint(root.scanned_at ? `扫描于 ${time(root.scanned_at)}` : '尚未扫描'), actions(inspect, scan),
    ]}));
  }
  if (!state.roots.length) list.append(empty('暂无授权目录。'));
  select.value = state.roots.some((item) => item.id === lastValue) ? lastValue : (state.roots[0]?.id ?? '');
  select.disabled = state.roots.length === 0;
  $('search-submit').disabled = state.roots.length === 0 || $('search-results').hasAttribute('aria-busy');
  for (const edit of state.edits) if (edit.rootInput) fillRootInput(edit.rootInput, edit.root);
}

async function loadRoots() {
  const payload = await api('/roots');
  state.roots = objects(payload.items);
  renderRoots();
}

function showRootSummary(contentNode) {
  const panel = $('root-summary');
  panel.replaceChildren(contentNode);
  panel.hidden = false;
}

function renderRootSummary(summary) {
  const types = Object.entries(summary.extensions ?? {}).map(([extension, count]) => `${extension || '无扩展名'} ${count}`).join(' · ');
  const folders = objects(summary.folders).map((folder) => `${folder.name}（${folder.file_count}）`).join('、');
  const panel = element('div', {className: 'stack', children: [element('h4', {text: `目录概况 · ${summary.label || summary.path}`}), facts([
    ['索引文件', summary.file_count], ['总大小', size(summary.total_size)], ['文件类型', types], ['一级目录', folders],
  ])]});
  const files = objects(summary.items);
  if (files.length) panel.append(button('检索此目录', () => {
    $('search-root').value = summary.root_id || summary.id;
    $('search-form').requestSubmit();
  }));
  showRootSummary(panel);
}

function dateBoundary(raw, endOfDay) {
  if (!raw) return undefined;
  const date = new Date(`${raw}T00:00:00`);
  if (endOfDay) date.setDate(date.getDate() + 1);
  return date.toISOString();
}

function searchParameters(form) {
  const values = new FormData(form);
  const extension = values.get('extension').trim();
  const start = values.get('after');
  const end = values.get('before');
  if (start && end && start > end) throw new Error('开始日期须早于或等于结束日期。');
  return {
    root_id: values.get('root'), query: values.get('query').trim(), extension: extension || undefined,
    modified_after: dateBoundary(start, false), modified_before: dateBoundary(end, true), limit: PAGE_SIZE,
  };
}

let searches = 0;
async function search(params, offset = 0) {
  const request = ++searches;
  const results = $('search-results');
  results.setAttribute('aria-busy', 'true');
  try {
    const result = await api('/search', {method: 'POST', body: {...params, offset}});
    if (request !== searches) return;
    state.search = {params, offset, ...result};
    renderSearch();
  } finally {
    if (request === searches) results.removeAttribute('aria-busy');
  }
}

function renderSearch() {
  const {params, offset, total, items} = state.search;
  const rows = objects(items);
  const panel = $('search-results');
  const count = rows.length ? `显示 ${offset + 1}–${offset + rows.length}，共 ${numeric.format(total)} 项` : `共 ${numeric.format(total)} 项`;
  const previous = button('上一页', () => run(previous, () => search(params, Math.max(0, offset - PAGE_SIZE))), {disabled: offset === 0});
  const next = button('下一页', () => run(next, () => search(params, offset + PAGE_SIZE)), {disabled: offset + PAGE_SIZE >= total});
  panel.replaceChildren(element('div', {className: 'results-bar', children: [element('span', {text: count}), actions(previous, next)]}));
  if (!rows.length) {
    panel.append(empty('没有匹配的文件。'));
    return;
  }
  panel.append(element('ul', {className: 'result-list', children: rows.map((file) => {
    const detail = [size(file.size), modified(file)].filter(Boolean).join(' · ');
    const open = rowButton(file.relative_path, detail, null, () => run(open, async () => {
      updateUrl('files', file.id);
      await showFile(file.id);
      if (state.file?.id === file.id) $('file-heading').focus();
    }));
    return element('li', {children: [open]});
  })}));
}

const contentStatus = {ready: '可读取', unsupported: '此格式没有正文提取器', needs_ocr: '没有可提取文本，此版本尚未支持 OCR',
  no_text: '没有可提取文本', too_large: '文件超过正文提取大小上限', encrypted: 'PDF 已加密', parse_error: '提取正文失败'};

let fileRequest = 0;
let excerptRequest = 0;
async function showFile(id, evidence = null) {
  const request = ++fileRequest;
  $('file-card').hidden = false;
  $('file-detail').replaceChildren(empty('正在读取文件信息…'));
  state.file = null;
  state.fileEvidence = evidence;
  state.excerpt = null;
  let file;
  try {
    file = await api(path('files', id));
  } catch (error) {
    if (request === fileRequest) $('file-detail').replaceChildren(empty('无法读取文件信息。'));
    throw error;
  }
  if (request !== fileRequest) return;
  state.file = file;
  renderFile();
  if (file.present === false || (evidence && file.version !== evidence.version)) return;
  if (!file.content_status || file.content_status === 'ready') await loadExcerpt(0, request, evidence);
}

async function loadExcerpt(start, request = fileRequest, evidence = null) {
  const file = state.file;
  if (!file) return;
  const revision = ++excerptRequest;
  const excerpt = await getExcerpt(file.id, {start, evidence});
  if (request !== fileRequest || revision !== excerptRequest) return;
  if (excerpt.version !== file.version || (evidence && excerpt.version !== evidence.version)) {
    throw new Error('正文版本已变化。请重新打开文件，核对来源版本。');
  }
  state.excerpt = excerpt;
  renderFile();
}

function renderFile() {
  const {file, fileEvidence, excerpt} = state;
  const panel = element('div', {className: 'stack', children: [
    element('h4', {className: 'path', text: file.relative_path}),
    facts([['授权目录', rootName(file.root_id), 'path'], ['文件 ID', file.id, 'hash'], ['文件版本', file.version, 'hash'],
      ['大小', size(file.size)], ['修改时间', modified(file)], ['正文状态', contentStatus[file.content_status] ?? file.content_status]]),
  ]});
  if (file.present === false) panel.append(hint('此文件已不在当前索引中。', true));
  if (fileEvidence) {
    panel.append(facts([['证据记录版本', fileEvidence.version, 'hash'], ['证据记录位置', locationText(fileEvidence.location)]]));
    if (file.version !== fileEvidence.version) panel.append(hint('当前文件版本与证据记录不同。请核对原计划。', true));
    else if (evidenceStart(fileEvidence) == null) panel.append(hint('原证据记录未保存全文偏移，可按证据记录位置查找。'));
  }
  if (excerpt) {
    const locations = objects(excerpt.locations);
    const previous = button('上一段', () => run(previous, () => loadExcerpt(Math.max(0, excerpt.start - EXCERPT_SIZE))), {disabled: !excerpt.start});
    const next = button('下一段', () => run(next, () => loadExcerpt(excerpt.next_start)), {disabled: excerpt.next_start == null});
    const positions = (locations.length ? locations : [excerpt.location]).map(locationText).filter(Boolean).join('、');
    panel.append(facts([['来源位置', positions], ['字符范围', `${excerpt.start + 1}–${excerpt.end} / ${excerpt.total_chars}`]]),
      element('pre', {className: 'formatted excerpt', text: excerpt.text || '此范围没有正文。'}),
      actions(previous, next));
    if (excerpt.truncated) panel.append(hint('正文索引达到提取上限，后续内容可能未包含。', true));
  }
  $('file-detail').replaceChildren(panel);
}

function issueList(issues, scope = () => null) {
  return element('ul', {className: 'issues', children: issues.map((issue) =>
    element('li', {text: [scope(issue), issue.message, issue.code ? `（${issue.code}）` : ''].filter(Boolean).join(' ')}))});
}

function planList() {
  const list = $('plan-list');
  list.replaceChildren(...state.plans.map((plan) => rowButton(plan.title, `v${plan.version}${plan.created_at ? ` · ${time(plan.created_at)}` : ''}`,
    plan.status, () => run(null, () => openView('plans', plan.id, 'push')), state.plan?.id === plan.id)));
  if (!state.plans.length) list.append(empty('暂无计划。可在 dsh 聊天中请助手提出整理计划，再刷新这里。'));
}

async function loadPlans() {
  const payload = await api('/plans');
  state.plans = objects(payload.items);
  planList();
}

function editable(plan) {
  return plan.status === 'draft' || plan.status === 'approved';
}

function mayReplacePlan() {
  if (state.planBusy) return false;
  return !planChanged() || window.confirm('当前计划有未保存的修订。放弃这些修订？');
}

async function loadPlan(id) {
  if (state.planBusy) return;
  state.planBusy = true;
  const detail = $('plan-detail');
  detail.setAttribute('aria-busy', 'true');
  syncPlanControls();
  try {
    applyPlan(await api(path('plans', id)));
  } finally {
    state.planBusy = false;
    detail.removeAttribute('aria-busy');
    syncPlanControls();
  }
}

function applyPlan(plan, {keepValidation = false} = {}) {
  const validation = keepValidation && bindingMatches(state.validation, plan) ? state.validation : null;
  state.plan = plan;
  state.validation = validation;
  state.confirmed = false;
  state.edits = objects(plan.items).map((item) => ({item, included: true, target: item.target_path, root: item.target_root_id}));
  const listed = state.plans.findIndex((item) => item.id === plan.id);
  if (listed >= 0) state.plans[listed] = plan;
  else state.plans.unshift(plan);
  renderPlan();
  planList();
}

function planChanged() {
  return state.edits.some(({item, included, target, root}) => !included || target !== item.target_path || root !== item.target_root_id);
}

function itemPath(rootId, relativePath) {
  const root = state.roots.find((entry) => entry.id === rootId);
  return [root?.path || rootId, relativePath].filter(Boolean).join(' / ');
}

function renderEvidence(rawEvidence) {
  if (rawEvidence == null) return null;
  const references = Array.isArray(rawEvidence) ? rawEvidence : [rawEvidence];
  return element('div', {className: 'stack', children: references.map((evidence) => {
    if (!evidence?.file_id || !evidence.version) return element('pre', {className: 'formatted evidence', text: content(evidence)});
    const btn = button('打开证据文件', () => run(btn, async () => {
      activate('files');
      updateUrl('files', evidence.file_id);
      await showFile(evidence.file_id, evidence);
      $('file-heading').focus();
    }));
    const loc = locationText(evidence.location);
    return element('div', {className: 'evidence stack', children: [
      facts([['证据文件', evidence.file_id, 'hash'], ['文件版本', evidence.version, 'hash'], ['位置', loc]]), actions(btn),
    ]});
  })});
}

function fillRootInput(select, value) {
  const roots = [...state.roots];
  if (!roots.some((root) => root.id === value)) roots.unshift({id: value, label: value});
  select.replaceChildren(...roots.map((root) => element('option', {
    text: root.label && root.path && root.label !== root.path ? `${root.label} · ${root.path}` : (root.label || root.path), attrs: {value: root.id},
  })));
  select.value = value;
}

function renderPlanItem(edit, canEdit) {
  const {item} = edit;
  const source = itemPath(item.source_root_id, item.source_path ?? item.relative_path ?? item.file_id);
  const row = element('li', {className: 'item stack'});
  const header = element('div', {className: 'item-head', children: [element('h4', {className: 'path', text: source})]});
  row.append(header);
  if (canEdit) {
    const include = element('input', {attrs: {type: 'checkbox', 'data-edit': ''}});
    include.checked = edit.included;
    header.append(element('label', {children: [include, document.createTextNode('保留此项')]}));
    const select = element('select');
    fillRootInput(select, edit.root);
    const target = element('input', {attrs: {type: 'text', autocomplete: 'off', maxlength: '32000', spellcheck: 'false'}});
    target.value = edit.target;
    include.addEventListener('change', () => {
      edit.included = include.checked;
      row.classList.toggle('is-excluded', !edit.included);
      invalidateLocalValidation();
    });
    select.addEventListener('change', () => { edit.root = select.value; invalidateLocalValidation(); });
    target.addEventListener('input', () => { edit.target = target.value; invalidateLocalValidation(); });
    row.append(element('div', {className: 'form-grid', children: [
      element('label', {text: '目标目录', children: [select]}), element('label', {text: '目标相对路径', children: [target]}),
    ]}));
    edit.includeInput = include;
    edit.rootInput = select;
    edit.pathInput = target;
  } else {
    row.append(facts([['目标路径', itemPath(item.target_root_id, item.target_path), 'path']]));
  }
  row.append(facts([['整理理由', item.reason]]));
  const evidence = renderEvidence(item.evidence);
  if (evidence) row.append(evidence);
  return row;
}

function renderPlan() {
  const plan = state.plan;
  const canEdit = editable(plan);
  const detail = $('plan-detail');
  const heading = element('div', {className: 'detail-heading', children: [element('h3', {text: plan.title}), badge(plan.status)]});
  const header = element('div', {className: 'card-inset stack', children: [heading, facts([
    ['版本', `v${plan.version}`], ['哈希', plan.hash, 'hash'], ['计划 ID', plan.id, 'hash'], ['任务 ID', plan.task_id, 'hash'],
  ])]});
  if (plan.undo_of) header.append(hint('这是撤销计划，仍需校验、批准和执行。'));
  const items = element('div', {className: 'card-inset stack', children: [
    element('h4', {text: `移动与重命名 · ${state.edits.length} 项`}),
    element('ol', {className: 'item-list', children: state.edits.map((edit) => renderPlanItem(edit, canEdit))}),
  ]});
  detail.replaceChildren(header, items);
  if (!canEdit) {
    const report = button('查看执行报告', () => run(report, async () => {
      const match = state.jobs.find((job) => job.plan_id === plan.id) ?? objects((await api('/jobs')).items).find((job) => job.plan_id === plan.id);
      if (!match) throw new Error('未找到此计划对应的执行记录。');
      await openView('jobs', match.id, 'push');
    }));
    detail.append(element('div', {className: 'card-inset', children: [actions(report)]}));
    return;
  }
  const status = element('div', {attrs: {id: 'plan-validation', 'aria-live': 'polite'}});
  const controls = [
    ['save-revision', '保存修订', saveRevision], ['validate-plan', '校验计划', validatePlan],
  ].map(([id, title, callback]) => Object.assign(button(title, () => performPlan(callback)), {id}));
  const workflow = element('div', {className: 'card-inset stack', children: [actions(...controls), status]});
  if (plan.status === 'draft') {
    const confirmation = element('input', {attrs: {type: 'checkbox', id: 'confirm-plan'}});
    confirmation.addEventListener('change', () => {
      state.confirmed = confirmation.checked;
      syncPlanControls();
    });
    const approve = button('批准此版本', () => performPlan(approvePlan), {className: 'primary'});
    approve.id = 'approve-plan';
    workflow.append(element('label', {className: 'approval-check', children: [confirmation, document.createTextNode(`我已核对 v${plan.version}、上方哈希和所有保留条目。`)]}), actions(approve));
  } else {
    const execute = button('执行已批准版本', () => performPlan(executePlan), {className: 'primary'});
    execute.id = 'execute-plan';
    workflow.append(hint('执行会移动或重命名计划中的本机文件。'), actions(execute));
  }
  detail.append(workflow);
  syncPlanControls();
  showValidation();
}

function invalidateLocalValidation() {
  state.validation = null;
  state.confirmed = false;
  syncPlanControls();
  showValidation();
}

function syncPlanControls() {
  const dirty = planChanged();
  const valid = !dirty && state.validation?.valid === true && bindingMatches(state.validation, state.plan);
  for (const edit of state.edits) {
    if (!edit.includeInput) continue;
    edit.includeInput.disabled = state.planBusy;
    edit.rootInput.disabled = state.planBusy || !edit.included;
    edit.pathInput.disabled = state.planBusy || !edit.included;
  }
  const base = {'save-revision': !dirty || !state.edits.some((edit) => edit.included),
    'validate-plan': dirty, 'confirm-plan': !valid, 'approve-plan': !valid || !state.confirmed, 'execute-plan': !valid};
  for (const [id, disabled] of Object.entries(base)) {
    const control = $(id);
    if (control) control.disabled = state.planBusy || disabled;
  }
  if ($('confirm-plan')) $('confirm-plan').checked = state.confirmed;
}

function showValidation() {
  const box = $('plan-validation');
  if (!box) return;
  box.replaceChildren();
  if (planChanged()) {
    box.append(hint(state.edits.some((edit) => edit.included)
      ? '先保存修订，再校验新版本。' : '修订至少需要保留一个条目。', true));
  } else if (state.validation) {
    if (state.validation.valid) box.append(hint(`v${state.plan.version} 已通过校验。`));
    else {
      box.append(hint('校验发现冲突，请修订后重试。', true));
      if (state.validation.issues.length) box.append(issueList(state.validation.issues, (issue) => {
        const item = state.edits.find((edit) => edit.item.id === issue.item_id)?.item;
        return item ? `${item.source_path} → ${item.target_path}` : issue.item_id;
      }));
    }
  } else box.append(hint('校验会检查源文件版本、路径和目标冲突。'));
}

async function performPlan(callback) {
  if (state.planBusy || !state.plan || !editable(state.plan)) return;
  state.planBusy = true;
  syncPlanControls();
  $('plan-detail').setAttribute('aria-busy', 'true');
  await run(null, async () => {
    try {
      await callback(state.plan);
    } catch (error) {
      const issues = objects(error.detail?.issues);
      if (issues.length && $('plan-validation')) {
        state.validation = {id: state.plan.id, ...planBinding(state.plan), valid: false, issues};
        state.confirmed = false;
        showValidation();
      } else if (error.detail?.code === 'stale_plan' || error.detail?.code === 'plan_locked') {
        invalidateLocalValidation();
      }
      throw error;
    }
  });
  state.planBusy = false;
  $('plan-detail').removeAttribute('aria-busy');
  syncPlanControls();
}

async function saveRevision(plan) {
  if (!planChanged()) return;
  const items = state.edits.filter(({included}) => included).map(({item, target, root}) => ({
    file_id: item.file_id, target_root_id: root, target_path: target, reason: item.reason, evidence: item.evidence,
  }));
  if (!items.length || items.some((item) => !item.target_path.trim() || !item.target_root_id)) throw new Error('请为每个保留条目填写目标路径和目录。');
  const revised = await api(`${path('plans', plan.id)}/revise`, {method: 'POST', body: {version: plan.version, items}});
  applyPlan(revised);
  feedback(`已保存修订 · v${revised.version} 待审核。`);
}

async function validatePlan(plan) {
  if (planChanged()) return;
  const result = await api(`${path('plans', plan.id)}/validate`, {method: 'POST'});
  const latest = await api(path('plans', plan.id));
  if (!bindingMatches(plan, latest) || plan.status !== latest.status) {
    applyPlan(latest);
    throw new Error('计划已更新。请核对当前版本后重新校验。');
  }
  state.validation = {id: plan.id, ...planBinding(plan), valid: result.valid === true, issues: objects(result.issues)};
  state.confirmed = false;
  showValidation();
}

function readyForApproval(plan) {
  return !planChanged() && state.validation?.valid && bindingMatches(state.validation, plan);
}

async function approvePlan(plan) {
  if (!readyForApproval(plan) || plan.status !== 'draft' || !state.confirmed) return;
  const approved = await api(`${path('plans', plan.id)}/approve`, {method: 'POST', body: planBinding(plan)});
  if (!bindingMatches(approved, plan) || approved.status !== 'approved') throw new Error('服务未确认已批准当前版本，请刷新计划。');
  applyPlan(approved, {keepValidation: true});
  feedback(`v${approved.version} 已批准。可单独点击执行。`);
}

async function executePlan(plan) {
  if (!readyForApproval(plan) || plan.status !== 'approved') return;
  const job = await api(`${path('plans', plan.id)}/execute`, {method: 'POST', body: planBinding(plan)});
  applyPlan({...plan, status: job.status || 'submitted'});
  feedback(`已提交 v${plan.version} 的执行任务。`);
  await openView('jobs', job.id, 'push');
}

function jobList() {
  const list = $('job-list');
  list.replaceChildren(...state.jobs.map((job) => {
    const title = state.plans.find((plan) => plan.id === job.plan_id)?.title ?? `任务 ${String(job.id).slice(0, 8)}`;
    return rowButton(title, [`v${job.version ?? job.plan_version}`, time(job.created_at)].filter(Boolean).join(' · '), job.status,
      () => run(null, () => openView('jobs', job.id, 'push')), state.job?.id === job.id);
  }));
  if (!state.jobs.length) list.append(empty('暂无执行记录。'));
}

async function loadJobs() {
  const payload = await api('/jobs');
  state.jobs = objects(payload.items);
  jobList();
}

const pollingStatuses = new Set(['queued', 'running', 'executing']);
const recoverableStatuses = new Set(['running', 'executing', 'needs_recovery', 'recovery_required']);
let jobRequest = 0;

function schedulePoll() {
  window.clearTimeout(state.poll);
  state.poll = null;
  const panelHidden = window.frameElement && window.frameElement.getClientRects().length === 0;
  if (!state.job || state.jobBusy || state.view !== 'jobs' || document.hidden || panelHidden || !pollingStatuses.has(state.job.status)) return;
  const id = state.job.id;
  state.poll = window.setTimeout(async () => {
    try {
      await loadJob(id, {automatic: true});
    } catch (error) {
      feedback(`执行状态更新暂停，点击刷新重试。${error.message}`, true);
    }
  }, POLL_INTERVAL);
}

async function loadJob(id, {automatic = false} = {}) {
  const request = ++jobRequest;
  window.clearTimeout(state.poll);
  const detail = $('job-detail');
  if (!automatic) detail.setAttribute('aria-busy', 'true');
  try {
    const job = await api(path('jobs', id));
    if (request !== jobRequest) return;
    const changed = JSON.stringify(job) !== JSON.stringify(state.job);
    state.job = job;
    const position = state.jobs.findIndex((item) => item.id === job.id);
    if (position >= 0) state.jobs[position] = job;
    else state.jobs.unshift(job);
    if (changed || !automatic) {
      renderJob();
      jobList();
    }
    schedulePoll();
  } finally {
    if (request === jobRequest) detail.removeAttribute('aria-busy');
  }
}

function renderJob() {
  const job = state.job;
  const panel = $('job-detail');
  const operations = objects(job.items);
  const events = objects(job.events);
  const operationNumbers = new Map(operations.map((item, index) => [item.id, item.position == null ? index + 1 : item.position + 1]));
  const totalByStatus = new Map();
  for (const item of operations) totalByStatus.set(item.status, (totalByStatus.get(item.status) ?? 0) + 1);
  const counts = [...totalByStatus].map(([status, count]) => `${labels[status] ?? status} ${count}`).join(' · ');
  const plan = button('查看计划', () => run(plan, () => openView('plans', job.plan_id, 'push')), {disabled: state.jobBusy});
  const controls = actions(plan);
  if (pollingStatuses.has(job.status)) {
    controls.append(button(job.cancel_requested ? '已请求取消' : '请求取消', () => changeJob('cancel'),
      {className: 'danger', disabled: state.jobBusy || job.cancel_requested}));
  }
  if (recoverableStatuses.has(job.status)) {
    controls.append(button('核对并恢复状态', () => changeJob('recover'), {disabled: state.jobBusy}));
  }
  if (!pollingStatuses.has(job.status) && !recoverableStatuses.has(job.status) && operations.some((item) => item.status === 'completed')) {
    controls.append(button('生成撤销计划', () => createUndo(job), {disabled: state.jobBusy}));
  }
  const header = element('div', {className: 'card-inset stack', children: [
    element('div', {className: 'detail-heading', children: [element('h3', {text: '执行详情'}), badge(job.status)]}),
    facts([['执行 ID', job.id, 'hash'], ['计划 ID', job.plan_id, 'hash'], ['计划版本', job.version ?? job.plan_version],
      ['哈希', job.hash ?? job.plan_hash, 'hash'], ['创建时间', time(job.created_at)], ['结束时间', time(job.finished_at)], ['进度', counts]]), controls,
  ]});
  if (job.cancel_requested && pollingStatuses.has(job.status)) header.append(hint('取消请求将在当前操作结束后处理。', true));
  if (job.status === 'needs_recovery' || job.status === 'recovery_required') header.append(hint('有操作需要核对源与目标文件。请查看逐项结果。', true));
  panel.replaceChildren(header);
  if (operations.length) panel.append(element('section', {className: 'card-inset stack', children: [
    element('h4', {text: '逐项结果'}), element('ol', {className: 'item-list', children: operations.map((record) => {
      const item = record.item ?? record;
      return element('li', {className: 'item stack', children: [
        element('div', {className: 'item-head', children: [element('strong', {text: `条目 ${operationNumbers.get(record.id)}`}), badge(record.status)]}),
        facts([['源路径', itemPath(item.source_root_id, item.source_path), 'path'],
          ['目标路径', itemPath(item.target_root_id, item.target_path), 'path'], ['结果', record.message], ['代码', record.code, 'path']]),
      ]});
    })}),
  ]}));
  if (events.length) panel.append(element('section', {className: 'card-inset stack', children: [
    element('h4', {text: '操作事件'}), element('ol', {className: 'item-list', children: events.map((event) => element('li', {className: 'item stack', children: [
      element('div', {className: 'item-head', children: [hint(time(event.created_at)), badge(event.status)]}),
      facts([['条目', operationNumbers.get(event.operation_id)], ['事件', event.message], ['代码', event.code, 'path']]),
    ]}))}),
  ]}));
}

async function performJob(callback) {
  if (!state.job || state.jobBusy) return;
  state.jobBusy = true;
  ++jobRequest;
  schedulePoll();
  const job = state.job;
  renderJob();
  await run(null, () => callback(job));
  state.jobBusy = false;
  renderJob();
  schedulePoll();
}

async function changeJob(action) {
  await performJob(async (job) => {
    await api(`${path('jobs', job.id)}/${action}`, {method: 'POST'});
    feedback(action === 'cancel' ? '取消请求已提交。' : '已核对执行状态。');
    if (state.job.id === job.id) await loadJob(job.id);
  });
}

async function createUndo(job) {
  if (!mayReplacePlan()) return;
  state.planBusy = true;
  syncPlanControls();
  try {
    await performJob(async () => {
      const undo = await api(`${path('plans', job.plan_id)}/undo`, {method: 'POST'});
      applyPlan(undo);
      activate('plans');
      updateUrl('plans', undo.id);
      feedback('已生成待审核的撤销计划。');
      await loadPlans();
    });
  } finally {
    state.planBusy = false;
    syncPlanControls();
  }
}

function init() {
  for (const link of document.querySelectorAll('[data-view]')) link.addEventListener('click', (event) => {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
    event.preventDefault();
    void run(null, () => openView(link.dataset.view, null, 'push'));
  });
  window.addEventListener('popstate', () => {
    const {view, id} = currentRoute();
    void run(null, () => openView(view, id));
  });
  document.addEventListener('visibilitychange', schedulePoll);
  window.addEventListener('filemate-panel-visibility', schedulePoll);
  $('refresh-roots').addEventListener('click', (event) => run(event.currentTarget, loadRoots));
  $('root-form').addEventListener('submit', (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    void run(event.submitter, async () => {
      const root = await api('/roots', {method: 'POST', body: {path: values.get('path'), label: values.get('label') || undefined}});
      form.reset();
      feedback(`目录已登记 · ${root.label || root.path}`);
      await loadRoots();
      $('search-root').value = root.id;
    });
  });
  $('search-form').addEventListener('submit', (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    void run(event.submitter ?? $('search-submit'), () => search(searchParameters(form)));
  });
  $('close-file').addEventListener('click', () => {
    ++fileRequest;
    state.file = null;
    state.fileEvidence = null;
    state.excerpt = null;
    $('file-card').hidden = true;
    updateUrl('files', null);
  });
  $('refresh-plans').addEventListener('click', (event) => run(event.currentTarget, async () => {
    if (!mayReplacePlan()) return;
    await Promise.all([loadPlans(), state.plan ? loadPlan(state.plan.id) : null]);
  }));
  $('refresh-jobs').addEventListener('click', (event) => run(event.currentTarget, () => Promise.all([loadJobs(), state.job ? loadJob(state.job.id) : null])));
  const {view, id} = currentRoute();
  void run(null, async () => {
    try {
      await loadRoots();
    } finally {
      await openView(view, id);
    }
  });
}

if (typeof document !== 'undefined') init();
