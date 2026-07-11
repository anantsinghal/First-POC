const $ = (s, root=document) => root.querySelector(s);
const showAuthBar = () => { const bar = document.getElementById('authBar'); if (bar) bar.hidden = false; };
const hideAuthBar = () => { const bar = document.getElementById('authBar'); if (bar) bar.hidden = true; };
const apiRaw = async (url, options = {}) => {
  const headers = { ...(options.headers || {}) };
  const key = localStorage.getItem('scanner_api_key');
  if (key) headers['X-API-Key'] = key;
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401) {
    showAuthBar();
    const detail = (await response.json().catch(() => ({ detail: response.statusText }))).detail;
    const err = new Error(detail || 'Missing or invalid API key');
    err.status = 401;
    throw err;
  }
  if (!response.ok) throw new Error((await response.json().catch(()=>({detail:response.statusText}))).detail);
  return response;
};
const api = async (url, options = {}) => (await apiRaw(url, options)).json();

const TYPE_LABELS = {
  missing_required:'Missing required field', invalid_stage:'Invalid stage',
  missing_account:'Missing account', missing_contract_closed_won:'Closed Won · no contract',
  non_positive_amount:'Non-positive amount', numeric_outlier:'IQR outlier',
  robust_outlier:'Robust (MAD) outlier', rare_combination:'Rare combination',
  stale_open_opportunity:'Stale open opportunity', duplicate_name:'Possible duplicate',
  missing_close_date:'Missing close date',
  pattern_break:'Learned-pattern break',
  ml_anomaly:'ML anomaly (Isolation Forest)',
  picklist_invalid:'Invalid picklist value',
  reference_missing:'Missing required reference',
  required_missing:'Required field empty',
  invalid_format:'Invalid format',
  orphan_reference:'Orphan reference',
  missing_children:'Missing related records',
};
const humanizeType = (t) => TYPE_LABELS[t] || t.replace(/_/g,' ').replace(/\b\w/g, c=>c.toUpperCase());

function setMessage(text, failure=false) { const message = $('#message'); message.textContent = text; message.style.color = failure ? 'var(--bad)' : 'var(--good)'; }
function coverage(scan) { const c = scan?.coverage; if (!c) return $('#coverage').innerHTML = ''; $('#coverage').className = `coverage ${c.grade}`; $('#coverage').innerHTML = `<strong>Coverage: ${c.grade.toUpperCase()}</strong><p>${c.reason}</p><small>${c.observed_records} records · ${c.queried_fields.length} fields · Missing: ${c.missing_fields.join(', ') || 'none'}</small>${c.suppressed_checks.length ? `<p><small>Suppressed checks: ${c.suppressed_checks.join('; ')}</small></p>` : ''}`; }
const FINDINGS_PAGE_SIZE = 200;
let findingsState = { scanId: null, offset: 0, total: 0, loaded: [] };

async function fetchFindingsPage(scanId, offset) {
  const response = await apiRaw(`/api/findings?scan_id=${scanId}&limit=${FINDINGS_PAGE_SIZE}&offset=${offset}`);
  const items = await response.json();
  const total = Number(response.headers.get('X-Total-Count') || items.length);
  return { items, total };
}

function renderFindingCard(item) {
  const template = $('#findingTemplate');
  const node = template.content.cloneNode(true);
  const badge = $('.badge', node); badge.textContent = item.severity; badge.classList.add(item.severity);
  $('.type-tag', node).textContent = humanizeType(item.finding_type);
  $('h3', node).textContent = item.title;
  const score = $('.score', node); score.textContent = `${Math.round(item.confidence * 100)}% confidence`;
  if (item.rank_score !== undefined && item.rank_score !== item.priority) {
    const arrow = item.rank_score < item.priority ? '▼' : '▲';
    score.insertAdjacentHTML('beforeend', ` <span class="rank-tag">rank ${Number(item.rank_score).toFixed(2)} ${arrow}</span>`);
  }
  if (item.ranking_basis === 'model' && typeof item.model_p_accept === 'number') {
    const modelPct = Math.round(item.model_p_accept * 100);
    const factors = (item.model_factors || []).map(f => `${f.feature} ${f.contribution >= 0 ? '+' : ''}${Number(f.contribution).toFixed(2)}`).join(', ').replace(/"/g, '&quot;');
    score.insertAdjacentHTML('beforeend', ` <span class="model-chip" title="${factors}">model: ${modelPct}% real</span>`);
  }
  $('.record', node).textContent = `${item.object_name} · ${item.record_name} (${item.record_id})`;
  $('.why', node).textContent = item.evidence.why || 'Review the supplied evidence.';
  $('pre', node).textContent = JSON.stringify({ evidence:item.evidence, fingerprint:item.fingerprint, scan_id:item.scan_id, status:item.status }, null, 2);
  for (const button of node.querySelectorAll('[data-label]')) {
    button.onclick = async () => {
      try {
        await api(`/api/findings/${item.id}/feedback`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({label:button.dataset.label}) });
        setMessage('Review label saved.');
        await refresh();
      } catch (e) { setMessage(e.message, true); }
    };
  }
  return node;
}

