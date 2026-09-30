// Opindx website: loads a data run and draws the journal table. The ranking logic is in engine.js.
import { parquetReadObjects } from 'https://cdn.jsdelivr.net/npm/hyparquet@1.31.1/+esm';
import * as E from './engine.js';

const $ = id => document.getElementById(id);
const PAGE_SIZE = 25;
// Columns in every downloaded CSV; the chosen universes add their own columns.
const BASE_COLUMNS = ['openalex_id', 'title', 'publisher', 'issn_l', 'issns', 'oa_domain', 'oa_field', 'norwegian_area',
  'norwegian_field', 'norwegian_level', 'norwegian_register_url', 'is_open_access', 'score_year', 'publications_raw', 'publications_filtered',
  'citations_raw', 'citations_filtered', 'reference_coverage_pct', 'active_years'];
const LEVELS_URL = 'https://kanalregister.hkdir.no/en/informasjonsartikler/levels-and-changes-in-levels';
// The only place where the scores are named; the keys match the data columns (see SPEC.md).
const METRICS = {
  share: { short: 'JNS', name: 'Journal Network Share', note: 'share of citation-network prestige; sums to 100 over the universe', digits: 5 },
  per_article: { short: 'ANS', name: 'Article Network Score', note: 'network share per article; article-weighted mean 1', digits: 3 },
};
// The percentile settings start at the values used in the working paper and report.
const DEFAULTS = {
  treatment: 'filtered', universe: 'n', metric: 'per_article',
  minCoverage: 20, minYears: 4, topPercent: 70, query: '', fields: [], publishers: [],
  oaOnly: false, poolOnly: true, showPercentiles: false,
  sortKey: 'score:per_article', sortDirection: -1, page: 0,
};
const PERCENTILE_SETTINGS = ['metric', 'minCoverage', 'minYears', 'topPercent', 'poolOnly'];
// The filter lists, each a search box above a scrollable list of checkboxes.
const FILTERS = {
  fields: { column: 'oa_field', label: 'OpenAlex fields', list: 'field-filter', search: 'field-search', legend: 'field-legend' },
  publishers: { column: 'publisher', label: 'Publishers', list: 'publisher-list', search: 'publisher-search', legend: 'publisher-legend' },
};
const FILTER_ROWS_SHOWN = 40; // a filter list shows this many matches at a time

let index;          // data/index.json: the published score years, newest first
let year;           // the selected score year
let rows = [];      // journals of the selected score year
let state = { ...DEFAULTS };
let ranks, ranksKey, visible, columns, memberCount;
const filterValues = {}; // every value per filter, for the selected universe
const historyFiles = new Map(); // history files, downloaded on demand for the journal details
let historyRequest = 0;

const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const option = (value, label) => `<option value="${escape(value)}">${escape(label)}</option>`;
const count = n => n.toLocaleString('en-US');
const fmt = (value, digits = 0) => E.isNumber(value)
  ? value.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '—';
// Very small positive scores use scientific notation so they don't round to zero.
const fmtScore = (value, metric) => E.isNumber(value) && value > 0 && value < 10 ** -METRICS[metric].digits
  ? value.toExponential(2) : fmt(value, METRICS[metric].digits);
const publishedYear = () => index.years.find(entry => entry.year === year);
const universeIds = () => Object.keys(publishedYear().universes);
const universeName = u => publishedYear().universes[u] ?? u.toUpperCase();
const openAlexUrl = row => `https://openalex.org/${encodeURIComponent(row.openalex_id)}`;

