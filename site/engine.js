// Table and ranking logic, separate from the page so it can be tested.
// A row is one journal in one score year, with the columns described in SPEC.md.

export const isNumber = value => typeof value === 'number' && Number.isFinite(value);

export const score = (row, state, universe = state.universe, metric = state.metric) =>
  row[`${metric}_${universe}_${state.treatment}`] ?? null;

// Percentiles are grouped by OpenAlex field (see SPEC.md).
export const FIELD_COLUMN = 'oa_field';
export const field = row => row[FIELD_COLUMN] || 'Unclassified';

// Display percentages separately; ranking and filtering use the plain field.
export const FIELD_DETAIL_COLUMNS = ['oa_field_modal_share', 'oa_field_modal_works',
  'oa_field_classified_works', 'oa_field_classification_coverage', 'oa_field_tied_modes'];
export function fieldLabel(row) {
  const label = field(row), share = row.oa_field_modal_share;
  return isNumber(share) && share >= 0 && share <= 1 && label !== 'Unknown'
    ? `${label} (${(100 * share).toFixed(1)}%)` : label;
}
export function fieldDescription(row) {
  if (!Object.hasOwn(row, 'oa_field_modal_share')) return 'OpenAlex source field from this historical data run.';
  const window = `${row.score_year - 5}-${row.score_year - 1}`;
  const coverage = row.oa_field_classification_coverage;
  const classified = isNumber(coverage) ? `${(100 * coverage).toFixed(1)}% of eligible works have a field.` : 'No eligible works in this window.';
  const mode = field(row) === 'Unknown' ? 'No classified article/review works.'
    : 'Most frequent primary-topic broad field; percentage of classified article/review works. It does not describe the whole journal.';
  const ties = row.oa_field_tied_modes > 1 ? ` ${row.oa_field_tied_modes} fields tied; alphabetical choice shown.` : '';
  return `${window} publications. ${mode} ${classified}${ties}`;
}


export const compareText = new Intl.Collator('en').compare;

// 64-bit integers are read as BigInt; the table works with ordinary numbers.
export function toNumbers(row) {
  for (const key in row) if (typeof row[key] === 'bigint') row[key] = Number(row[key]);
  return row;
}

// Run once per loaded year: converts integers and stores each row's alphabetical position,
// so sorting never has to compare titles as text (slow for 100k rows).
export function prepareRows(rows) {
  rows.forEach(toNumbers);
  [...rows].sort((a, b) => compareText(a.title, b.title)).forEach((row, i) => { row.titleOrder = i; });
  return rows;
}

// Empirical cumulative distribution: tied values receive their maximum rank.
export function percentiles(items) {
  const sorted = [...items].sort((a, b) => a.value - b.value);
  const result = new Map();
  for (let i = 0; i < sorted.length;) {
    let end = i + 1;
    while (end < sorted.length && sorted[end].value === sorted[i].value) end++;
    for (let k = i; k < end; k++) result.set(sorted[k].id, 100 * end / sorted.length);
    i = end;
  }
  return result;
}

function exclusionReason(row, state) {
  const coverage = row.reference_coverage_pct, years = row.active_years;
  if (!row[`in_${state.universe}`]) return 'Not in the selected universe';
  if (!isNumber(score(row, state))) return 'No score in the selected universe';
  if (state.minCoverage >= 0 && !isNumber(coverage)) return 'Reference coverage unavailable';
  if (state.minCoverage >= 0 && coverage <= state.minCoverage) return 'Below the reference coverage requirement';
  if (state.minYears > 0 && !isNumber(years)) return 'Publication history unavailable';
  if (state.minYears > 0 && years < state.minYears) return 'Below the publication history requirement';
  return '';
}

// Percentiles within each field, then across all journals retained in the top share of their field.
// The ranking universe is always the selected universe.
export function rank(rows, state) {
  const groups = new Map();
  const reasons = new Map();
  for (const row of rows) {
    const reason = exclusionReason(row, state);
    if (reason) { reasons.set(row.openalex_id, reason); continue; }
    const key = field(row);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push({ id: row.openalex_id, value: score(row, state) });
  }
  const fieldRanks = new Map();
  const fieldCounts = new Map();
  const retained = [];
  for (const members of groups.values()) {
    const ranks = percentiles(members);
    for (const item of members) {
      const pct = ranks.get(item.id);
      fieldRanks.set(item.id, pct);
      fieldCounts.set(item.id, members.length);
      if (pct > 100 - state.topPercent) retained.push(item);
      else reasons.set(item.id, 'Outside the retained top share of its field');
    }
  }
  return { fieldRanks, fieldCounts, poolRanks: percentiles(retained), reasons,
    qualified: fieldRanks.size, retained: retained.length, groups: groups.size };
}

// The value behind a table column, used for display, sorting and CSV export.
export function columnValue(row, state, ranks, key) {
  if (key === 'fieldPct') return ranks.fieldRanks.get(row.openalex_id) ?? null;
  if (key === 'poolPct') return ranks.poolRanks.get(row.openalex_id) ?? null;
  if (key === 'publications' || key === 'citations') return row[`${key}_${state.treatment}`] ?? null;
  if (key.startsWith('score:')) return score(row, state, state.universe, key.split(':')[1]);
  return row[key] ?? null;
}

// Rows to show: only journals in the selected universe. Filters and search change what is visible,
// never the percentiles. Empty filter lists mean "no restriction".
export function view(rows, state, ranks) {
  const query = state.query.toLowerCase().trim();
  const fields = new Set(state.fields), publishers = new Set(state.publishers);
  const shown = rows.filter(row =>
    row[`in_${state.universe}`] &&
    (!fields.size || fields.has(row[FIELD_COLUMN])) &&
    (!publishers.size || publishers.has(row.publisher)) &&
    (!state.oaOnly || row.is_open_access === true) &&
    (!state.poolOnly || ranks.poolRanks.has(row.openalex_id)) &&
    (!query || searchText(row).includes(query)));
  // Missing values sort last in both directions; ties fall back to the title.
  const sortValue = row => state.sortKey === 'title' ? row.titleOrder : columnValue(row, state, ranks, state.sortKey);
  const keyed = shown.map(row => [sortValue(row), row]);
  keyed.sort(([a, rowA], [b, rowB]) => {
    if (a == null && b == null) return rowA.titleOrder - rowB.titleOrder;
    if (a == null) return 1;
    if (b == null) return -1;
    const cmp = typeof a === 'number' && typeof b === 'number' ? a - b : compareText(String(a), String(b));
    return cmp * state.sortDirection || rowA.titleOrder - rowB.titleOrder;
  });
  return keyed.map(([, row]) => row);
}

export const searchText = row => row.search ??= [row.openalex_id, row.title, row.issns, row.publisher,
  row.oa_domain, row.oa_field, row.norwegian_area, row.norwegian_field].filter(Boolean).join(' ').toLowerCase();
