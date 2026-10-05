// Opindx loads annual data; engine.js contains the scientific ranking rules.
import { parquetReadObjects } from 'https://cdn.jsdelivr.net/npm/hyparquet@1.31.1/+esm';
import * as E from './engine.js';
import * as D from './journal-details.js';
import * as P from './presets.js';
import {} from './section-nav.js';
import { workbookBytes } from './selection-download.js';

const $ = id => document.getElementById(id);
const PAGE_SIZE = 25;
const METRICS = {
  share: {short: 'NF', name: 'Network Factor', note: 'share of citation-network prestige; sums to 100 over the universe', digits: 5},
  per_article: {short: 'ANS', name: 'Article Network Score', note: 'network share per article; article-weighted mean 1', digits: 3},
};
const FILTERS = {
  fields: {column: 'oa_field', label: 'OpenAlex fields', list: 'field-filter'},
};
let index, year, rows = [], state = {...P.DEFAULTS, fields: []};
let ranks, ranksKey, visible = [], columns = [], memberCount = 0, yearRequest = 0, loading = true;
let explicitCustom = false, downloadBusy = false;
// A display preference for this visit; never part of a preset or ranking pool.
const hiddenColumns = new Set();
const filterValues = {fields: []};
const historyFiles = new Map();
let historyRequest = 0;
let stopJournalTracking = () => {};

const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const option = (value, label) => `<option value="${escape(value)}">${escape(label)}</option>`;
const count = n => n.toLocaleString('en-US');
const fmt = (value, digits = 0) => E.isNumber(value)
  ? value.toLocaleString('en-US', {minimumFractionDigits: digits, maximumFractionDigits: digits}) : '—';
const fmtScore = (value, metric) => E.isNumber(value) && value > 0 && value < 10 ** -METRICS[metric].digits
  ? value.toExponential(2) : fmt(value, METRICS[metric].digits);
const publishedYear = () => index.years.find(entry => entry.year === year);
const universeIds = () => Object.keys(publishedYear().universes);
const universeName = u => publishedYear().universes[u] ?? u.toUpperCase();

