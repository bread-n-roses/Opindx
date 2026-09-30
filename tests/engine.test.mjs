// Tests for the ranking logic in site/engine.js. Run with: node tests/engine.test.mjs
import * as E from '../site/engine.js';

let failures = 0;
function check(name, test) {
  try { test(); console.log(`PASS ${name}`); } catch (error) { failures++; console.log(`FAIL ${name}: ${error.message}`); }
}
function equal(actual, expected) {
  if (actual !== expected) throw new Error(`expected ${expected}, got ${actual}`);
}

const base = { treatment: 'raw', universe: 'n', metric: 'per_article', minCoverage: 20, minYears: 4,
  topPercent: 70, query: '', fields: [], publishers: [], oaOnly: false, poolOnly: false,
  sortKey: 'score:per_article', sortDirection: -1 };

function journal(id, score, { coverage = 90, activeYears = 5, field = 'Economics', inN = true } = {}) {
  return { openalex_id: id, title: id, publisher: 'Test publisher', oa_domain: 'Social Sciences', oa_field: field,
    reference_coverage_pct: coverage, active_years: activeYears, publications_raw: 100, publications_filtered: 80,
    in_n: inN, share_n_raw: score == null ? null : score / 100, per_article_n_raw: score,
    share_n_filtered: 0.01, per_article_n_filtered: score == null ? null : score / 2 };
}
const fixture = E.prepareRows([journal('zero', 0), journal('tieA', 1), journal('tieB', 1), journal('high', 3),
  journal('boundary', 100, { coverage: 20 }), journal('short', 80, { activeYears: 3 }),
  journal('missing', null), journal('otherField', 8, { field: 'Medicine' }), journal('outside', 50, { inN: false })]);

check('Percentiles keep zero and give ties their highest shared rank', () => {
  const p = E.percentiles([{ id: 'a', value: 0 }, { id: 'b', value: 1 }, { id: 'c', value: 1 }, { id: 'd', value: 3 }]);
  equal(p.get('a'), 25); equal(p.get('b'), 75); equal(p.get('c'), 75); equal(p.get('d'), 100);
});
check('Universe, missing-score, coverage and history requirements apply before the top-share cut', () => {
  const r = E.rank(fixture, base);
  equal(r.qualified, 5); equal(r.groups, 2); equal(r.retained, 4);
  equal(r.reasons.get('outside'), 'Not in the selected universe');
  equal(r.reasons.get('missing'), 'No score in the selected universe');
  equal(r.reasons.get('boundary'), 'Below the reference coverage requirement');
  equal(r.reasons.get('short'), 'Below the publication history requirement');
  equal(r.fieldRanks.get('zero'), 25); equal(r.poolRanks.has('zero'), false);
  equal(r.poolRanks.get('tieA'), 50); equal(r.poolRanks.get('high'), 75); equal(r.poolRanks.get('otherField'), 100);
});
check('The top-share cut is strict, and ties stay together', () => {
  const r = E.rank(fixture, { ...base, topPercent: 25 });
  equal(r.retained, 2); equal(r.poolRanks.has('tieA'), false); equal(r.poolRanks.has('tieB'), false);
});
check('Search and filters change what is shown, not the percentiles', () => {
  const r = E.rank(fixture, base);
  const filtered = { ...base, query: 'high', publishers: ['Test publisher'], poolOnly: true };
  equal(JSON.stringify([...E.rank(fixture, filtered).poolRanks]), JSON.stringify([...r.poolRanks]));
  equal(E.view(fixture, filtered, r).length, 1);
  equal(E.view(fixture, { ...base, fields: ['Medicine'] }, r).length, 1);
  equal(E.view(fixture, { ...base, publishers: ['Other publisher'] }, r).length, 0);
});
check('Journals outside the universe are never shown; missing scores sort last, zero stays a number', () => {
  const r = E.rank(fixture, base);
  equal(E.view(fixture, base, r).some(row => row.openalex_id === 'outside'), false);
  for (const direction of [1, -1]) equal(E.view(fixture, { ...base, sortDirection: direction }, r).at(-1).openalex_id, 'missing');
  equal(E.view(fixture, { ...base, sortDirection: 1 }, r)[0].openalex_id, 'zero');
});
check('Treatment switches scores and publication counts, not coverage', () => {
  const row = journal('treatment', 4), filtered = { ...base, treatment: 'filtered' };
  equal(E.score(row, base), 4); equal(E.score(row, filtered), 2);
  equal(E.columnValue(row, base, null, 'publications'), 100); equal(E.columnValue(row, filtered, null, 'publications'), 80);
  equal(E.columnValue(row, base, null, 'reference_coverage_pct'), E.columnValue(row, filtered, null, 'reference_coverage_pct'));
});
check('Empty pools and missing coverage stay undefined instead of becoming zero', () => {
  equal(E.rank(fixture, { ...base, minCoverage: 100 }).retained, 0);
  const rows = [journal('unknownCoverage', 1, { coverage: null })];
  equal(E.rank(rows, base).qualified, 0);
  equal(E.rank(rows, { ...base, minCoverage: -1 }).qualified, 1);
  equal(E.percentiles([]).size, 0);
});

check('Rolling field shares are display metadata, never group keys', () => {
  const a = {...journal('a', 1, {field:'Medicine'}), score_year:2026, oa_field_modal_share:.195,
    oa_field_classification_coverage:.98, oa_field_tied_modes:1};
  const b = {...journal('b', 2, {field:'Medicine'}), oa_field_modal_share:.9};
  equal(E.fieldLabel(a), 'Medicine (19.5%)');
  equal(E.fieldLabel(b), 'Medicine (90.0%)');
  equal(E.rank([a,b], base).groups, 1);
  equal(E.fieldDescription(a).includes('2021-2025'), true);
  equal(E.fieldDescription(a).includes('98.0%'), true);
  equal(E.fieldLabel({oa_field:'Unknown',oa_field_modal_share:null}), 'Unknown');
  equal(E.fieldLabel({oa_field:'Medicine'}), 'Medicine');
  equal(E.fieldDescription({oa_field:'Medicine'}).includes('historical'), true);
  equal(E.fieldDescription({...a,oa_field_tied_modes:2}).includes('2 fields tied'), true);
});

if (failures) throw new Error(`${failures} test(s) failed`);
