// Presets define the metrics and comparison pool. Search/display filters are independent.
export const PRESETS = Object.freeze({
  full: Object.freeze({universe: 'oa', treatment: 'filtered', showPercentiles: true, percentileMode: 'field',
    metric: 'per_article', minCoverage: 20, minYears: 4, topPercent: 100, poolOnly: false}),
  'ef-ais': Object.freeze({universe: 'n', treatment: 'filtered', showPercentiles: true, percentileMode: 'field',
    metric: 'per_article', minCoverage: 20, minYears: 4, topPercent: 100, poolOnly: false}),
  'vu-sbe': Object.freeze({universe: 'n', treatment: 'filtered', showPercentiles: true, percentileMode: 'total',
    metric: 'per_article', minCoverage: 20, minYears: 4, topPercent: 75, poolOnly: true}),
});
export const DEFAULTS = Object.freeze({...PRESETS.full, query: '', fields: [],
  oaOnly: false, sortKey: 'score:per_article', sortDirection: -1, page: 0});

export function identifyPreset(state) {
  return Object.entries(PRESETS).find(([, values]) =>
    Object.entries(values).every(([key, value]) => state[key] === value))?.[0] ?? 'custom';
}

export function startingSelection(years, search = '', now = new Date()) {
  const available = years.map(entry => Number(entry.year)).sort((a, b) => b - a);
  if (!available.length) throw new Error('no data runs published yet');
  const params = new URLSearchParams(search);
  const requested = Number(params.get('year'));
  const year = available.includes(requested) ? requested :
    available.find(value => value < now.getFullYear()) ?? available[0];
  const preset = Object.hasOwn(PRESETS, params.get('preset')) ? params.get('preset') : 'full';
  return {year, preset};
}