async function fetchOk(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} returned ${response.status}`);
  return response;
}

function showError(error) {
  $('notice').className = 'notice error';
  $('notice').textContent = `The data could not be loaded (${error.message}).`;
}

// ---- Loading data ----

async function loadYear(newYear) {
  $('notice').className = 'notice';
  $('notice').textContent = `Loading score year ${newYear}…`;
  const buffer = await (await fetchOk(`data/scores_${newYear}.parquet`)).arrayBuffer();
  const data = await parquetReadObjects({ file: buffer });
  year = newYear;
  rows = E.prepareRows(data);
  ranksKey = null;
  state.page = 0;
  // A year keeps the universes of the run it came from, so the choice is rebuilt per year.
  const ids = universeIds();
  if (!ids.includes(state.universe)) state.universe = ids[0];
  $('universe').innerHTML = ids.map(u => option(u, universeName(u))).join('');
  fillFilters();
  syncControls();
  render();
  showDataInfo();
}

// Where the numbers come from: frozen years never change, live years follow the newest run.
function showDataInfo() {
  const entry = publishedYear();
  const month = (entry.openalex_snapshot ?? '').slice(0, 7);
  $('data-status').textContent = `${entry.status === 'frozen' ? 'Frozen' : 'Live'}${month ? ` · ${month}` : ''}`;
  $('data-status').className = `data-status ${entry.status}`;
  $('data-status').title = `Score year ${entry.year} comes from run ${entry.run}` +
    ` · OpenAlex snapshot ${entry.openalex_snapshot ?? 'unknown'}` +
    ` · Norwegian Register snapshot ${entry.norwegian_register_snapshot ?? 'unknown'}` +
    ` · run published ${entry.created}`;
  const notice = $('notice');
  notice.className = entry.dummy ? 'notice dummy' : 'notice';
  notice.textContent = entry.dummy
    ? `Dummy data: every journal and number in run ${entry.run} is made up, for testing the website only.`
    : `Score year ${entry.year} · ${entry.status === 'frozen' ? 'frozen' : 'live'} · from run ${entry.run}`;
}

// ---- Table ----

function getColumns() {
  const cols = [
    { key: 'title', label: 'Journal', className: 'journal-column align-left', title: 'Open OpenAlex with the ID, or "More info" for the details' },
    { key: 'oa_field', label: 'Field', className: 'align-left', title: publishedYear().field_assignment ? 'Most frequent work-based field and its share of classified works; percentiles use the field without the percentage' : 'OpenAlex source field from this historical run; percentiles are calculated within these fields' },
  ];
  cols.push(
    { key: 'publications', label: 'Publications', title: `Articles and reviews ${year - 5}–${year - 1}${state.treatment === 'raw' ? '' : ' with at least one linked reference'}` },
    { key: 'citations', label: 'Citations', title: `Citations in ${year} to those publications, excluding journal self-citations` },
    { key: 'reference_coverage_pct', label: 'Ref. coverage', title: 'Share of publications with at least one linked OpenAlex reference' });
  for (const [metric, m] of Object.entries(METRICS)) cols.push(
    { key: `score:${metric}`, label: m.short, className: metric === 'share' ? 'group-start' : '', score: true, title: `${m.name} in the ${universeName(state.universe)} universe (${m.note})` });
  if (state.showPercentiles) cols.push(
    { key: 'fieldPct', label: 'Field pct.', className: 'group-start percentile-cell', title: 'Percentile within its field, after the coverage and history requirements' },
    { key: 'poolPct', label: `${METRICS[state.metric].short} pct.`, className: 'percentile-cell', title: 'Percentile among all retained journals; 100 is highest' });
  return cols;
}

function renderHead() {
  const profileColumns = columns.filter(c => !c.score && !c.key.endsWith('Pct')).length;
  const groups = `<th scope="colgroup" colspan="${profileColumns}" class="meta-group">Journal profile · ${year - 5}–${year - 1} publications · ${year} citations</th>` +
    `<th scope="colgroup" colspan="${Object.keys(METRICS).length}" class="score-group ${escape(state.universe)}">${escape(universeName(state.universe))}</th>` +
    (state.showPercentiles ? `<th scope="colgroup" colspan="2" class="percentile-group">Percentiles · ${METRICS[state.metric].short}</th>` : '');
  const headers = columns.map(col => {
    const sorted = state.sortKey === col.key;
    const direction = state.sortDirection === 1 ? 'ascending' : 'descending';
    const arrow = sorted ? (state.sortDirection === 1 ? '↑' : '↓') : '↕';
    return `<th scope="col" class="${col.className || ''} ${sorted ? 'sorted' : ''}" aria-sort="${sorted ? direction : 'none'}" title="${escape(col.title || col.label)}">` +
      `<button type="button" class="sort-button" data-sort="${col.key}">${col.label}<span class="sort-indicator" aria-hidden="true">${arrow}</span></button></th>`;
  }).join('');
  $('table-head').innerHTML = `<tr class="group-row">${groups}</tr><tr>${headers}</tr>`;
}

function cell(row, col) {
  const value = E.columnValue(row, state, ranks, col.key);
  const id = row.openalex_id;
  if (col.key === 'title') return `<td class="journal-column"><span class="journal-title">${escape(row.title)}</span>` +
    `<a class="journal-id" href="${openAlexUrl(row)}" target="_blank" rel="noopener noreferrer" aria-label="Open ${escape(row.title)} in OpenAlex, new tab">${escape(id)} ↗</a>` +
    `<span class="journal-id"> · ${escape(row.issn_l || 'no ISSN')}</span>` +
    `<button type="button" class="text-button more-info" data-journal="${escape(id)}">More info</button></td>`;
  if (col.key === 'oa_field') return `<td class="field-cell" title="${escape(E.fieldDescription(row))}">${escape(E.fieldLabel(row))}</td>`;
  if (col.key === 'reference_coverage_pct') {
    const low = E.isNumber(value) && state.minCoverage >= 0 && value <= state.minCoverage;
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
    const classes = `metric-cell ${col.className || ''} ${metric === 'per_article' ? 'per-article-cell' : ''}`;
    return `<td class="${classes} ${value == null ? 'missing' : ''}" title="${value == null ? 'In this universe, but no score' : escape(value)}">${fmtScore(value, metric)}</td>`;
  }
  return `<td>${fmt(value)}</td>`;
}

function render() {
  // Percentiles only depend on the ranking settings, so they are recomputed only when those change.
  const key = JSON.stringify([state.treatment, state.universe, state.metric, state.minCoverage, state.minYears, state.topPercent]);
  if (key !== ranksKey) { ranks = E.rank(rows, state); ranksKey = key; }
  columns = getColumns();
  if (!columns.some(col => col.key === state.sortKey)) Object.assign(state, { sortKey: 'title', sortDirection: 1 });
  visible = E.view(rows, state.showPercentiles ? state : { ...state, poolOnly: false }, ranks);
  memberCount = rows.filter(row => row[`in_${state.universe}`]).length;
  $('percentile-settings').hidden = !state.showPercentiles;
  drawPage();
}

// Draws the current page only; paging doesn't need to filter and sort again.
function drawPage() {
  state.page = Math.max(0, Math.min(state.page, Math.ceil(visible.length / PAGE_SIZE) - 1));
  const shown = visible.slice(state.page * PAGE_SIZE, (state.page + 1) * PAGE_SIZE);
  renderHead();
  $('table-body').innerHTML = shown.length ? shown.map(row => `<tr>${columns.map(col => cell(row, col)).join('')}</tr>`).join('')
    : `<tr><td colspan="${columns.length}" class="empty-cell">No journals match these choices in the ${escape(universeName(state.universe))} universe.` +
      `${matchesElsewhere()} Try a broader search or reset the filters.</td></tr>`;
  $('result-count').textContent = `${count(visible.length)} of ${count(memberCount)} journals in the ${universeName(state.universe)} universe`;
  $('pool-summary').innerHTML = `<strong>${count(ranks.qualified)}</strong> meet the requirements · <strong>${count(ranks.groups)}</strong> fields · ` +
    `<strong>${count(ranks.retained)}</strong> retained for the final percentile${ranks.retained ? '' : ' — broaden the requirements to define percentiles'}`;
  $('page-status').textContent = visible.length ? `${count(state.page * PAGE_SIZE + 1)}–${count(state.page * PAGE_SIZE + shown.length)} of ${count(visible.length)} journals` : '0 journals';
  $('previous').disabled = state.page === 0;
  $('next').disabled = (state.page + 1) * PAGE_SIZE >= visible.length;
  $('treatment-note').textContent = state.treatment === 'raw'
    ? 'Raw scores · all eligible articles and reviews' : 'Filtered scores · eligible articles and reviews with linked references';
}

// ---- Controls ----

const SELECTS = { metric: 'metric', coverage: 'minCoverage', 'min-years': 'minYears' };
const NUMERIC = new Set(['minCoverage', 'minYears']);
const CHECKBOXES = { 'oa-only': 'oaOnly', 'show-percentiles': 'showPercentiles', 'pool-only': 'poolOnly' };

// When a search finds nothing here, say where the journals are instead.
function matchesElsewhere() {
  const query = state.query.toLowerCase().trim();
  if (!query) return '';
  return universeIds().filter(u => u !== state.universe).map(u => {
    const matches = rows.filter(row => row[`in_${u}`] && E.searchText(row).includes(query)).length;
    if (!matches) return '';
    return ` ${matches === 1 ? '1 journal matches' : `${count(matches)} journals match`} in the ${universeName(u)} universe.`;
  }).join('');
}

const uniqueValues = values => [...new Set(values.filter(Boolean))].sort(E.compareText);

function fillFilters() {
  const inUniverse = rows.filter(row => row[`in_${state.universe}`]);
  for (const [key, filter] of Object.entries(FILTERS)) {
    filterValues[key] = uniqueValues(inUniverse.map(row => row[filter.column]));
    state[key] = state[key].filter(value => filterValues[key].includes(value));
    drawFilter(key);
  }
}

// Chosen values come first, so they stay visible and the box never changes size.
function drawFilter(key) {
  const filter = FILTERS[key], chosen = state[key];
  const query = $(filter.search).value.toLowerCase().trim();
  const matches = filterValues[key].filter(value => value.toLowerCase().includes(query));
  const shown = [...matches].sort((a, b) => Number(chosen.includes(b)) - Number(chosen.includes(a))).slice(0, FILTER_ROWS_SHOWN);
  $(filter.legend).textContent = chosen.length ? `${filter.label} (${chosen.length} selected)` : filter.label;
  $(filter.list).innerHTML = shown.map(value =>
    `<label class="check-line"><input type="checkbox" value="${escape(value)}"${chosen.includes(value) ? ' checked' : ''}>${escape(value)}</label>`).join('') +
    (matches.length > shown.length ? `<p class="hint">${count(matches.length - shown.length)} more; refine the search.</p>` : '') +
    (matches.length ? '' : '<p class="hint">Nothing matches.</p>');
}

function syncControls() {
  for (const [id, key] of Object.entries(SELECTS)) $(id).value = state[key];
  for (const [id, key] of Object.entries(CHECKBOXES)) $(id).checked = state[key];
  $('year').value = year;
  $('universe').value = state.universe;
  $('search').value = state.query;
  $('top-percent').value = state.topPercent;
  $('include-zero').checked = state.treatment === 'raw';
}

function showJournal(id) {
  const row = rows.find(r => r.openalex_id === id);
  if (!row) return;
  const details = [
    ['OpenAlex ID', `<a href="${openAlexUrl(row)}" target="_blank" rel="noopener noreferrer">${escape(id)} ↗ (new tab)</a>`],
    ['ISSNs', escape(row.issns || 'Unavailable')],
    ['Publisher', escape(row.publisher || 'Unavailable')],
    ['OpenAlex domain / field', escape([row.oa_domain, E.fieldLabel(row)].filter(Boolean).join(' / '))],
    ['Field assignment', escape(E.fieldDescription(row))],
    ['Norwegian area / field', escape([row.norwegian_area, row.norwegian_field].filter(Boolean).join(' / ') || 'Unclassified')],
    [`Norwegian level ${year}`, row.norwegian_level == null ? 'Not in the register'
      : `Level ${escape(row.norwegian_level)} · ` +
        (row.norwegian_register_url ? `<a href="${escape(row.norwegian_register_url)}" target="_blank" rel="noopener noreferrer">this journal in the register ↗</a> · ` : '') +
        `<a href="${LEVELS_URL}" target="_blank" rel="noopener noreferrer">what levels mean ↗</a>`],
    ['Open access journal', row.is_open_access == null ? 'Unknown' : row.is_open_access ? 'Yes' : 'No'],
    ['Publication years', `${fmt(row.active_years)} of 5 with eligible output`],
    ['Reference coverage', E.isNumber(row.reference_coverage_pct) ? `${fmt(row.reference_coverage_pct, 2)}%` : 'Unavailable'],
  ];
  if (state.showPercentiles) details.push(['Final percentile', ranks.poolRanks.has(id)
    ? `${fmt(ranks.poolRanks.get(id), 1)} · among ${count(ranks.retained)} journals` : escape(ranks.reasons.get(id) || 'Not eligible')]);
  $('dialog-content').innerHTML = `<p class="dialog-label">JOURNAL DETAILS · ${year} · ${state.treatment.toUpperCase()}</p><h2 id="dialog-title">${escape(row.title)}</h2>` +
    `<dl>${details.map(([key, value]) => `<dt>${key}</dt><dd>${value}</dd>`).join('')}</dl>` +
    `<h3>Scores by year · ${escape(universeName(state.universe))} universe · ${state.treatment === 'raw' ? 'Raw' : 'Filtered'}</h3><div id="history"></div>` +
    `<p>Publications: ${fmt(E.columnValue(row, state, ranks, 'publications'))} eligible articles and reviews from ${year - 5}–${year - 1}. ` +
    `Citations in ${year}: ${fmt(E.columnValue(row, state, ranks, 'citations'))}, excluding journal self-citations.</p>`;
  $('journal-dialog').showModal();
  showHistory(id);
}

// The journal's row in every score year of the run. tools/build_site_data.py splits all years into
// 100 small history files by the last two digits of the journal ID, so only one small file is downloaded.
async function journalHistory(id) {
  const url = `data/history/${id.slice(-2)}.parquet`;
  if (!historyFiles.has(url)) historyFiles.set(url, fetchOk(url).then(response => response.arrayBuffer())
    .catch(error => { historyFiles.delete(url); throw error; })); // a failed download is retried next time
  const found = await parquetReadObjects({ file: await historyFiles.get(url), filter: { openalex_id: { $eq: id } } });
  const byYear = new Map(found.map(row => [Number(row.score_year), E.toNumbers(row)]));
  return index.years.map(entry => [entry, byYear.get(entry.year) ?? null]);
}

async function showHistory(id) {
  const request = ++historyRequest; // ignore late answers for a journal that is no longer shown
  const u = state.universe, metrics = Object.keys(METRICS);
  $('history').innerHTML = '<p>Loading the other years…</p>';
  try {
    const history = await journalHistory(id);
    if (request !== historyRequest) return;
    const cells = r => !r ? `<td colspan="${4 + metrics.length}" class="missing">Not in the data for this year</td>`
      : `<td>${r[`in_${u}`] ? 'Yes' : 'No'}</td><td>${escape(r.norwegian_level ?? '—')}</td>` +
        `<td>${fmt(r[`publications_${state.treatment}`])}</td><td>${E.isNumber(r.reference_coverage_pct) ? `${fmt(r.reference_coverage_pct, 1)}%` : '—'}</td>` +
        metrics.map(metric => `<td>${r[`in_${u}`] ? fmtScore(E.score(r, state, u, metric), metric) : '—'}</td>`).join('');
    const yearCell = entry => `<th scope="row" class="align-left">${entry.year}` +
      `<span class="vintage">${entry.status} · ${(entry.openalex_snapshot ?? '').slice(0, 7)}</span></th>`;
    $('history').innerHTML = '<table><thead><tr><th scope="col" class="align-left">Score year</th><th scope="col">In universe</th>' +
      '<th scope="col">Level</th><th scope="col">Publications</th><th scope="col">Ref. coverage</th>' +
      `${Object.values(METRICS).map(m => `<th scope="col">${m.short}</th>`).join('')}</tr></thead><tbody>` +
      history.map(([entry, r]) => `<tr class="${entry.year === year ? 'current-year' : ''}">${yearCell(entry)}${cells(r)}</tr>`).join('') +
      '</tbody></table>';
  } catch (error) {
    if (request === historyRequest) $('history').textContent = `The other years could not be loaded (${error.message}).`;
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

function csvHeader(key) {
  if (key === 'fieldPct') return 'field_percentile';
  if (key === 'poolPct') return 'pool_percentile';
  if (key === 'publications' || key === 'citations') return `${key}_${state.treatment}`;
  if (key.startsWith('score:')) return `${key.split(':')[1]}_${state.universe}_${state.treatment}`;
  return key;
}

function downloadView() {
  const entry = publishedYear();
  const settings = { score_year: year, data_status: entry.status, run: entry.run, openalex_snapshot: entry.openalex_snapshot,
    universe: state.universe, treatment: state.treatment };
  if (state.showPercentiles) Object.assign(settings, {
    ranking_indicator: state.metric, percentiles_grouped_by: E.FIELD_COLUMN, coverage_strictly_above_pct: state.minCoverage < 0 ? 'none' : state.minCoverage,
    minimum_output_years: state.minYears, retained_top_pct_per_field: state.topPercent, final_pool_size: ranks.retained,
  });
  const reason = row => (state.showPercentiles ? [ranks.reasons.get(row.openalex_id) || ''] : []);
  const header = ['openalex_id', `in_${state.universe}`, ...columns.map(c => csvHeader(c.key)), ...E.FIELD_DETAIL_COLUMNS,
    ...(state.showPercentiles ? ['percentile_exclusion_reason'] : []), ...Object.keys(settings)];
  const lines = visible.map(row => [row.openalex_id, row[`in_${state.universe}`],
    ...columns.map(c => E.columnValue(row, state, ranks, c.key)), ...E.FIELD_DETAIL_COLUMNS.map(c => row[c]), ...reason(row), ...Object.values(settings)]);
  saveCsv(`opindx-${year}-${state.treatment}-view.csv`, [csvLines([header, ...lines])]);
}

function showDownloadChoices() {
  const box = (value, label) => `<label class="check-line"><input type="checkbox" value="${escape(value)}" checked>${escape(label)}</label>`;
  const universes = Object.assign({}, ...index.years.map(entry => entry.universes));
  const vintage = entry => `${entry.year} (${entry.status} · ${(entry.openalex_snapshot ?? '').slice(0, 7) || 'date unknown'})`;
  $('download-years').innerHTML = '<legend class="visually-hidden">Score years</legend>' +
    index.years.map(entry => box(entry.year, vintage(entry))).join('');
  $('download-universes').innerHTML = '<legend>Universes</legend>' + Object.entries(universes).map(([u, label]) => box(u, label)).join('');
  showYearChoice();
  $('download-files').innerHTML = 'Complete files per score year, all columns (Parquet): ' +
    index.years.map(entry => `<a href="data/scores_${entry.year}.parquet" download>${entry.year}</a>`).join(' · ') + '.';
  $('download-older').innerHTML = index.releases_url
    ? `<strong>Older versions of the data.</strong> The files above are the data set published now. Every earlier data run stays available on the <a href="${escape(index.releases_url)}">releases page</a>, one release per run, each with the same files per score year.`
    : '';
}

const chosenBoxes = id => [...$(id).querySelectorAll('input:checked')].map(input => input.value);

// The closed dropdown has to say what is chosen, because the checkboxes are then out of sight.
function showYearChoice() {
  const chosen = chosenBoxes('download-years');
  $('download-years-summary').textContent = !chosen.length ? 'No score years'
    : chosen.length === index.years.length ? 'All score years'
    : chosen.length === 1 ? `Score year ${chosen[0]}` : `${chosen.length} score years`;
}

// One CSV with the chosen years and universes, built one year at a time to limit memory use.
async function downloadSelection() {
  const years = chosenBoxes('download-years').map(Number), universes = chosenBoxes('download-universes');
  const status = text => { $('download-status').textContent = text; };
  if (!years.length || !universes.length) return status('Choose at least one score year and one universe.');
  const universeColumns = u => [`in_${u}`, ...Object.keys(METRICS).flatMap(metric => [`${metric}_${u}_raw`, `${metric}_${u}_filtered`])];
  const columns = [...BASE_COLUMNS, ...E.FIELD_DETAIL_COLUMNS, ...universes.flatMap(universeColumns)];
  const source = ['data_status', 'run', 'openalex_snapshot']; // where this score year's numbers come from
  const parts = [csvLines([[...columns, ...source]])];
  let total = 0;
  $('download-build').disabled = true;
  try {
    for (const [i, y] of years.entries()) {
      status(`Preparing score year ${y} (${i + 1} of ${years.length})…`);
      const buffer = await (await fetchOk(`data/scores_${y}.parquet`)).arrayBuffer();
      // A year only has the universes of its own run; columns it lacks stay empty in the CSV.
      const yearEntry = index.years.find(entry => entry.year === y);
      const inYear = universes.filter(u => u in yearEntry.universes);
      const available = [...BASE_COLUMNS, ...(yearEntry.field_assignment ? E.FIELD_DETAIL_COLUMNS : []), ...inYear.flatMap(universeColumns)];
      const kept = (await parquetReadObjects({ file: buffer, columns: available })).filter(row => inYear.some(u => row[`in_${u}`]));
      const from = [yearEntry.status, yearEntry.run, yearEntry.openalex_snapshot];
      if (kept.length) parts.push('\r\n', csvLines(kept.map(row => [...columns.map(column => row[column]), ...from])));
      total += kept.length;
    }
    saveCsv(`opindx-${years.join('-')}-${universes.join('-')}.csv`, parts);
    status(`Saved ${count(total)} rows · ${years.length === 1 ? `score year ${years[0]}` : `${years.length} score years`}` +
      ` · journals in ${universes.length === 1 ? 'the chosen universe' : 'at least one chosen universe'}.`);
  } catch (error) {
    status(`The download could not be prepared (${error.message}).`);
  } finally {
    $('download-build').disabled = false;
  }
}

// ---- Events ----

const update = changes => { Object.assign(state, changes, { page: 0 }); render(); };

for (const [id, key] of Object.entries(SELECTS)) $(id).addEventListener('change', event => {
  const value = NUMERIC.has(key) ? Number(event.target.value) : event.target.value;
  update({ [key]: value });
});
for (const [id, key] of Object.entries(CHECKBOXES)) $(id).addEventListener('change', event => update({ [key]: event.target.checked }));
$('year').addEventListener('change', event => loadYear(Number(event.target.value)).catch(showError));
$('search').addEventListener('input', event => update({ query: event.target.value }));
$('include-zero').addEventListener('change', event => update({ treatment: event.target.checked ? 'raw' : 'filtered' }));
$('top-percent').addEventListener('change', event => {
  const value = Number(event.target.value);
  const topPercent = Number.isFinite(value) ? Math.min(100, Math.max(1, Math.round(value))) : 100;
  event.target.value = topPercent;
  update({ topPercent });
});
$('universe').addEventListener('change', event => update({ universe: event.target.value }));
$('settings-toggle').addEventListener('click', () => {
  const open = $('settings').hidden;
  $('settings').hidden = !open;
  $('settings-toggle').setAttribute('aria-expanded', String(open));
  $('settings-toggle').textContent = open ? 'Settings −' : 'Settings +';
});
$('table-head').addEventListener('click', event => {
  const key = event.target.closest('[data-sort]')?.dataset.sort;
  if (!key) return;
  const textColumn = ['title', 'oa_field'].includes(key);
  update({ sortKey: key, sortDirection: state.sortKey === key ? -state.sortDirection : textColumn ? 1 : -1 });
  $('table-head').querySelector(`[data-sort="${key}"]`)?.focus({ preventScroll: true });
});
$('table-body').addEventListener('click', event => {
  const id = event.target.closest('[data-journal]')?.dataset.journal;
  if (id) showJournal(id);
});
for (const [key, filter] of Object.entries(FILTERS)) {
  $(filter.search).addEventListener('input', () => drawFilter(key));
  $(filter.list).addEventListener('change', event => {
    const value = event.target.value;
    update({ [key]: event.target.checked ? [...state[key], value] : state[key].filter(chosen => chosen !== value) });
    drawFilter(key);
  });
}
$('previous').addEventListener('click', () => { state.page--; drawPage(); });
$('next').addEventListener('click', () => { state.page++; drawPage(); });
$('download-view').addEventListener('click', downloadView);
$('download-build').addEventListener('click', downloadSelection);
$('download-years').addEventListener('change', showYearChoice);
// A dropdown is expected to close when you click elsewhere.
document.addEventListener('click', event => {
  if ($('download-years-picker').open && !$('download-years-picker').contains(event.target)) $('download-years-picker').open = false;
});
$('close-dialog').addEventListener('click', () => $('journal-dialog').close());
$('reset-percentiles').addEventListener('click', () => {
  update(Object.fromEntries(PERCENTILE_SETTINGS.map(key => [key, DEFAULTS[key]])));
  syncControls();
});
$('reset').addEventListener('click', () => {
  state = { ...DEFAULTS };
  if (!universeIds().includes(state.universe)) state.universe = universeIds()[0];
  fillFilters(); syncControls(); render();
});

// ---- Start ----

$('metric').innerHTML = Object.entries(METRICS).map(([metric, m]) => option(metric, `${m.short} · ${m.name}`)).join('');
try {
  index = await (await fetchOk('data/index.json')).json();
  if (!index.years.length) throw new Error('no data runs published yet');
  $('year').innerHTML = index.years.map(entry => option(entry.year, entry.year)).join('');
  showDownloadChoices();
  await loadYear(index.years[0].year);
} catch (error) {
  showError(error);
}
