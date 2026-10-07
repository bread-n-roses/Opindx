// Journal popup presentation. All values from data are escaped before rendering.
import * as E from './engine.js';

export const escape = value => String(value ?? '').replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (value, digits = 0) => E.isNumber(value)
  ? value.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '—';
const fields = value => [...new Set(String(value ?? '').split('|').map(s => s.trim()).filter(Boolean))];
const registerUrl = id => `https://kanalregister.hkdir.no/en/tidsskrift?id=${encodeURIComponent(id)}`;
const link = (url, label, title = '') => `<a href="${escape(url)}" target="_blank" rel="noopener noreferrer"${title ? ` aria-label="${escape(title)}"` : ''}>${escape(label)}</a>`;

export function registerIds(row) {
  if (!row.in_n) return [];
  return [...new Set(String(row.norwegian_register_url ?? '').split(';').flatMap(text => {
    try {
      const url = new URL(text.trim()), id = url.searchParams.get('id');
      return url.hostname === 'kanalregister.hkdir.no' && /^\d+$/.test(id ?? '') ? [id] : [];
    } catch { return []; }
  }))];
}

export function registerEntries(row, detail) {
  const allowed = new Set(registerIds(row));
  try {
    const entries = JSON.parse(detail?.norwegian_entries_json || '[]');
    if (!Array.isArray(entries)) return [];
    return entries.filter(e => e && allowed.has(e.id) && typeof e.field === 'string');
  } catch { return []; }
}

export function registerLinks(row, detail) {
  const ids = registerIds(row);
  if (!ids.length) return 'Not in Norwegian Register';
  const entries = registerEntries(row, detail);
  const idLink = id => link(registerUrl(id), id,
    `Norwegian Register entry ${id}${entries.find(e => e.id === id)?.title ? ': ' + entries.find(e => e.id === id).title : ''} (new tab)`);
  const primary = ids.length === 1 ? ids[0] : ids.includes(detail?.norwegian_primary_id) ? detail.norwegian_primary_id : null;
  if (!primary) return `Register entries: ${ids.map(idLink).join(', ')}`;
  const others = ids.filter(id => id !== primary);
  return link(registerUrl(primary), 'Journal page', 'Norwegian Register journal page (new tab)') +
    ` for ID: ${escape(primary)}` + (others.length ? `<br><span class="register-alternates">Other register IDs: ${others.map(idLink).join(', ')}</span>` : '');
}

export function metadataRows(row, state = {universe:'oa',treatment:'filtered'}) {
  const issns = [...new Set(String(row.issns ?? '').split(/[;,]/).map(s => s.trim()).filter(Boolean))].join(', ');
  const result = [
    ['ISSN / Publisher', `${escape(issns || 'Unavailable')} / ${escape(row.publisher || 'Unavailable')}`],
    ['OpenAlex details', link(`https://openalex.org/${encodeURIComponent(row.openalex_id)}`, 'Journal page', 'OpenAlex journal page (new tab)') + ` for ID: ${escape(row.openalex_id)}`],
  ];
  result.push(['Norwegian Register details', `<span id="register-details">${registerLinks(row)}</span>`]);
  result.push(
    ['Open access journal', row.is_open_access == null ? 'Unknown' : row.is_open_access ? 'Yes' : 'No'],
    ['Publications recorded', `${fmt(row.publications_raw)} &middot; Used in ${escape(state.treatment)} metrics: ${fmt(E.usedCount(row, state, 'publications'))}`],
    ['Citations recorded', `${fmt(row.citations_raw)} &middot; Used in ${escape(state.treatment)} metrics: ${fmt(E.usedCount(row, state, 'citations'))}`],
    ['Publication years', `${fmt(row.active_years)} of 5 with eligible output`],
    ['Publications w/out references', E.isNumber(E.withoutReferences(row)) ? `${fmt(E.withoutReferences(row), 2)}%` : '—'],
  );
  return result;
}