function renderTypeChips(findings) {
  const counts = {};
  for (const f of findings) counts[f.finding_type] = (counts[f.finding_type]||0) + 1;
  $('#findingTypes').innerHTML = Object.entries(counts).sort((a,b)=>b[1]-a[1]).map(([t,c]) => `<span class="type-chip">${humanizeType(t)} ×${c}</span>`).join('');
}

function ensureLoadMoreButton() {
  if (findingsState.loaded.length >= findingsState.total) return;
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'load-more';
  btn.textContent = `Load more findings (${findingsState.total - findingsState.loaded.length} remaining)`;
  btn.onclick = loadMoreFindings;
  $('#findings').append(btn);
}

function showFindings(findings, total, { append = false } = {}) {
  const target = $('#findings');
  if (!append) {
    target.innerHTML = '';
    findingsState.loaded = [];
  } else {
    const existingBtn = $('#findings .load-more');
    if (existingBtn) existingBtn.remove();
  }
  findingsState.total = total;
  if (!append && !findings.length) {
    $('#findingCount').textContent = '0 findings';
    $('#findingTypes').innerHTML = '';
    target.innerHTML = '<p>No findings in this scan.</p>';
    return;
  }
  for (const item of findings) target.append(renderFindingCard(item));
  findingsState.loaded.push(...findings);
  $('#findingCount').textContent = findingsState.loaded.length < findingsState.total
    ? `showing ${findingsState.loaded.length} of ${findingsState.total}`
    : `${findingsState.loaded.length} finding${findingsState.loaded.length === 1 ? '' : 's'}`;
  renderTypeChips(findingsState.loaded);
  ensureLoadMoreButton();
}

async function loadMoreFindings() {
  if (!findingsState.scanId) return;
  try {
    const { items, total } = await fetchFindingsPage(findingsState.scanId, findingsState.offset);
    findingsState.offset += items.length;
    showFindings(items, total, { append: true });
  } catch (e) { setMessage(e.message, true); }
}

function showCatalog(schema) {
  const target = $('#catalog');
  if (!target) return;
  if (!schema || !schema.fields || !schema.fields.length) {
    target.innerHTML = '<p>Run a Salesforce scan to populate the schema.</p>';
    return;
  }
  let html = `<strong>${schema.object_name || 'Object'}</strong>`;
  for (const f of schema.fields) {
    let extra = '';
    if (f.required) extra += ' · required';
    if (f.picklist_values && f.picklist_values.length) extra += ` · picklist:${f.picklist_values.length}`;
    if (f.reference_to && f.reference_to.length) extra += ` · ref→${f.reference_to.join(',')}`;
    html += `<div class="catalog-row">${f.name} · ${f.data_type}${extra}</div>`;
  }
  target.innerHTML = html;
}

function showAudit(entries) {
  const target = $('#audit');
  if (!target) return;
  if (!entries || !entries.length) {
    target.innerHTML = '<p>No audit entries yet.</p>';
    return;
  }
  target.innerHTML = entries.map(a => {
    const ts = a.ts ? new Date(a.ts).toLocaleString() : '';
    const detail = a.detail ? JSON.stringify(a.detail) : '';
    return `<div class="audit-row"><span>${ts}</span> <strong>${a.event || ''}</strong> <small>${detail}</small></div>`;
  }).join('');
}