async function fetchOk(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} returned ${response.status}`);
  return response;
}
function showError(error) {
  $('notice').hidden = false;
  $('notice').className = 'notice error';
  $('notice-message').hidden = false;
  $('notice-message').textContent = `The data could not be loaded (${error.message}).`;
  $('selection-summary').hidden = true;
}
function setLoading(value) {
  loading = value;
  $('results').setAttribute('aria-busy', String(value));
  $('download-view').disabled = value || downloadBusy;
  if (value) toggleDownloads(false);
  $('search').disabled = year == null;
  $('search-controls').disabled = year == null;
  $('reset').disabled = year == null;
  if (value) { $('previous').disabled = true; $('next').disabled = true; }
}

// Only the most recent year request may replace the table, including on failure.
async function loadYear(newYear) {
  const request = ++yearRequest;
  setLoading(true);
  $('notice').hidden = false;
  $('notice').className = 'notice';
  $('notice-message').hidden = false;
  $('notice-message').textContent = `Loading citation year ${newYear}…`;
  $('selection-summary').hidden = true;
  try {
    const buffer = await (await fetchOk(`data/scores_${newYear}.parquet`)).arrayBuffer();
    if (request !== yearRequest) return;
    const data = await parquetReadObjects({file: buffer});
    if (request !== yearRequest) return;
    year = newYear;
    rows = E.prepareRows(data);
    ranksKey = null;
    state.page = 0;
    const ids = universeIds();
    if (!ids.includes(state.universe)) state.universe = ids[0];
    $('universe').innerHTML = ids.map(u => option(u, universeName(u))).join('');
    fillFilters();
    setLoading(false);
    syncControls();
    render();
    const entry = publishedYear();
    $('notice').className = entry.dummy ? 'notice dummy' : 'notice';
    $('notice-message').hidden = !entry.dummy;
    $('notice-message').textContent = entry.dummy ? 'Dummy data: these journals and numbers are made up for testing.' : '';
    $('selection-summary').hidden = false;
  } catch (error) {
    if (request !== yearRequest) return;
    setLoading(false);
    $('download-view').disabled = year == null || downloadBusy;
    if (year != null) { syncControls(); drawPage(); }
    showError(error);
  }
}

// ---- Table ----
function getColumns() {
  const cols = [
    {key:'title', label:'Journal title', className:'journal-column align-left', title:'Open more info for journal details'},
    {key:'oa_field', label:'OpenAlex field', className:'align-left', title:'Predominant field and its share of classified publications'},
    {key:'publications', label:'Publications', title:`Articles and reviews ${year - 5}–${year - 1}${state.treatment === 'raw' ? '' : ' with linked references'}`},
    {key:'citations', label:'Citations', title:`Citations in ${year}, excluding journal self-citations`},
    {key:'publications_without_references_pct', label:'Pubs. w/out refs.', title:'Share of publications without a recorded OpenAlex reference'},
  ];
  for (const [metric, m] of Object.entries(METRICS)) cols.push({key:`score:${metric}`, label:m.short,
    className:metric === 'share' ? 'group-start' : '', score:true, title:`${m.name} in the ${universeName(state.universe)} universe (${m.note})`});
  for (const key of E.percentileKeys(state)) cols.push(key === 'fieldPct'
    ? {key, label:'Field', className:'group-start percentile-cell', title:'Percentile within the assigned field, after eligibility requirements'}
    : {key, label:'Total', className:'percentile-cell', title:'Percentile among all retained journals; 100 is highest'});
  return cols;
}
function renderHead() {
  const entry = publishedYear(), month = (entry.openalex_snapshot ?? '').slice(0, 7) || 'unknown';
  const scoreCount = columns.filter(col => col.score).length;
  const pctCount = columns.filter(col => col.key.endsWith('Pct')).length;
  const profileCount = columns.length - scoreCount - pctCount;
  const groups = `<th scope="colgroup" colspan="${profileCount}" class="meta-group"><span>Publications in ${year - 5}–${year - 1} · Cited in ${year} · OpenAlex snapshot ${escape(month)} · ${entry.status === 'frozen' ? 'Frozen' : 'Live'} metrics</span> ` +
    '<a class="table-help" href="journal-help.html#journal-table" target="_blank" rel="noopener noreferrer" aria-label="Journal table: What is what? (new tab)">What is what?</a></th>' +
    (scoreCount ? `<th scope="colgroup" colspan="${scoreCount}" class="score-group ${escape(state.universe)}">${escape(universeName(state.universe))}</th>` : '') +
    (pctCount ? `<th scope="colgroup" colspan="${pctCount}" class="percentile-group">Percentiles · ${METRICS[state.metric].short}</th>` : '');
  const headers = columns.map(col => {
    const sorted = state.sortKey === col.key;
    const direction = state.sortDirection === 1 ? 'ascending' : 'descending';
    const arrow = sorted ? (state.sortDirection === 1 ? '↑' : '↓') : '↕';
    const label = col.key.endsWith('Pct') ? `${col.label} percentile` : col.label;
    const hide = col.key === 'title' ? '' : `<button type="button" class="hide-column" data-hide-column="${col.key}" aria-label="Hide ${escape(label)} column" title="Hide ${escape(label)} column"><span aria-hidden="true">×</span></button>`;
    return `<th scope="col" class="${col.className || ''} ${sorted ? 'sorted' : ''}" aria-sort="${sorted ? direction : 'none'}" title="${escape(col.title || col.label)}">` +
      `<div class="column-heading"><button type="button" class="sort-button" data-sort="${col.key}">${col.label}<span class="sort-indicator" aria-hidden="true">${arrow}</span></button>${hide}</div></th>`;
  }).join('');
  $('table-head').innerHTML = `<tr class="group-row">${groups}</tr><tr>${headers}</tr>`;
}
function cell(row, col) {
  const value = E.columnValue(row, state, ranks, col.key), id = row.openalex_id;
  if (col.key === 'title') return `<td class="journal-column"><span class="journal-title">${escape(row.title)}</span> ` +
    `<button type="button" class="text-button more-info" data-journal="${escape(id)}" aria-label="More info about ${escape(row.title)}">more info</button></td>`;
  if (col.key === 'oa_field') return `<td class="field-cell" title="${escape(E.fieldDescription(row))}">${escape(E.fieldLabel(row))}</td>`;
  if (col.key === 'publications_without_references_pct') {
    const low = state.showPercentiles && E.isNumber(value) && state.minCoverage >= 0 && value >= 100 - state.minCoverage;
    const bar = E.isNumber(value) ? `<div class="coverage-track" aria-hidden="true"><div class="coverage-fill" style="width:${Math.max(0, Math.min(100, value))}%"></div></div>` : '';
    return `<td class="coverage-cell ${low ? 'coverage-low' : ''}"><span class="coverage-value">${fmt(value, 1)}${E.isNumber(value) ? '%' : ''}</span>${bar}</td>`;
  }
  if (col.key === 'fieldPct' || col.key === 'poolPct') {
    const note = !E.isNumber(value) ? ranks.reasons.get(id) || 'Not eligible'
      : col.key === 'fieldPct' ? `Compared with ${count(ranks.fieldCounts.get(id))} journals in this field` : `Compared with ${count(ranks.retained)} retained journals`;
    return `<td class="${col.className} ${value == null ? 'missing' : ''}" title="${escape(note)}">${fmt(value, 1)}</td>`;
  }
  if (col.score) {
    const metric = col.key.split(':')[1];
    return `<td class="metric-cell ${col.className || ''} ${metric === 'per_article' ? 'per-article-cell' : ''} ${value == null ? 'missing' : ''}" title="${value == null ? 'Metric unavailable' : escape(value)}">${fmtScore(value, metric)}</td>`;
  }
  return `<td>${fmt(value)}</td>`;
}
function render() {
  if (year == null) return;
  if (state.showPercentiles) {
    const key = JSON.stringify([state.treatment, state.universe, state.metric, state.minCoverage, state.minYears, state.topPercent]);
    if (key !== ranksKey) { ranks = E.rank(rows, state); ranksKey = key; }
  }
  const available = getColumns();
  if (!available.some(col => col.key === state.sortKey)) Object.assign(state, {sortKey:'score:per_article', sortDirection:-1});
  columns = available.filter(col => !hiddenColumns.has(col.key));
  const firstScore = columns.find(col => col.score), firstPct = columns.find(col => col.key.endsWith('Pct'));
  for (const col of [firstScore, firstPct].filter(Boolean)) {
    if (!col.className?.includes('group-start')) col.className = `${col.className || ''} group-start`;
  }
  $('restore-columns').hidden = !hiddenColumns.size;
  visible = E.view(rows, state.showPercentiles ? state : {...state, poolOnly:false}, ranks);
  memberCount = rows.filter(row => row[`in_${state.universe}`]).length;
  drawPage();
}
function drawPage() {
  state.page = Math.max(0, Math.min(state.page, Math.ceil(visible.length / PAGE_SIZE) - 1));
  const shown = visible.slice(state.page * PAGE_SIZE, (state.page + 1) * PAGE_SIZE);
  renderHead();
  const restricted = state.universe !== 'oa' || state.fields.length || state.oaOnly || (state.showPercentiles && state.poolOnly);
  $('table-body').innerHTML = shown.length ? shown.map(row => `<tr>${columns.map(col => cell(row, col)).join('')}</tr>`).join('')
    : `<tr><td colspan="${columns.length}" class="empty-cell">No journals match these choices in the ${escape(universeName(state.universe))} universe. ` +
      (restricted && universeIds().includes('oa') ? '<button type="button" class="text-button" data-search-all>Search all OpenAlex journals</button>' : 'Try a broader search.') + '</td></tr>';
  $('result-count').innerHTML = `<strong>${count(visible.length)}</strong> of <strong>${count(memberCount)}</strong> journals in the ${escape(universeName(state.universe))} universe`;
  $('pool-summary').hidden = !state.showPercentiles;
  $('pool-summary').title = `Journals with a ${E.percentileKeys(state).includes('poolPct') ? 'Total' : 'Field'} percentile across the selected universe, before search and display filters.`;
  $('pool-summary').innerHTML = state.showPercentiles ? ` · <strong>${count(E.availablePercentiles(state, ranks).size)}</strong> journals with percentile` : '';
  $('page-status').textContent = visible.length ? `${count(state.page * PAGE_SIZE + 1)}–${count(state.page * PAGE_SIZE + shown.length)} of ${count(visible.length)} journals` : '0 journals';
  $('previous').disabled = loading || state.page === 0;
  $('next').disabled = loading || (state.page + 1) * PAGE_SIZE >= visible.length;
}

// ---- Controls ----
const SELECTS = {metric:'metric', coverage:'minCoverage', 'min-years':'minYears'};
const NUMERIC = new Set(['minCoverage','minYears']);
const CHECKBOXES = {'oa-only':'oaOnly', 'pool-only':'poolOnly'};
const uniqueValues = values => [...new Set(values.filter(Boolean))].sort(E.compareText);
function fillFilters() {
  const inUniverse = rows.filter(row => row[`in_${state.universe}`]);
  for (const [key, filter] of Object.entries(FILTERS)) {
    filterValues[key] = uniqueValues(inUniverse.map(row => row[filter.column]));
    state[key] = state[key].filter(value => filterValues[key].includes(value));
    drawFilter(key);
  }
}
function syncFilterLabels(key) {
  const chosen = state[key];
  if (key === 'fields') {
    $('field-summary').textContent = chosen.length === 1 ? chosen[0] : chosen.length ? `${chosen.length} selected` : 'All';
    $('all-fields').checked = !chosen.length;
  }
}
function drawFilter(key) {
  const filter = FILTERS[key], chosen = state[key], values = filterValues[key];
  syncFilterLabels(key);
  $(filter.list).innerHTML = values.map(value => `<label class="check-line"><input type="checkbox" value="${escape(value)}"${chosen.includes(value) ? ' checked' : ''}>${escape(value)}</label>`).join('') +
    (values.length ? '' : '<p class="hint">No fields available.</p>');
}
function syncControls() {
  for (const [id, key] of Object.entries(SELECTS)) $(id).value = state[key];
  for (const [id, key] of Object.entries(CHECKBOXES)) $(id).checked = state[key];
  if (!loading) $('year').value = year;
  $('universe').value = state.universe;
  $('preset').value = explicitCustom ? 'custom' : P.identifyPreset(state);
  // Do not replace the search input's value while typing: keep caret/IME state.
  if ($('search').value !== state.query) $('search').value = state.query;
  $('top-percent').value = state.topPercent;
  $('include-zero').checked = state.treatment === 'raw';
  const selected = E.percentileKeys(state);
  $('percentiles-none').checked = !selected.length;
  $('percentiles-field').checked = selected.includes('fieldPct');
  $('percentiles-total').checked = selected.includes('poolPct');
  $('percentile-summary').textContent = !selected.length ? 'None' : selected.length === 2 ? 'Field and Total' : selected[0] === 'fieldPct' ? 'Field percentiles' : 'Total percentiles';
  $('percentile-settings').disabled = !state.showPercentiles;
  $('pool-only').disabled = !state.showPercentiles;
}
function toggleSettings(open) {
  $('settings').hidden = !open;
  $('settings-help').hidden = !open;
  $('settings-toggle').setAttribute('aria-expanded', String(open));
  $('settings-indicator').textContent = open ? '\u2212' : '+';
}
function update(changes) {
  const changedUniverse = changes.universe != null && changes.universe !== state.universe;
  if (Object.keys(changes).some(key => key in P.PRESETS.full)) explicitCustom = false;
  if (Object.hasOwn(changes, 'percentileMode')) changes = {...changes, showPercentiles:changes.percentileMode !== 'none'};
  Object.assign(state, changes, {page:0});
  if (Object.keys(changes).some(key => key in P.PRESETS.full) && P.identifyPreset(state) === 'custom') toggleSettings(true);
  if (changedUniverse) fillFilters();
  syncControls(); render();
}
function applyPreset(preset) {
  if (preset === 'custom') { explicitCustom = true; syncControls(); toggleSettings(true); return; }
  explicitCustom = false;
  update(P.PRESETS[preset]);
  toggleSettings(false);
}

function showJournal(id) {
  const row = rows.find(r => r.openalex_id === id);
  if (!row) return;
  stopJournalTracking();
  const details = D.metadataRows(row);
  $('dialog-content').innerHTML = `<p class="dialog-label">JOURNAL DETAILS · ${year} · ${state.treatment.toUpperCase()}</p><h2 id="dialog-title">${escape(row.title)}</h2>` +
    '<nav id="journal-sections" class="journal-section-nav" aria-label="Journal detail sections">' +
    '<a href="#details-title" data-journal-section="details-title">Details</a>' +
    '<a href="#fields-title" data-journal-section="fields-title">Fields covered</a>' +
    '<a href="#metrics-title" data-journal-section="metrics-title">Metrics by year</a>' +
    '<button type="button" id="close-dialog" class="dialog-close" data-journal-close aria-label="Close journal details" title="Close journal details" autofocus>×</button></nav>' +
    '<div class="dialog-section-heading"><h3 id="details-title" tabindex="-1">Details</h3><a class="table-help" href="journal-help.html#journal-details" target="_blank" rel="noopener noreferrer" aria-label="Details: What is what? (new tab)">What is what?</a></div>' +
    `<dl id="journal-metadata" aria-labelledby="details-title">${details.map(([key, value]) => `<dt>${key}</dt><dd>${value}</dd>`).join('')}</dl>` +
    '<div class="dialog-section-heading"><h3 id="fields-title" tabindex="-1">Fields covered</h3><a class="table-help" href="journal-help.html#fields-covered" target="_blank" rel="noopener noreferrer" aria-label="Fields covered: What is what? (new tab)">What is what?</a></div><div id="field-breakdown" aria-live="polite"><p>Loading fields…</p></div>' +
    `<div class="dialog-section-heading"><h3 id="metrics-title" tabindex="-1">Metrics by year · ${escape(universeName(state.universe))} universe · ${state.treatment === 'raw' ? 'Raw' : 'Filtered'}</h3><a class="table-help" href="journal-help.html#yearly-metrics" target="_blank" rel="noopener noreferrer" aria-label="Metrics by year: What is what? (new tab)">What is what?</a></div>` +
    '<div id="history" class="dialog-table-scroll" role="region" aria-labelledby="metrics-title" tabindex="0"></div>' +
    '<p id="history-percentile-status" role="status"></p>';
  $('journal-dialog').showModal();
  $('journal-dialog').scrollTop = 0;
  stopJournalTracking = globalThis.OpindxSections.trackSections($('journal-sections'), {scroller: $('journal-dialog'), content: $('dialog-content')});
  showHistory(id, row);
}

function navigateJournalSection(event) {
  if (event.target.closest('[data-journal-close]')) {
    $('journal-dialog').close();
    return;
  }
  const link = event.target.closest('[data-journal-section]');
  if (!link) return;
  const id = link.dataset.journalSection;
  if (!['details-title', 'fields-title', 'metrics-title'].includes(id)) return;
  const target = $(id), dialog = $('journal-dialog'), nav = $('journal-sections');
  if (!target || !nav) return;
  event.preventDefault();
  // Move keyboard focus too, without scrolling the page behind the modal.
  target.focus({preventScroll:true});
  const top = dialog.scrollTop + target.getBoundingClientRect().top -
    dialog.getBoundingClientRect().top - dialog.clientTop - nav.getBoundingClientRect().height - 12;
  dialog.scrollTo({top:Math.max(0, top), behavior:'auto'});
  // Sticky positioning includes the dialog's padding. Check the actual position
  // after scrolling so wrapped bars and different screen sizes cannot cover a heading.
  const overlap = nav.getBoundingClientRect().bottom + 12 - target.getBoundingClientRect().top;
  if (overlap > 0) dialog.scrollTo({top:Math.max(0, dialog.scrollTop - overlap), behavior:'auto'});
}

// One small history file contains every score year for this journal.
async function journalHistory(id) {
  const folder = index.journal_details?.history_path ?? index.field_breakdown?.history_path ?? 'history';
  const url = `data/${folder}/${id.slice(-2)}.parquet`;
  if (!historyFiles.has(url)) historyFiles.set(url, fetchOk(url).then(response => response.arrayBuffer())
    .catch(error => { historyFiles.delete(url); throw error; }));
  const found = await parquetReadObjects({ file: await historyFiles.get(url), filter: { openalex_id: { $eq: id } } });
  const byYear = new Map(found.map(row => [Number(row.score_year), E.toNumbers(row)]));
  return index.years.map(entry => [entry, byYear.get(entry.year) ?? null]);
}

const historicalRankFiles = new Map();
let historicalRankKey = '', historicalRanks = new Map();

async function rankYear(entry, settings, selectedYear, selectedRows) {
  if (entry.year === selectedYear) return E.rank(selectedRows, settings);
  const folder = index.journal_details?.rank_path;
  const suffix = index.journal_details?.rank_layout === 'metric-treatment' ? `-${settings.metric}-${settings.treatment}` : '';
  const url = folder ? `data/${folder}/${entry.year}-${settings.universe}${suffix}.parquet` : `data/scores_${entry.year}.parquet`;
  if (!historicalRankFiles.has(url)) historicalRankFiles.set(url, fetchOk(url)
    .then(response => response.arrayBuffer()).then(file => parquetReadObjects({ file }))
    .then(data => data.map(E.toNumbers))
    .catch(error => { historicalRankFiles.delete(url); throw error; }));
  return E.rank(await historicalRankFiles.get(url), settings);
}

async function showHistory(id, metadata) {
  const request = ++historyRequest;
  const selectedYear = year, selectedRows = rows, settings = { ...state };
  $('history').innerHTML = '<p>Loading the other years…</p>';
  try {
    const history = await journalHistory(id);
    if (request !== historyRequest) return;
    const detail = history.find(([entry]) => entry.year === selectedYear)?.[1];
    $('field-breakdown').innerHTML = D.fieldsTable(metadata, detail, selectedYear);
    if ($('register-details')) $('register-details').innerHTML = D.registerLinks(metadata, detail);
    $('history').innerHTML = D.metricsTable(history, selectedYear, settings, METRICS);
    if (!settings.showPercentiles) return;
    $('history-percentile-status').textContent = 'Loading yearly percentiles…';
    // Search, field filters and display-only choices do not define the ranking pool.
    const key = JSON.stringify([settings.universe, settings.treatment, settings.metric,
      settings.minCoverage, settings.minYears, settings.topPercent]);
    if (key !== historicalRankKey) { historicalRankKey = key; historicalRanks = new Map(); }
    const cache = historicalRanks;
    const results = await Promise.all(history.map(async ([entry, row]) => {
      if (!row || !row[`in_${settings.universe}`]) return [entry.year, { ranks: null }];
      try {
        if (!cache.has(entry.year)) cache.set(entry.year,
          rankYear(entry, settings, selectedYear, selectedRows).catch(error => { cache.delete(entry.year); throw error; }));
        return [entry.year, { ranks: await cache.get(entry.year) }];
      } catch { return [entry.year, { error: true }]; }
    }));
    if (request !== historyRequest) return;
    $('history').innerHTML = D.metricsTable(history, selectedYear, settings, METRICS, new Map(results));
    $('history-percentile-status').textContent = results.some(([, result]) => result.error)
      ? 'Some yearly percentiles could not be loaded. Close and reopen this journal to retry.' : '';
  } catch (error) {
    if (request === historyRequest) {
      $('history').textContent = `The other years could not be loaded (${error.message}).`;
      $('field-breakdown').innerHTML = D.fieldsTable(metadata, null, selectedYear);
      $('history-percentile-status').textContent = '';
    }
  }
}


// ---- Downloads ----

function csvCell(value) {
  if (value == null) return '';
  if (typeof value !== 'string') return String(value); // numbers and booleans need no quotes
  const text = /^[=+@\-\t\r]/.test(value) ? "'" + value : value; // keep spreadsheet formulas as plain text
  return '"' + text.replace(/"/g, '""') + '"';
}
const csvLines = lines => lines.map(line => line.map(csvCell).join(',')).join('\r\n');

// Saves a CSV from text parts; the byte order mark makes Excel read the file as UTF-8.
function saveCsv(filename, parts) {
  const url = URL.createObjectURL(new Blob(['\ufeff', ...parts], { type: 'text/csv;charset=utf-8' }));
  const link = Object.assign(document.createElement('a'), { href: url, download: filename });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function selectionExport() {
  // Follow the displayed columns, splitting the field cell into name and percentage.
  const downloadColumns = columns.flatMap(col => {
    if (col.key === 'oa_field') return [
      {label:col.label, value:row => E.field(row)},
      {label:'OpenAlex field (%)', value:row => {
        const share = row.oa_field_modal_share;
        return E.field(row) !== 'Unknown' && E.isNumber(share) && share >= 0 && share <= 1
          ? 100 * share : null;
      }},
    ];
    const label = col.key.endsWith('Pct')
      ? `${col.label} percentile (${METRICS[state.metric].short})` : col.label;
    return [{label, value:row => E.columnValue(row, state, ranks, col.key)}];
  });
  const header = downloadColumns.map(col => col.label);
  const lines = visible.map(row => downloadColumns.map(col => col.value(row)));
  return {filename:`opindx-${year}-${state.treatment}-view`, matrix:[header, ...lines]};
}
function downloadView() {
  const {filename, matrix} = selectionExport();
  saveCsv(`${filename}.csv`, [csvLines(matrix)]);
}
function toggleDownloads(open) {
  $('download-options').hidden = !open;
  $('download-view').setAttribute('aria-expanded', String(open));
}
function saveBinary(filename, bytes) {
  const url = URL.createObjectURL(new Blob([bytes], {type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}));
  const link = Object.assign(document.createElement('a'), {href:url, download:filename});
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function downloadXlsx() {
  if (loading || year == null || downloadBusy) return;
  // Capture the selection before any asynchronous library load or later filter change.
  const {filename, matrix} = selectionExport();
  downloadBusy = true; toggleDownloads(false);
  $('download-view').disabled = true;
  $('download-status').textContent = 'Preparing XLSX...';
  try {
    // Let the status paint before constructing a potentially large workbook.
    await new Promise(resolve => setTimeout(resolve, 0));
    const bytes = await workbookBytes(matrix);
    saveBinary(`${filename}.xlsx`, bytes);
    $('download-status').textContent = '';
  } catch (error) {
    $('download-status').textContent = 'XLSX could not be created. Please try again or choose CSV.';
  } finally {
    downloadBusy = false;
    $('download-view').disabled = loading || year == null;
  }
}

// ---- Events ----
for (const [id, key] of Object.entries(SELECTS)) $(id).addEventListener('change', event =>
  update({[key]:NUMERIC.has(key) ? Number(event.target.value) : event.target.value}));
for (const [id, key] of Object.entries(CHECKBOXES)) $(id).addEventListener('change', event => update({[key]:event.target.checked}));
$('year').addEventListener('change', event => loadYear(Number(event.target.value)));
$('search').addEventListener('input', event => update({query:event.target.value}));
$('include-zero').addEventListener('change', event => update({treatment:event.target.checked ? 'raw' : 'filtered'}));
$('top-percent').addEventListener('change', event => {
  const value = Number(event.target.value);
  update({topPercent:Number.isFinite(value) ? Math.min(100, Math.max(1, Math.round(value))) : 100});
});
$('universe').addEventListener('change', event => update({universe:event.target.value}));
$('preset').addEventListener('change', event => applyPreset(event.target.value));
$('settings-toggle').addEventListener('click', () => toggleSettings($('settings').hidden));
$('percentile-choices').addEventListener('change', event => {
  if (event.target.id === 'percentiles-none') { update({percentileMode:'none'}); return; }
  const field = $('percentiles-field').checked, total = $('percentiles-total').checked;
  update({percentileMode:field && total ? 'both' : field ? 'field' : total ? 'total' : 'none'});
});
$('table-head').addEventListener('click', event => {
  if (loading) return;
  const hideKey = event.target.closest('[data-hide-column]')?.dataset.hideColumn;
  if (hideKey) {
    const position = columns.findIndex(col => col.key === hideKey);
    if (hideKey === 'title' || position < 0) return;
    hiddenColumns.add(hideKey);
    render();
    const next = columns[Math.min(position, columns.length - 1)].key;
    $('table-head').querySelector(`[data-sort="${next}"]`)?.focus({preventScroll:true});
    return;
  }
  const key = event.target.closest('[data-sort]')?.dataset.sort;
  if (!key) return;
  const textColumn = ['title','oa_field'].includes(key);
  update({sortKey:key, sortDirection:state.sortKey === key ? -state.sortDirection : textColumn ? 1 : -1});
  $('table-head').querySelector(`[data-sort="${key}"]`)?.focus({preventScroll:true});
});
$('restore-columns').addEventListener('click', () => {
  hiddenColumns.clear(); render();
  $('table-head').querySelector('[data-sort="title"]')?.focus({preventScroll:true});
});
$('table-body').addEventListener('click', event => {
  if (loading) return;
  if (event.target.closest('[data-search-all]')) {
    update({...P.PRESETS.full, fields:[], oaOnly:false}); fillFilters(); return;
  }
  const id = event.target.closest('[data-journal]')?.dataset.journal;
  if (id) showJournal(id);
});
for (const [key, filter] of Object.entries(FILTERS)) {
  $(filter.list).addEventListener('change', event => {
    const value = event.target.value;
    update({[key]:event.target.checked ? [...new Set([...state[key],value])] : state[key].filter(chosen => chosen !== value)});
    // Keep the focused checkbox in place for keyboard and screen-reader users.
    syncFilterLabels(key);
  });
}
$('all-fields').addEventListener('change', () => {
  // With no individual choices, All fields is the unrestricted selection.
  update({fields:[]}); drawFilter('fields');
});
$('previous').addEventListener('click', () => { state.page--; drawPage(); });
$('next').addEventListener('click', () => { state.page++; drawPage(); });
$('download-view').addEventListener('click', () => {
  if (loading || year == null || downloadBusy) return;
  toggleDownloads($('download-options').hidden);
  if (!$('download-options').hidden) $('download-csv').focus();
});
$('download-csv').addEventListener('click', () => {
  if (loading || year == null || downloadBusy) return;
  toggleDownloads(false); $('download-status').textContent = ''; downloadView(); $('download-view').focus();
});
$('download-xlsx').addEventListener('click', downloadXlsx);
document.addEventListener('click', event => {
  if (!$('download-options').hidden && !event.target.closest('.download-picker')) toggleDownloads(false);
  for (const id of ['field-picker', 'percentile-picker']) {
    if ($(id).open && !$(id).contains(event.target)) $(id).open = false;
  }
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !$('download-options').hidden) { toggleDownloads(false); $('download-view').focus(); }
  if (event.key === 'Escape') for (const id of ['field-picker', 'percentile-picker']) {
    if ($(id).open) { $(id).open = false; $(id).querySelector('summary').focus(); }
  }
});
$('dialog-content').addEventListener('click', navigateJournalSection);
$('journal-dialog').addEventListener('close', () => { historyRequest++; stopJournalTracking(); });
$('reset').addEventListener('click', () => {
  explicitCustom = false;
  hiddenColumns.clear();
  state = {...P.DEFAULTS, fields:[]};
  if (!universeIds().includes(state.universe)) state.universe = universeIds()[0];
  fillFilters(); syncControls(); render(); toggleSettings(false);
});

// ---- Start ----
$('metric').innerHTML = Object.entries(METRICS).map(([metric, m]) => option(metric, m.short)).join('');
try {
  index = await (await fetchOk('data/index.json')).json();
  const initial = P.startingSelection(index.years, location.search);
  state = {...state, ...P.PRESETS[initial.preset]};
  $('year').innerHTML = [...index.years].sort((a, b) => b.year - a.year).map(entry => option(entry.year, `${entry.year} (${entry.status})`)).join('');
  $('year').value = initial.year;
  await loadYear(initial.year);
} catch (error) { showError(error); }
