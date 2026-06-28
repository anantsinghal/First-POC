const $ = (s, root=document) => root.querySelector(s);
const api = async (url, options = {}) => { const response = await fetch(url, options); if (!response.ok) throw new Error((await response.json().catch(()=>({detail:response.statusText}))).detail); return response.json(); };

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
function showFindings(findings) { const target = $('#findings'); $('#findingCount').textContent = `${findings.length} finding${findings.length === 1 ? '' : 's'}`; target.innerHTML = ''; if (!findings.length) { $('#findingTypes').innerHTML = ''; return target.innerHTML = '<p>No findings in this scan.</p>'; } const template = $('#findingTemplate'); for (const item of findings) { const node = template.content.cloneNode(true); const badge = $('.badge', node); badge.textContent = item.severity; badge.classList.add(item.severity); $('.type-tag', node).textContent = humanizeType(item.finding_type); $('h3', node).textContent = item.title; $('.score', node).textContent = `${Math.round(item.confidence * 100)}% confidence`; $('.record', node).textContent = `${item.object_name} · ${item.record_name} (${item.record_id})`; $('.why', node).textContent = item.evidence.why || 'Review the supplied evidence.'; $('pre', node).textContent = JSON.stringify({ evidence:item.evidence, fingerprint:item.fingerprint, scan_id:item.scan_id, status:item.status }, null, 2); for (const button of node.querySelectorAll('[data-label]')) button.onclick = async () => { try { await api(`/api/findings/${item.id}/feedback`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({label:button.dataset.label}) }); setMessage('Review label saved.'); await refresh(); } catch (e) { setMessage(e.message, true); } }; target.append(node); } const counts = {}; for (const f of findings) counts[f.finding_type] = (counts[f.finding_type]||0) + 1; const chips = Object.entries(counts).sort((a,b)=>b[1]-a[1]).map(([t,c]) => `<span class="type-chip">${humanizeType(t)} ×${c}</span>`).join(''); $('#findingTypes').innerHTML = chips; }

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
  target.innerHTML =
    chip('Findings', m.findings_total) +
    chip('Open', m.open_findings) +
    chip('Suppressed', m.suppressed) +
    chip('Reviewed', m.feedback_total) +
    chip('Acceptance', pct(m.acceptance_rate)) +
    chip('False-positive', pct(m.false_positive_rate)) +
    chip('High', sev.high || 0) + chip('Medium', sev.medium || 0) + chip('Low', sev.low || 0);
}

async function refresh() {
  const dashboard = await api('/api/dashboard');
  const scan = dashboard.latest_scan; 
  $('#stats').innerHTML = `<div class="stat"><span>Completed scans</span><strong>${dashboard.total_scans}</strong></div><div class="stat"><span>Open findings</span><strong>${dashboard.open_findings}</strong></div><div class="stat"><span>Latest coverage</span><strong>${scan?.coverage?.grade?.toUpperCase() || '—'}</strong></div>`; 
  coverage(scan); 
  showFindings(scan ? await api(`/api/findings?scan_id=${scan.id}`) : []); 
  const scans = await api('/api/scans'); 
  $('#scans').innerHTML = scans.length ? scans.map(s => `<div class="scan"><span>${s.object_name} · ${s.source_name}</span><span>${s.row_count} records · ${new Date(s.created_at).toLocaleString()}</span></div>`).join('') : '<p>No scan manifests yet.</p>'; 
  // new panels
  try { showMetrics(await api('/api/metrics')); } catch (e) { const t = $('#metrics'); if (t) t.innerHTML = ''; }
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

refresh().catch(e => setMessage(e.message, true));