function showMetrics(m) {
  const target = $('#metrics');
  if (!target) return;
  if (!m || !m.findings_total) { target.innerHTML = '<p>Run a scan to populate metrics.</p>'; return; }
  const pct = (x) => x == null ? '—' : `${Math.round(x * 100)}%`;
  const sev = m.by_severity || {};
  const chip = (label, val) => `<span class="metric-chip">${label}: <strong>${val}</strong></span>`;
  let html =
    chip('Findings', m.findings_total) +
    chip('Open', m.open_findings) +
    chip('Suppressed', m.suppressed) +
    chip('Reviewed', m.feedback_total) +
    chip('Acceptance', pct(m.acceptance_rate)) +
    chip('False-positive', pct(m.false_positive_rate)) +
    chip('High', sev.high || 0) + chip('Medium', sev.medium || 0) + chip('Low', sev.low || 0);
  const acceptanceEntries = Object.entries(m.type_acceptance || {}).slice(0, 6);
  if (acceptanceEntries.length) {
    html += acceptanceEntries.map(([t, a]) => chip(`Acc[${humanizeType(t)}]`, `${Math.round((a.acceptance || 0) * 100)}% (${a.labels})`)).join('');
  }
  target.innerHTML = html;
}

function showLearning(status) {
  const target = $('#learning');
  if (!target) return;
  if (!status) { target.innerHTML = ''; return; }
  const model = status.model;
  if (!model) {
    target.innerHTML =
      `<p class="learning-line">Ranking mode: heuristic (learned model not active yet).</p>` +
      `<p class="learning-line">Labels: <strong>${status.n_labels}</strong> (real_issue <strong>${status.n_pos}</strong> · other <strong>${status.n_neg}</strong>)</p>` +
      `<p class="learning-line">Ready to train: <strong>${status.ready ? 'yes' : 'no'}</strong></p>`;
    return;
  }
  const pct = (x) => typeof x === 'number' ? `${Math.round(x * 100)}%` : '—';
  const trainedAt = model.trained_at ? new Date(model.trained_at).toLocaleString() : '—';
  const metrics = model.metrics || {};
  target.innerHTML =
    `<p class="learning-line">Ranking mode: <strong>LEARNED MODEL</strong> — trained on ${model.n_labels} labels on ${trainedAt}.</p>` +
    `<p class="learning-line">Holdout accuracy: <strong>${pct(metrics.accuracy)}</strong> · precision: <strong>${pct(metrics.precision)}</strong> · recall: <strong>${pct(metrics.recall)}</strong> · ROC AUC: <strong>${pct(metrics.roc_auc)}</strong></p>`;
}

function showQuality(quality) {
  const target = $('#quality');
  if (!target) return;
  if (!quality) {
    target.innerHTML = '<p>Run a scan to populate.</p>';
    return;
  }
  const score = quality.quality_score == null ? '—' : `${quality.quality_score}%`;
  const grade = quality.coverage_grade ? quality.coverage_grade.toUpperCase() : '—';
  const sampled = quality.sampled ? ' (sampled)' : '';
  let trend = '—';
  if (typeof quality.quality_delta === 'number') {
    trend = quality.quality_delta > 0 ? '▲' : quality.quality_delta < 0 ? '▼' : '—';
  }
  target.innerHTML = `<div class="quality-score">Quality: ${score} ${trend}</div><p class="quality-meta">Coverage: ${grade}${sampled}</p>`;
}

function showDrift(drift) {
  const target = $('#drift');
  if (!target) return;
  if (!drift) {
    target.innerHTML = '<p>Run two scans of the same object to compare.</p>';
    return;
  }
  if (drift.drift_count === 0) {
    target.innerHTML = `<p>No drift detected${drift.baseline_scan_id ? ' vs baseline' : ' (no previous scan of this object)'}.</p>`;
    return;
  }
  target.innerHTML = (drift.drifts || []).map(item => {
    const whyText = item.why || '';
    const why = whyText.length > 120 ? `${whyText.slice(0, 117)}...` : whyText;
    return `<div class="drift-row"><strong>${item.kind}</strong> ${item.field} · ${why}</div>`;
  }).join('');
}

function clearDrift() {
  const target = $('#drift');
  if (target) target.innerHTML = '';
}