export function fieldsTable(row, detail, year) {
  const parts = E.fieldBreakdown(detail);
  const oaHeading = `OpenAlex primary fields of ${year - 5}–${year - 1} publications`;
  let html = '<table class="fields-table"><thead><tr>' +
    `<th scope="col" class="align-left">${oaHeading}</th><th scope="col">${parts?.length ? '100%' : 'Unavailable'}</th></tr></thead><tbody>`;
  html += parts?.length
    ? parts.map(p => `<tr><th scope="row" class="align-left field-name">${escape(p.name)}</th><td>${fmt(p.percent, 1)}%</td></tr>`).join('')
    : `<tr><td colspan="2" class="align-left">${parts === null ? 'Field breakdown unavailable.' : 'No classified publications.'}</td></tr>`;
  html += '</tbody>';
  if (row.in_n) {
    const names = fields(row.norwegian_field), entries = registerEntries(row, detail);
    const multiple = names.length > 1;
    html += '<tbody class="register-fields"><tr class="field-section">' +
      `<th scope="rowgroup" class="align-left">Norwegian Register field (${year})${multiple ? '' : ': ' + escape(names[0] || 'Unavailable')}</th>` +
      `<td>${names.length ? 'Assigned field' : 'Unavailable'}</td></tr>`;
    if (multiple) {
      const assignments = entries.length ? entries.flatMap(e => fields(e.field).map(name => ({ name, id: e.id })))
        : names.map(name => ({ name, id: null }));
      html += assignments.map(a => `<tr><th scope="row" class="align-left field-name">${escape(a.name)} ` +
        `<span class="register-field-id">(${a.id ? 'ID: ' + escape(a.id) : 'ID unavailable'})</span></th><td></td></tr>`).join('');
    }
    html += '</tbody>';
  }
  return html + '</table>';
}

export function universeMembership(row) {
  return [row.in_oa ? 'OA' : '', row.in_n ? 'NR' : ''].filter(Boolean).join(' · ') || '—';
}

export function metricsTable(history, year, state, metrics, annualRanks = new Map()) {
  const metricKeys = Object.keys(metrics), pctKeys = E.percentileKeys(state), pct = pctKeys.length;
  const headings = [['Citing', 'year'], ['Publications', 'used'], ['Citations', 'used'],
    ['Publications', 'w/out references'], ['Norwegian', 'Reg. level'],
    ...Object.values(metrics).map(m => [m.short])];
  const percentileCells = (r, entry) => {
    if (!r[`in_${state.universe}`]) return '<td>&mdash;</td>'.repeat(pct);
    const result = annualRanks.get(entry.year);
    if (!result) return pctKeys.map(key => `<td aria-label="Loading ${key === 'fieldPct' ? 'field' : 'total'} percentile">&hellip;</td>`).join('');
    if (result.error) return '<td title="Percentiles could not be loaded">&mdash;</td>'.repeat(pct);
    const ranks = result.ranks, id = r.openalex_id;
    return pctKeys.map(key => {
      const values = key === 'fieldPct' ? ranks.fieldRanks : ranks.poolRanks;
      const value = values.get(id);
      return `<td${value == null ? ` title="${escape(ranks.reasons.get(id) || 'Unavailable')}"` : ''}>${fmt(value, 1)}</td>`;
    }).join('');
  };
  const cells = (r, entry) => {
    if (!r) return `<td colspan="${headings.length - 1 + pct}" class="missing">Not in the data for this year</td>`;
    if (!r[`in_${state.universe}`]) return `<td colspan="${headings.length - 1 + pct}" class="missing">Outside the selected universe this year.</td>`;
    const withoutRefs = E.withoutReferences(r);
    let html = `<td>${fmt(E.usedCount(r, state, 'publications'))}</td><td>${fmt(E.usedCount(r, state, 'citations'))}</td>` +
      `<td>${E.isNumber(withoutRefs) ? `${fmt(withoutRefs, 2)}%` : '—'}</td>` +
      `<td>${escape(r.norwegian_level ?? '—')}</td>`;
    html += metricKeys.map(metric => {
      const value = E.score(r, state, state.universe, metric), digits = metrics[metric].digits;
      const formatted = E.isNumber(value) && value > 0 && value < 10 ** -digits ? value.toExponential(2) : fmt(value, digits);
      return `<td>${r[`in_${state.universe}`] ? formatted : '—'}</td>`;
    }).join('');
    return html + (pct ? percentileCells(r, entry) : '');
  };
  return '<table class="metrics-table"><thead><tr>' + headings.map((h, i) => `<th scope="col"${pct ? ' rowspan="2"' : ''}${i === 0 ? ' class="align-left"' : ''}>${h.map(line => `<span class="metric-heading-line">${escape(line)}</span>`).join(' ')}</th>`).join('') +
    (pct ? `<th scope="colgroup" colspan="${pct}" class="percentile-heading">Pctiles &middot; ${escape(metrics[state.metric].short)}</th></tr><tr>${pctKeys.map(key => `<th scope="col">${key === 'fieldPct' ? 'Field' : 'Total'}</th>`).join('')}` : '') +
    '</tr></thead><tbody>' + history.map(([entry, r]) => `<tr class="${entry.year === year ? 'current-year' : ''}">` +
      `<th scope="row" class="align-left">${entry.year}<span class="vintage">${escape(entry.status)} · ${escape((entry.openalex_snapshot ?? '').slice(0, 7))}</span></th>${cells(r, entry)}</tr>`).join('') +
    '</tbody></table>';
}