async function refresh() {
  const dashboard = await api('/api/dashboard');
  const scan = dashboard.latest_scan;
  $('#stats').innerHTML = `<div class="stat"><span>Completed scans</span><strong>${dashboard.total_scans}</strong></div><div class="stat"><span>Open findings</span><strong>${dashboard.open_findings}</strong></div><div class="stat"><span>Latest coverage</span><strong>${scan?.coverage?.grade?.toUpperCase() || '—'}</strong></div>`;
  coverage(scan);
  try { showQuality(scan ? await api(`/api/scans/${scan.id}/quality`) : null); } catch (e) { showQuality(null); }
  if (scan) {
    try { showDrift(await api(`/api/scans/${scan.id}/drift`)); } catch (e) { clearDrift(); }
  } else {
    showDrift(null);
  }
  if (scan) {
    const { items, total } = await fetchFindingsPage(scan.id, 0);
    findingsState.scanId = scan.id;
    findingsState.offset = items.length;
    showFindings(items, total);
  } else {
    findingsState = { scanId: null, offset: 0, total: 0, loaded: [] };
    showFindings([], 0);
  }
  const scans = await api('/api/scans');
  $('#scans').innerHTML = scans.length ? scans.map(s => `<div class="scan"><span>${s.object_name} · ${s.source_name}</span><span>${s.row_count} records · ${new Date(s.created_at).toLocaleString()}</span></div>`).join('') : '<p>No scan manifests yet.</p>';
  try { showMetrics(await api('/api/metrics')); } catch (e) { const t = $('#metrics'); if (t) t.innerHTML = ''; }
  try { showLearning(await api('/api/learning/status')); } catch (e) { const t = $('#learning'); if (t) t.innerHTML = ''; }
  showCatalog(scan && scan.schema);
  try {
    const audits = await api('/api/audit?limit=20');
    showAudit(audits);
  } catch (e) {
    const t = $('#audit'); if (t) t.innerHTML = '<p>No audit entries yet.</p>';
  }
}
$('#demo').onclick = async () => { try { setMessage('Running manifest-backed demo scan…'); const scan = await api('/api/scans/demo', {method:'POST'}); setMessage(`Scan completed: ${scan.finding_count} candidate occurrences generated.`); await refresh(); } catch(e) { setMessage(e.message, true); } };
$('#upload').onsubmit = async (event) => { event.preventDefault(); try { const form = new FormData(event.currentTarget); const object = form.get('object_name'); form.delete('object_name'); setMessage('Uploading and scanning…'); const scan = await api(`/api/scans/upload?object_name=${encodeURIComponent(object)}`, {method:'POST', body:form}); setMessage(`Scan completed: ${scan.finding_count} candidate occurrences generated.`); await refresh(); } catch(e) { setMessage(e.message, true); } };

$('#multiUpload').onsubmit = async (event) => {
  event.preventDefault();
  try {
    const formEl = event.currentTarget;
    const primary = (formEl.querySelector('input[name="primary_object"]') || {}).value || 'Account';
    const fileInput = formEl.querySelector('input[name="files"]');
    const fd = new FormData();
    if (fileInput && fileInput.files) {
      for (const f of fileInput.files) fd.append('files', f);
    }
    setMessage('Uploading multi-object scan…');
    const scan = await api(`/api/scans/multi?primary_object=${encodeURIComponent(primary)}`, {method:'POST', body:fd});
    setMessage(`Scan completed: ${scan.finding_count} candidate occurrences generated.`);
    await refresh();
  } catch(e) { setMessage(e.message, true); }
};

$('#dbUpload').onsubmit = async (event) => {
  event.preventDefault();
  try {
    const form = new FormData(event.currentTarget);
    const table = form.get('table_name');
    const stream = form.get('stream');
    form.delete('table_name');
    form.delete('stream');
    setMessage('Uploading and scanning...');
    const path = stream ? '/api/scans/database/stream' : '/api/scans/database';
    const scan = await api(`${path}?table_name=${encodeURIComponent(table)}`, {method:'POST', body:form});
    setMessage(`Scan completed: ${scan.finding_count} candidate occurrences generated.`);
    await refresh();
  } catch(e) { setMessage(e.message, true); }
};

$('#trainModel').onclick = async () => {
  try {
    setMessage('Training…');
    const result = await api('/api/learning/train', { method: 'POST' });
    if (result.trained) {
      const acc = result.metrics && typeof result.metrics.accuracy === 'number' ? ` (acc ${Math.round(result.metrics.accuracy * 100)}%)` : '';
      setMessage(`Model trained on ${result.n_labels} labels${acc}.`);
    } else {
      setMessage(result.reason || 'Training did not produce a model.', true);
    }
    await refresh();
  } catch (e) { setMessage(e.message, true); }
};

const saveApiKeyBtn = document.getElementById('saveApiKey');
if (saveApiKeyBtn) {
  saveApiKeyBtn.onclick = () => {
    const input = document.getElementById('apiKeyInput');
    const val = input ? input.value.trim() : '';
    if (val) localStorage.setItem('scanner_api_key', val);
    else localStorage.removeItem('scanner_api_key');
    hideAuthBar();
    refresh().catch(e => {
      if (e && e.status === 401) { showAuthBar(); return; }
      setMessage(e.message, true);
    });
  };
}
refresh().catch(e => {
  if (e && e.status === 401) { showAuthBar(); return; }
  setMessage(e.message, true);
});
