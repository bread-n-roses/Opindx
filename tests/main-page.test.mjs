import assert from 'node:assert/strict';
import './section-nav.test.mjs';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import * as E from '../site/engine.js';
import * as D from '../site/journal-details.js';
import * as P from '../site/presets.js';
import {workbookBytes} from '../site/selection-download.js';
import * as XLSX from '../site/vendor/sheetjs-0.20.3.mjs';

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log(`PASS ${name}`); }
const source = readFileSync(new URL('../site/app.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../site/index.html', import.meta.url), 'utf8');
function selectPercentiles(h, mode) {
  h.elements.get('percentiles-field').checked = ['field','both'].includes(mode);
  h.elements.get('percentiles-total').checked = ['total','both'].includes(mode);
  h.elements.get('percentile-choices').events.change({target:h.elements.get(mode === 'none' ? 'percentiles-none' : 'percentiles-field')});
}
const main = source.slice(0, source.indexOf('// ---- Start ----')).replace(/^import .*;\r?\n/gm, '');
const years = [2026,2025,2024].map(year => ({year,universes:{n:'Norwegian Register',oa:'OpenAlex'},fields:{n:['Economics'],oa:['Economics','Medicine']},status:year === 2024 ? 'frozen':'live',openalex_snapshot:'2026-09-23'}));
const records = Array.from({length:40}, (_,i) => ({openalex_id:`S${i}`,title:`Journal ${String(i).padStart(2,'0')}`,in_oa:true,in_n:i < 20,
  publisher:i < 20 ? 'Shared Publisher':'Outside Publisher',oa_field:i < 20 ? 'Economics':'Medicine',is_open_access:i % 2 === 0,
  oa_field_modal_share:0.625,reference_coverage_pct:90,active_years:5,share_oa_filtered:i/100,per_article_oa_filtered:i === 39 ? null:i,
  share_n_filtered:i/100,per_article_n_filtered:i,
  citations_n_filtered:i, citations_oa_filtered:i,citations_n_raw:i+1,citations_oa_raw:i+2,per_article_oa_raw:1000+i,publications_filtered:i,citations_filtered:i,
  issns:'1234-5678'}));

// Stable custom fixture for existing ranking/export tests; preset defaults are tested separately.
const filteredFixture = {...P.PRESETS.full,treatment:'filtered',minCoverage:20,minYears:4};
function harness(fetchOverride, workbookWriter = workbookBytes, initialSettings = filteredFixture) {
  const elements = new Map();
  function element(id) {
    return {id, hidden:false, disabled:false, checked:false, value:'', innerHTML:'', textContent:'', open:false,
      attrs:{},events:{},children:new Map(),
      addEventListener(type, fn) { this.events[type] = fn; },
      setAttribute(key,value) { this.attrs[key] = value; },
      querySelector(key) { if (!this.children.has(key)) this.children.set(key, element(key)); return this.children.get(key); },
      focus() {}, contains(target) { return target === this; },
      showModal() { this.open = true; }, close() { this.open = false; this.events.close?.(); },
      emit(type, changes = {}) { Object.assign(this, changes); return this.events[type]?.({target:this}); },
    };
  }
  for (const match of html.matchAll(/\bid="([^"]+)"/g)) {
    assert.equal(elements.has(match[1]), false, `Duplicate ID ${match[1]}`);
    elements.set(match[1], element(match[1]));
  }
  elements.get('settings').hidden = true;
  elements.get('settings-help').hidden = true;
  const document = {getElementById(id) { assert.ok(elements.has(id), `Missing main-page element: ${id}`); return elements.get(id); },addEventListener() {}};
  let rankCalls = 0;
  const context = {document, E:{...E,rank(...args) { rankCalls++; return E.rank(...args); }},D,P,console,URL,Blob,setTimeout,workbookBytes:workbookWriter,
    fetch:fetchOverride ?? (async () => ({ok:true,arrayBuffer:async () => records})),
    parquetReadObjects:async ({file}) => file,
  };
  const api = vm.runInNewContext(main + `\n({
    init(data, settings) { index = data; state = {...state, ...settings}; }, loadYear, applyPreset, update, fillFilters, switchMetric,
    getState:()=>state, getYear:()=>year, getRows:()=>rows, getVisible:()=>visible,
    getColumns, getDisplayColumns:()=>columns,
    csv() { let output; saveCsv=(filename,parts)=>{output={filename,parts}}; downloadView(); return output; },
    selectionExport,
    async xlsx() { let output; saveBinary=(filename,bytes)=>{output={filename,bytes}}; await downloadXlsx(); return output; },
  })`,context);
  api.init({years,releases_url:'https://example.org/releases'},initialSettings);
  return {api,elements,rankCalls:()=>rankCalls};
}

await check('Calendar defaults and VU bookmark choose an available complete year', () => {
  const now = new Date(2026,9,1);
  assert.deepEqual(P.startingSelection(years,'',now),{year:2025,preset:'ef-ais'});
  assert.deepEqual(P.startingSelection([...years].reverse(),'?preset=vu-sbe',now),{year:2025,preset:'vu-sbe'});
  assert.equal(P.startingSelection(years,'?year=2026',now).year,2026);
  assert.equal(P.startingSelection(years,'?year=2099',now).year,2025);
  assert.equal(P.startingSelection([{year:2026}],'',now).year,2026);
  assert.throws(()=>P.startingSelection([]),/no data/);
});
await check('First load shows headers and one blank row; later loads preserve the populated table', async () => {
  const initialHead = html.match(/<thead id="table-head">([\s\S]*?)<\/thead>/)[1];
  const initialBody = html.match(/<tbody id="table-body">([\s\S]*?)<\/tbody>/)[1];
  assert.match(html, /<select id="year"><option value="2025" selected>2025 \(live\)<\/option>/);
  assert.match(html, /<select id="universe"><option value="n" selected>Norwegian Register<\/option>/);
  assert.match(initialHead, /Citations: 2025/);
  assert.match(initialHead, /Publications: 2020&ndash;2024/);
  assert.equal((initialHead.match(/<tr\b/g) || []).length,2);
  assert.equal((initialBody.match(/<tr\b/g) || []).length,1);
  for (const title of ['Journal title','OpenAlex field','Publications used','Citations used','ANS','Field']) assert.ok(initialHead.includes(title));
  assert.match(initialBody,/class="loading-row" aria-hidden="true"/);
  assert.equal((initialBody.match(/<td\b/g) || []).length,7);
  assert.ok(html.indexOf('</tbody>') < html.indexOf('class="table-footer"'));
  const pending = new Map();
  const h = harness(url => new Promise(resolve => pending.set(url,resolve)));
  const first = h.api.loadYear(2024);
  assert.equal(h.api.getYear(),undefined);
  assert.match(h.elements.get('table-head').innerHTML,/Citations: 2024/);
  assert.doesNotMatch(h.elements.get('table-head').innerHTML,/Frozen metrics|Live metrics/);
  assert.doesNotMatch(h.elements.get('table-head').innerHTML,/data-sort=|data-hide-column=/);
  assert.equal((h.elements.get('table-body').innerHTML.match(/<tr\b/g)||[]).length,1);
  assert.equal(h.elements.get('selection-actions').hidden,true);
  pending.get('data/scores_2024.parquet')({ok:true,arrayBuffer:async()=>records}); await first;
  assert.doesNotMatch(h.elements.get('table-body').innerHTML,/loading-row/);
  assert.equal((h.elements.get('table-body').innerHTML.match(/<tr\b/g)||[]).length,25);
  const loadedHead = h.elements.get('table-head').innerHTML;
  const loadedBody = h.elements.get('table-body').innerHTML;
  const next = h.api.loadYear(2025);
  assert.equal(h.elements.get('table-head').innerHTML,loadedHead);
  assert.equal(h.elements.get('table-body').innerHTML,loadedBody);
  pending.get('data/scores_2025.parquet')({ok:true,arrayBuffer:async()=>records}); await next;
  assert.match(h.elements.get('table-head').innerHTML,/Citations: 2025/);
  assert.doesNotMatch(h.elements.get('table-body').innerHTML,/loading-row/);
});
await check('Year and universe can change before the first table; late responses cannot override them', async () => {
  const pending = new Map();
  const h = harness(url => new Promise(resolve => pending.set(url,resolve)));
  const first = h.api.loadYear(2025);
  assert.equal(h.elements.get('search-controls').disabled,false);
  assert.equal(h.elements.get('year').disabled,false);
  assert.equal(h.elements.get('universe').disabled,false);
  assert.equal(h.elements.get('preset').disabled,false);
  assert.equal(h.elements.get('search').disabled,false);
  h.elements.get('universe').emit('change',{value:'n'});
  assert.equal(h.elements.get('universe').value,'n');
  assert.match(h.elements.get('table-head').innerHTML,/>ANS<\//);
  const second = h.elements.get('year').emit('change',{value:'2024'});
  assert.match(h.elements.get('table-head').innerHTML,/Citations: 2024/);
  assert.equal(h.elements.get('universe').value,'n');
  assert.match(h.elements.get('table-head').innerHTML,/>ANS<\//);
  pending.get('data/scores_2024.parquet')({ok:true,arrayBuffer:async()=>records}); await second;
  assert.equal(h.api.getYear(),2024);
  assert.equal(h.api.getState().universe,'n');
  assert.equal(h.api.getVisible().length,20);
  assert.equal(h.elements.get('preset').disabled,false);
  pending.get('data/scores_2025.parquet')({ok:true,arrayBuffer:async()=>records}); await first;
  assert.equal(h.api.getYear(),2024);
  assert.match(h.elements.get('table-head').innerHTML,/Citations: 2024/);
});
await check('Presets, fields and search apply before the first journal table arrives', async () => {
  let resolve;
  const h = harness(() => new Promise(done => {resolve=done;}));
  const first = h.api.loadYear(2025);
  assert.equal(h.elements.get('field-picker').querySelector('fieldset').disabled,false);
  assert.match(h.elements.get('field-filter').innerHTML,/Medicine/);
  h.elements.get('preset').emit('change',{value:'vu-sbe'});
  assert.equal(h.api.getState().universe,'n');
  assert.equal(h.api.getState().percentileMode,'total');
  assert.doesNotMatch(h.elements.get('field-filter').innerHTML,/Medicine/);
  assert.match(h.elements.get('table-head').innerHTML,/>Total · ANP<\/th>/);
  h.elements.get('field-filter').events.change({target:{value:'Economics',checked:true}});
  assert.equal(h.elements.get('field-summary').textContent,'Economics');
  h.api.update({query:'Journal 01'});
  resolve({ok:true,arrayBuffer:async()=>records}); await first;
  assert.deepEqual(Array.from(h.api.getState().fields),['Economics']);
  assert.equal(h.api.getState().query,'Journal 01');
  assert.equal(h.api.getState().percentileMode,'total');
});
await check('A custom filtered view preserves journals missing ANS and renders the selected settings', async () => {
  const h = harness(); await h.api.loadYear(2025);
  assert.equal(h.api.getVisible().length,40);
  assert.equal(h.api.getVisible().at(-1).openalex_id,'S39');
  assert.equal(h.api.getVisible().at(-1).share_oa_filtered,.39);
  assert.equal(h.api.getState().treatment,'filtered');
  assert.equal(h.rankCalls(),1);
  assert.equal(h.api.getState().showPercentiles,true);
  assert.equal(h.elements.get('percentiles-field').checked,true);
  assert.equal(h.elements.get('pool-only').checked,false);
  assert.match(h.elements.get('table-head').innerHTML,/class="percentile-group">Percentiles<\/th>/);
  assert.equal(h.elements.get('preset').value,'custom');
  assert.equal(h.elements.get('notice').hidden,false);
  assert.equal(h.elements.get('selection-actions').hidden,false);
  assert.equal(h.elements.get('notice-message').hidden,true);
  assert.equal(h.elements.get('selection-summary').hidden,false);
  assert.equal(h.elements.get('settings').hidden,true);
  assert.equal(h.elements.get('settings-help').hidden,true);
  assert.equal(h.api.getState().minCoverage,20);
  assert.equal(h.api.getState().minYears,4);
  assert.equal(h.api.getState().topPercent,100);
  assert.match(h.elements.get('table-head').innerHTML,/2020–2024 · Citations: 2025 · OpenAlex data: 2026-09/);
  assert.doesNotMatch(h.elements.get('table-body').innerHTML,/journal-id|1234-5678|openalex\.org/);
});
await check('Custom percentile requirements exclude boundary coverage and short histories without hiding journals', async () => {
  const sample = [
    {...records[0],reference_coverage_pct:20},
    {...records[1],reference_coverage_pct:20.01,active_years:4},
    {...records[2],active_years:3},
    {...records[3],active_years:4},
    {...records[4],reference_coverage_pct:null},
    {...records[39]},
  ];
  const h=harness(async()=>({ok:true,arrayBuffer:async()=>sample}));
  await h.api.loadYear(2025);
  assert.equal(h.api.getVisible().length,6);
  const ranks=E.rank(h.api.getRows(),h.api.getState());
  assert.equal([...ranks.poolRanks.keys()].sort().join(','),'S1,S3');
  for (const id of ['S0','S2','S4','S39']) {
    assert.equal(ranks.fieldRanks.has(id),false);
    assert.equal(ranks.poolRanks.has(id),false);
  }
  assert.match(h.elements.get('pool-summary').innerHTML,/<strong>2<\/strong> journals with percentile/);
  assert.equal(h.elements.get('preset').value,'custom');
});
await check('Advanced settings opens; presets close it; VU SBE applies 75%, four years and strict coverage', async () => {
  const h = harness(); await h.api.loadYear(2025);
  h.elements.get('settings-toggle').emit('click');
  assert.equal(h.elements.get('settings').hidden,false);
  assert.equal(h.elements.get('settings-help').hidden,false);
  assert.equal(h.elements.get('percentile-settings').disabled,false);
  h.elements.get('preset').emit('change',{value:'vu-sbe'});
  assert.equal(h.elements.get('settings').hidden,true);
  assert.equal(h.elements.get('settings-help').hidden,true);
  const state = h.api.getState();
  for (const [key,value] of Object.entries(P.PRESETS['vu-sbe']))
    assert.equal(state[key],key === 'averagePercentiles' ? false : value,key);
  assert.equal(h.elements.get('average-percentiles').disabled,true);
  assert.equal(h.elements.get('preset').value,'vu-sbe');
  assert.equal(h.api.getVisible().length,15);
  assert.match(h.elements.get('table-head').innerHTML,/class="percentile-group">Percentiles<\/th>/);
  assert.equal(h.elements.get('pool-only').checked,true);
  h.elements.get('include-zero').emit('change',{checked:true});
  assert.equal(h.elements.get('preset').value,'custom');
  h.elements.get('preset').emit('change',{value:'full'});
  assert.equal(h.api.getState().treatment,'filtered');
  assert.equal(h.elements.get('pool-only').checked,false);
});
await check('Display filters preserve presets; universe changes reconcile field choices without a publisher filter', async () => {
  const h = harness(); await h.api.loadYear(2025);
  h.api.update({fields:['Medicine'],query:'Journal',oaOnly:true});
  assert.equal(h.elements.get('preset').value,'custom');
  h.api.applyPreset('vu-sbe');
  assert.equal(h.api.getState().fields.length,0);
  assert.equal('publishers' in h.api.getState(),false);
  assert.doesNotMatch(h.elements.get('field-filter').innerHTML,/Medicine/);
  assert.equal(h.elements.has('publisher-list'),false);
  assert.equal(h.elements.has('more-options'),false);
  assert.equal(h.api.getState().query,'Journal');
  assert.equal(h.elements.get('preset').value,'vu-sbe');
});
await check('Manual percentile settings select Custom and turning them off restores missing records', async () => {
  const h = harness(); await h.api.loadYear(2025);
  h.elements.get('metric').emit('change',{value:'share'});
  assert.equal(h.api.getState().minCoverage,20);
  assert.equal(h.api.getState().minYears,4);
  assert.equal(h.api.getState().topPercent,100);
  assert.equal(h.api.getVisible().length,40);
  assert.equal(h.elements.get('preset').value,'custom');
  h.elements.get('metric').emit('change',{value:'per_article'});
  h.elements.get('pool-only').emit('change',{checked:true});
  assert.equal(h.api.getVisible().length,39);
  selectPercentiles(h,'none');
  assert.equal(h.api.getVisible().length,40);
  assert.equal(h.elements.get('pool-summary').hidden,true);
  assert.equal(h.elements.get('pool-only').disabled,true);
  selectPercentiles(h,'field');
  assert.equal(h.elements.get('pool-only').disabled,false);
  assert.equal(h.api.getVisible().length,39);
  h.api.applyPreset('custom'); assert.equal(h.elements.get('settings').hidden,false);
});
function reveal(h, reason) {
  h.elements.get('table-body').events.click({target:{closest:selector=>selector === '[data-reveal]' ? {dataset:{reveal:reason}} : null}});
}
await check('Hidden search notices reveal only the relevant setting and stay out of exports', async () => {
  const h = harness(); await h.api.loadYear(2025);
  h.api.applyPreset('vu-sbe'); h.api.update({query:'Journal 39'});
  assert.equal(h.api.getVisible().length,0);
  assert.match(h.elements.get('table-body').innerHTML,/Journal 39/);
  assert.match(h.elements.get('table-body').innerHTML,/Switch to OpenAlex universe/);
  assert.doesNotMatch(h.elements.get('table-body').innerHTML,/more-info|coverage-bar|data-journal=/);
  assert.equal(h.api.selectionExport().matrix.length,1);
  reveal(h,'universe');
  assert.equal(h.api.getState().universe,'oa');
  assert.equal(h.api.getState().treatment,'filtered');
  assert.equal(h.api.getState().topPercent,75);
  assert.equal(h.api.getState().poolOnly,true);
  assert.equal(h.api.getState().query,'Journal 39');
  assert.match(h.elements.get('table-body').innerHTML,/Show journals without percentiles/);
  reveal(h,'percentiles');
  assert.equal(h.api.getVisible().length,1);
  assert.equal(h.api.getVisible()[0].openalex_id,'S39');
  assert.equal(h.elements.get('pool-only').checked,false);
  assert.equal(h.api.selectionExport().matrix.length,2);
  assert.doesNotMatch(h.elements.get('table-body').innerHTML,/hidden-match/);
});
await check('Field and access exclusions appear only for a nonblank search and can be relaxed separately', async () => {
  const h = harness(); await h.api.loadYear(2025);
  h.api.update({fields:['Economics'],oaOnly:true});
  assert.doesNotMatch(h.elements.get('table-body').innerHTML,/hidden-match/);
  h.api.update({query:'Journal 21'});
  assert.match(h.elements.get('table-body').innerHTML,/Show all fields/);
  assert.match(h.elements.get('table-body').innerHTML,/Show also not-open-access journals/);
  assert.match(h.elements.get('table-body').innerHTML,/Medicine/);
  assert.equal(h.api.getVisible().length,0);
  reveal(h,'fields');
  assert.equal(h.api.getState().oaOnly,true);
  assert.equal(h.api.getState().fields.length,0);
  assert.equal(h.elements.get('all-fields').checked,true);
  reveal(h,'access');
  assert.equal(h.api.getVisible().length,1);
  assert.equal(h.api.getState().query,'Journal 21');
  h.api.update({query:'   ',fields:['Economics']});
  assert.doesNotMatch(h.elements.get('table-body').innerHTML,/hidden-match/);
});
await check('Eligible results precede paginated hidden notices; hidden columns and markup remain safe', async () => {
  const h = harness(); await h.api.loadYear(2025);
  h.api.applyPreset('ef-ais'); h.api.update({query:'Journal'});
  assert.equal(h.api.getVisible().length,20);
  const body=h.elements.get('table-body').innerHTML;
  assert.equal((body.match(/<tr/g)||[]).length,25);
  assert.equal((body.match(/class="hidden-match"/g)||[]).length,5);
  assert.equal(h.api.selectionExport().matrix.length,21);
  assert.match(h.elements.get('result-count').innerHTML,/<strong>20<\/strong>/);
  assert.match(h.elements.get('page-status').textContent,/40 search matches \(20 hidden\)/);
  h.elements.get('next').emit('click');
  assert.equal((h.elements.get('table-body').innerHTML.match(/class="hidden-match"/g)||[]).length,15);
  h.api.update({query:'Journal 39'});
  for (const col of [...h.api.getDisplayColumns()]) if(col.key !== 'title') hide(h,col.key);
  assert.match(h.elements.get('table-body').innerHTML,/hidden-match-field/);
  assert.doesNotMatch(h.elements.get('table-body').innerHTML,/colspan="0"/);
});
await check('New filtered default, full-coverage and VU presets have the requested requirements', async () => {
  const sample = [
    {...records[0],reference_coverage_pct:10,active_years:1},
    {...records[1],reference_coverage_pct:10.01,active_years:4},
    {...records[2],reference_coverage_pct:null,active_years:0},
    {...records[3],reference_coverage_pct:0,active_years:1},
    {...records[39]},
  ];
  const h=harness(async()=>({ok:true,arrayBuffer:async()=>sample}),workbookBytes,{});
  await h.api.loadYear(2025);
  assert.equal(h.elements.get('preset').value,'ef-ais');
  assert.deepEqual(Array.from(h.api.getVisible(),r=>r.openalex_id),['S1']);
  assert.equal(h.api.getState().minYears,4);
  assert.equal(h.elements.get('pool-only').checked,true);
  assert.equal(h.elements.get('include-zero').checked,false);
  h.api.update({oaOnly:true}); h.api.applyPreset('full');
  assert.equal(h.api.getState().oaOnly,false);
  assert.equal(h.api.getState().treatment,'filtered');
  assert.equal(h.api.getState().minCoverage,10);
  assert.equal(h.api.getState().minYears,4);
  assert.equal(h.api.getState().topPercent,100);
  assert.equal(h.api.getState().poolOnly,false);
  assert.equal(h.api.getVisible().length,5);
  assert.equal(E.rank(h.api.getRows(),h.api.getState()).fieldRanks.size,1);
  assert.equal(h.elements.get('include-zero').checked,false);
  h.api.applyPreset('vu-sbe');
  assert.equal(h.api.getState().percentileMode,'total');
  assert.equal(h.api.getState().minCoverage,20);
  assert.equal(h.api.getState().minYears,4);
  assert.equal(h.api.getState().topPercent,75);
  h.elements.get('reset').emit('click');
  assert.equal(h.elements.get('preset').value,'ef-ais');
});
await check('A late year response or error cannot replace the latest selected year', async () => {
  const pending = new Map();
  const h = harness(url => new Promise((resolve,reject)=>pending.set(url,{resolve,reject})));
  const old = h.api.loadYear(2026), latest = h.api.loadYear(2024);
  assert.match(h.elements.get('notice-message').textContent,/Loading citation year 2024/);
  assert.equal(h.elements.get('selection-actions').hidden,true);
  assert.equal(h.elements.get('selection-summary').hidden,true);
  pending.get('data/scores_2024.parquet').resolve({ok:true,arrayBuffer:async()=>records});
  await latest;
  pending.get('data/scores_2026.parquet').resolve({ok:true,arrayBuffer:async()=>[{...records[0],title:'Stale'}]});
  await old; assert.equal(h.api.getYear(),2024); assert.equal(h.api.getRows().length,40);
  const oldError = h.api.loadYear(2026), newer = h.api.loadYear(2025);
  pending.get('data/scores_2025.parquet').resolve({ok:true,arrayBuffer:async()=>records}); await newer;
  pending.get('data/scores_2026.parquet').reject(new Error('stale error')); await oldError;
  assert.equal(h.api.getYear(),2025); assert.equal(h.elements.get('notice').hidden,false);
  assert.equal(h.elements.get('selection-actions').hidden,false);
  assert.equal(h.elements.get('notice-message').hidden,true);
  assert.equal(h.elements.get('selection-summary').hidden,false);
  const failed = h.api.loadYear(2026);
  assert.equal(h.elements.get('selection-actions').hidden,true);
  pending.get('data/scores_2026.parquet').reject(new Error('offline')); await failed;
  assert.equal(h.elements.get('selection-actions').hidden,false);
  assert.equal(h.elements.get('year').value,2025); assert.equal(h.elements.get('download-view').disabled,false);
  assert.match(h.elements.get('notice-message').textContent,/offline/);
  assert.equal(h.elements.get('notice-message').hidden,false);
  assert.equal(h.elements.get('selection-summary').hidden,true);
  const retry = h.api.loadYear(2026);
  pending.get('data/scores_2026.parquet').resolve({ok:true,arrayBuffer:async()=>records}); await retry;
  assert.equal(h.elements.get('notice-message').hidden,true);
  assert.equal(h.elements.get('selection-summary').hidden,false);
  h.api.update({query:'Journal 01'});
  assert.match(h.elements.get('result-count').innerHTML,/^<strong>1<\/strong> of <strong>40<\/strong>/);
});
await check('Selection CSV follows ANS columns; NF is exported only when added', async () => {
  const h = harness(); await h.api.loadYear(2025);
  const csv = h.api.csv(), lines = csv.parts.join('').split('\r\n');
  assert.equal(lines.length,41);
  assert.equal(lines[0], '"Journal title","OpenAlex field","OpenAlex field (%)","Publications used","Citations used","Pubs. w/out refs.","ANS","Field percentile (ANP)"');
  const matrix = h.api.selectionExport().matrix;
  const row = matrix.find(row => row[0] === 'Journal 01');
  assert.equal(row[6],1);
  assert.doesNotMatch(lines[0],/openalex_id|publisher|issn|exclusion|snapshot|treatment/);
  add(h,'score:share');
  const withNf = h.api.selectionExport().matrix;
  assert.equal(withNf[0].at(-1),'Network Factor (NF)');
  assert.equal(withNf.find(row => row[0] === 'Journal 01').at(-1),0.01);
  h.api.update({query:'Journal 01',treatment:'raw'});
  assert.equal(h.api.selectionExport().matrix.length,2);
  assert.equal(h.api.selectionExport().matrix[1][6],1001);
});
function add(h,key) {
  h.elements.get('add-columns-choices').events.change({target:{value:key,checked:true}});
}

function hide(h, key) {
  h.elements.get('table-head').events.click({target:{closest:selector=>selector === '[data-hide-column]' ? {dataset:{hideColumn:key}}:null}});
}
await check('Hiding columns removes them from downloads while preserving sorting, presets, ranking and journal details', async () => {
  const h = harness(); await h.api.loadYear(2025);
  h.api.applyPreset('vu-sbe');
  h.api.update({sortKey:'poolPct',sortDirection:-1});
  const csv = h.api.csv().parts.join(''), state = JSON.stringify(h.api.getState());
  const ids = h.api.getVisible().map(row=>row.openalex_id).join(','), calculations = h.rankCalls();
  for (const key of ['score:per_article','fieldPct','poolPct','publications_without_references_pct']) hide(h,key);
  assert.equal(JSON.stringify(h.api.getState()),state);
  assert.equal(h.elements.get('preset').value,'vu-sbe');
  assert.equal(h.api.getVisible().map(row=>row.openalex_id).join(','),ids);
  assert.equal(h.rankCalls(),calculations);
  assert.notEqual(h.api.csv().parts.join(''),csv);
  assert.deepEqual(Array.from(h.api.selectionExport().matrix[0]),['Journal title','OpenAlex field','OpenAlex field (%)','Publications used','Citations used']);
  assert.equal(h.elements.get('restore-columns').hidden,false);
  assert.doesNotMatch(h.elements.get('table-head').innerHTML,/class="percentile-group"|data-sort="poolPct"|data-sort="score:per_article"/);
  const popup = D.metricsTable([[years[1],records[0]]],2025,h.api.getState(),{share:{short:'NF',digits:5},per_article:{short:'ANS',digits:3}});
  assert.match(popup,/class="percentile-heading">Percentiles<\/th>/);
  assert.doesNotMatch(popup,/>Field · ANP<\/th>/); assert.match(popup,/>Total · ANP<\/th>/);
  h.elements.get('restore-columns').emit('click');
  assert.equal(h.api.getDisplayColumns().length,7);
  assert.equal(h.elements.get('restore-columns').hidden,true);
  assert.equal(h.api.csv().parts.join(''),csv);
  assert.match(h.elements.get('table-head').innerHTML,/class="percentile-group">Percentiles<\/th>/);
});
await check('Grouped headers track visible columns; title cannot be hidden; reset restores all', async () => {
  const h = harness(); await h.api.loadYear(2025);
  const keys = h.api.getColumns().map(col=>col.key).filter(key=>key !== 'title');
  for (const key of keys) {
    hide(h,key);
    const head = h.elements.get('table-head').innerHTML;
    const spans = [...head.split('</tr>')[0].matchAll(/colspan="(\d+)"/g)].map(m=>Number(m[1]));
    assert.equal(spans.reduce((a,b)=>a+b,0),h.api.getDisplayColumns().length);
    assert.ok(spans.every(n=>n>0));
  }
  hide(h,'title');
  assert.equal(h.api.getDisplayColumns().length,1);
  assert.equal(h.api.getDisplayColumns()[0].key,'title');
  assert.doesNotMatch(h.elements.get('table-head').innerHTML,/data-hide-column="title"/);
  h.api.applyPreset('vu-sbe'); assert.equal(h.api.getDisplayColumns().length,2);
  await h.api.loadYear(2024); assert.equal(h.api.getDisplayColumns().length,2);
  h.elements.get('reset').emit('click');
  assert.equal(h.api.getDisplayColumns().length,7);
  assert.equal(h.elements.get('preset').value,'ef-ais');
});
await check('All fields and individual choices are mutually consistent without a field search', async () => {
  const h = harness(); await h.api.loadYear(2025);
  assert.equal(h.elements.has('field-search'),false);
  assert.equal(h.elements.has('field-legend'),false);
  assert.equal(h.elements.has('clear-fields'),false);
  assert.equal(h.elements.get('all-fields').checked,true);
  assert.doesNotMatch(h.elements.get('field-filter').innerHTML,/ checked/);
  const select=(value,checked)=>h.elements.get('field-filter').events.change({target:{value,checked}});
  select('Medicine',true);
  assert.equal(h.elements.get('all-fields').checked,false);
  assert.equal(h.api.getVisible().length,20);
  assert.equal(h.elements.get('field-summary').textContent,'Medicine');
  assert.equal(h.elements.get('preset').value,'custom');
  select('Economics',true); assert.equal(h.api.getVisible().length,40);
  h.elements.get('all-fields').emit('change',{checked:true});
  assert.equal(h.api.getState().fields.length,0);
  assert.equal(h.elements.get('all-fields').checked,true);
  assert.doesNotMatch(h.elements.get('field-filter').innerHTML,/ checked/);
  select('Medicine',true); select('Medicine',false);
  assert.equal(h.elements.get('all-fields').checked,true);
  assert.equal(h.api.getVisible().length,40);
  select('Medicine',true); h.api.applyPreset('vu-sbe');
  assert.equal(h.elements.get('all-fields').checked,true);
});
await check('Percentile summary counts matching journals across all pages without changing comparison pools', async () => {
  const h = harness(); await h.api.loadYear(2025);
  const count = () => Number(h.elements.get('pool-summary').innerHTML.match(/<strong>([0-9]+)<\/strong>/)[1]);
  assert.equal(count(),39); // Includes results beyond the first 25-row page.
  const rankCalls = h.rankCalls();
  h.api.update({fields:['Medicine']});
  assert.equal(h.api.getVisible().length,20);
  assert.equal(count(),19); // One matching journal has no ANS percentile.
  h.api.update({oaOnly:true});
  assert.equal(h.api.getVisible().length,10);
  assert.equal(count(),10);
  h.api.update({oaOnly:false,query:'Journal 39'});
  assert.equal(h.api.getVisible().length,1);
  assert.equal(count(),0);
  h.api.update({query:'no matching journal'});
  assert.equal(h.api.getVisible().length,0);
  assert.equal(count(),0);
  assert.equal(h.rankCalls(),rankCalls);
  h.api.update({query:'',fields:[]});
  assert.equal(count(),39);
  h.api.update({topPercent:75});
  selectPercentiles(h,'total');
  h.api.update({fields:['Medicine']});
  const eligible = E.availablePercentiles(h.api.getState(), E.rank(E.prepareRows(records.map(row=>({...row}))),h.api.getState()));
  assert.equal(count(),h.api.getVisible().filter(row=>eligible.has(row.openalex_id)).length);
  assert.ok(count() <= h.api.getVisible().length);
});
await check('Help covers main, historical, field-breakdown and About headers', async () => {
  const h = harness(); await h.api.loadYear(2025);
  const help = readFileSync(new URL('../site/journal-help.html',import.meta.url),'utf8');
  const section = id => help.split(`id="${id}">`)[1]?.split('</section>')[0] ?? '';
  const mainHelp = section('journal-table');
  for (const col of h.api.getColumns().filter(col => col.key !== 'title')) {
    const label = col.key.endsWith('Pct') ? `Percentiles \u00b7 ${col.label}` : col.label;
    assert.ok(mainHelp.replaceAll('&middot;', '\u00b7').includes(`<dt>${label}`),col.label);
  }
  for (const header of ['Citing year','Publications used','Citations used','Publications w/out references','Norwegian Reg. level','NF','ANS']) assert.ok(section('yearly-metrics').includes(`<dt>${header}`),header);
  for (const header of ['OpenAlex primary fields','Norwegian Register field']) assert.ok(section('fields-covered').includes(`<dt>${header}`),header);
  for (const header of ['Score year','Status','Data run','OpenAlex snapshot','Norwegian Register snapshot']) assert.ok(section('data-versions').includes(`<dt>${header}`),header);
  assert.match(h.elements.get('table-head').innerHTML,/journal-help.html#journal-table/);
  assert.match(html,/>Download selection<\/button>/);
  assert.match(html,/>Hide journals without percentiles<\/label>/);
  assert.match(html,/>AIS-oriented \(select\)<\/option>/);
  assert.match(html,/>Include publications without references \(noisy\)<\/label>/);
});
await check('Percentile choices propagate to columns, CSV, filtering and summary counts', async () => {
  const h = harness(); await h.api.loadYear(2025);
  h.api.update({topPercent:75,poolOnly:true});
  assert.equal(h.api.getVisible().length,39);
  assert.match(h.elements.get('pool-summary').innerHTML,/<strong>39<\/strong>/);
  assert.equal(h.elements.get('retain-control').hidden,true);
  assert.equal(h.elements.get('top-percent').disabled,true);
  selectPercentiles(h,'total');
  assert.equal(h.elements.get('retain-control').hidden,false);
  assert.equal(h.elements.get('top-percent').disabled,false);
  assert.equal(h.elements.get('retain-label').textContent,'Retain top % per field');
  assert.equal(h.elements.get('top-percent').value,75);
  assert.equal(h.api.getVisible().length,30);
  assert.match(h.elements.get('pool-summary').innerHTML,/<strong>30<\/strong>/);
  assert.doesNotMatch(h.api.csv().parts[0].split('\r\n')[0],/Field percentile/);
  assert.match(h.api.csv().parts[0].split('\r\n')[0],/Total percentile/);
  selectPercentiles(h,'both');
  assert.equal(h.elements.get('retain-control').hidden,false);
  assert.equal(h.elements.get('retain-label').textContent,'For total: retain top % per field');
  assert.equal(h.api.getVisible().length,30);
  assert.equal(h.elements.get('percentiles-none').checked,false);
  selectPercentiles(h,'none');
  assert.equal(h.api.getVisible().length,40);
  assert.equal(h.elements.get('percentiles-none').checked,true);
  assert.equal(h.elements.get('percentiles-field').checked,false);
  assert.equal(h.elements.get('percentiles-total').checked,false);
  assert.equal(h.elements.get('percentile-settings').disabled,true);
  assert.equal(h.elements.get('retain-control').hidden,true);
  assert.equal(h.elements.get('top-percent').disabled,true);
  assert.equal(h.api.getColumns().some(c=>c.key.endsWith('Pct')),false);
  assert.doesNotMatch(h.api.csv().parts[0].split('\r\n')[0],/Field percentile|Total percentile/);
});
await check('Custom opens settings; named presets close them and restore only their selected percentile type', async () => {
  const h = harness(); await h.api.loadYear(2025);
  for (const [preset, expected] of [['full','fieldPct'],['ef-ais','fieldPct'],['vu-sbe','poolPct']]) {
    h.api.applyPreset('custom');
    assert.equal(h.elements.get('settings').hidden,false);
    assert.equal(h.elements.get('settings-help').hidden,false);
    h.api.applyPreset(preset);
    assert.equal(h.elements.get('settings').hidden,true);
    assert.equal(h.elements.get('settings-help').hidden,true);
    assert.equal(h.api.getState().universe,preset === 'full' ? 'oa' : 'n');
    assert.deepEqual(Array.from(h.api.getColumns().filter(c=>c.key.endsWith('Pct')),c=>c.key),[expected]);
    hide(h,expected); hide(h,'score:share');
    assert.equal(h.elements.get('restore-columns').hidden,false);
    h.elements.get('restore-columns').emit('click');
    assert.deepEqual(Array.from(h.api.getDisplayColumns().filter(c=>c.key.endsWith('Pct')),c=>c.key),[expected]);
    assert.equal(h.elements.get('restore-columns').hidden,true);
    assert.equal(h.elements.get('settings').hidden,true);
    assert.equal(h.elements.get('settings-help').hidden,true);
  }
});
await check('Without-reference values are inverted in cells and CSV with the same strict eligibility boundary', async () => {
  const h = harness(); await h.api.loadYear(2025);
  assert.match(h.elements.get('table-body').innerHTML,/coverage-value">10\.0%/);
  assert.match(h.elements.get('table-body').innerHTML,/style="width:10%"/);
  const lines = h.api.csv().parts[0].split('\r\n');
  const position = lines[0].split(',').indexOf('"Pubs. w/out refs."');
  assert.ok(position >= 0);
  assert.equal(lines[1].split(',')[position],'10');
  assert.doesNotMatch(lines[0],/publications_without_references_strictly_below_pct/);
  assert.match(html,/<option value="20">Below 80%<\/option>/);
});
await check('Help close falls back to Journal Metrics and anchors account for the sticky bar height', () => {
  const js = readFileSync(new URL('../site/help.js',import.meta.url),'utf8');
  let action, delayed, observed, closed = false, destination, offset;
  const nav = {getBoundingClientRect:()=>({height:92})};
  const context = {document:{getElementById:id=>id === 'help-sections' ? nav : {addEventListener:(type,fn)=>{action=fn;}},
    documentElement:{style:{setProperty:(key,value)=>{offset=value;}}}},
    ResizeObserver:class {constructor(fn){this.fn=fn;} observe(value){observed=value;}},
    window:{close(){closed=true;},closed:false,location:{assign(value){destination=value;}}},setTimeout(fn){delayed=fn;}};
  context.OpindxSections = {trackSections: () => {}};
  vm.runInNewContext(js,context);
  assert.equal(observed,nav); assert.equal(offset,'92px');
  action(); assert.equal(closed,true); delayed(); assert.equal(destination,'index.html');
  destination=undefined; context.window.closed=true; delayed(); assert.equal(destination,undefined);
});
await check('Download button offers CSV and XLSX without changing the selection', async () => {
  const h = harness(); await h.api.loadYear(2025);
  const before = JSON.stringify(h.api.getState());
  h.elements.get('download-view').emit('click');
  assert.equal(h.elements.get('download-options').hidden,false);
  assert.equal(h.elements.get('download-view').attrs['aria-expanded'],'true');
  h.api.csv(); // Install the existing test capture for saveCsv.
  h.elements.get('download-csv').emit('click');
  assert.equal(h.elements.get('download-options').hidden,true);
  assert.equal(JSON.stringify(h.api.getState()),before);
  h.elements.get('download-view').emit('click');
  await h.api.loadYear(2024);
  assert.equal(h.elements.get('download-options').hidden,true);
});
await check('Real XLSX round-trip matches the selection and preserves numbers, missing cells and literal text', async () => {
  const sample = records.map((r,i)=>({...r,title:i === 0 ? '=1+1' : r.title,issns:'0123-4567'}));
  const h = harness(async()=>({ok:true,arrayBuffer:async()=>sample})); await h.api.loadYear(2025); add(h,'score:share');
  for (const mode of ['field','total','both','none']) {
    selectPercentiles(h,mode);
    hide(h,'oa_field'); hide(h,'citations');
    const selection = h.api.selectionExport();
    assert.ok(selection.matrix[0].includes('Network Factor (NF)'));
    assert.ok(selection.matrix[0].includes('ANS'));
    assert.ok(!selection.matrix[0].includes('OpenAlex field (%)'));
    assert.ok(!selection.matrix[0].includes('OpenAlex field'));
    assert.ok(!selection.matrix[0].includes('Citations used'));
    const output = await h.api.xlsx();
    assert.ok(output.filename.endsWith('.xlsx'));
    const book = XLSX.read(output.bytes,{type:'array'}), sheet = book.Sheets[book.SheetNames[0]];
    const matrix = XLSX.utils.sheet_to_json(sheet,{header:1,defval:null});
    assert.equal(matrix.length,41);
    assert.deepEqual(matrix,JSON.parse(JSON.stringify(selection.matrix)));
    const titleCol = selection.matrix[0].indexOf('Journal title');
    const formulaRow = selection.matrix.findIndex(row=>row[titleCol] === '=1+1');
    const titleCell = sheet[XLSX.utils.encode_cell({r:formulaRow,c:titleCol})];
    assert.equal(titleCell.t,'s'); assert.equal(titleCell.v,'=1+1'); assert.equal(titleCell.f,undefined);
    assert.equal(sheet['!autofilter'].ref,sheet['!ref']);
    assert.equal(h.elements.get('download-status').textContent,'');
    assert.equal(h.elements.get('download-view').disabled,false);
  }
});
await check('XLSX failure restores downloads and preserves the captured selection across later changes', async () => {
  let captured;
  const h = harness(undefined,async matrix=>{captured=matrix; throw new Error('test failure');});
  await h.api.loadYear(2025);
  const before=h.api.selectionExport().matrix;
  const pending=h.api.xlsx();
  h.api.update({query:'Journal 01'});
  await pending;
  assert.deepEqual(captured,before);
  assert.match(h.elements.get('download-status').textContent,/choose CSV/);
  assert.equal(h.elements.get('download-view').disabled,false);
  assert.equal(h.api.csv().parts[0].split('\r\n').length,2);
});

const averageYears=[2026,2025,2024,2023,2022].map(year=>({...years[0],year}));
const averageRow=(id,score,extra={})=>({...records[2],openalex_id:id,title:`Journal ${id}`,search:undefined,in_n:true,
  per_article_n_filtered:score,share_n_filtered:score/100,publications_filtered:2025, ...extra});
const averageData={
  2025:[averageRow('A',3,{title:'Current A',oa_field:'Economics'}),averageRow('B',4,{reference_coverage_pct:0}),
    averageRow('C',null,{in_n:false,share_n_filtered:null,oa_field:'Medicine'}),averageRow('D',6)],
  2024:[averageRow('A',1,{title:'Earlier A'}),averageRow('B',2),averageRow('H',3,{publisher:'Earlier publisher',issns:'old-issn'})],
  2023:[averageRow('A',99,{reference_coverage_pct:0}),averageRow('B',1),averageRow('C',2)],
  2022:[averageRow('A',1)],2026:[averageRow('D',8)],
};
const averageSettings={...filteredFixture,universe:'n',minCoverage:10,topPercent:50,poolOnly:true};
const averageFetch=async url=>({ok:true,arrayBuffer:async()=>averageData[Number(url.match(/scores_(\d+)/)[1])].map(row=>({...row}))});
function averageHarness(fetcher=averageFetch) {
  const h=harness(fetcher,workbookBytes,averageSettings);
  h.api.init({years:averageYears},averageSettings);
  return h;
}
await check('Select preset initially loads all three years and defaults to averaged Total percentiles', async()=>{
  const reads=[];
  const h=averageHarness(url=>{reads.push(Number(url.match(/scores_(\d+)/)[1]));return averageFetch(url);});
  h.api.init({years:averageYears},P.PRESETS['vu-sbe']);
  await h.api.loadYear(2025);
  assert.deepEqual(reads.sort(),[2023,2024,2025]);
  assert.equal(h.api.getState().averagePercentiles,true);
  assert.equal(h.api.getState().topPercent,75);
  assert.equal(h.elements.get('preset').value,'vu-sbe');
  assert.equal(h.elements.get('average-percentiles').checked,true);
  assert.equal(h.elements.get('settings').hidden,true);
  assert.match(h.elements.get('table-head').innerHTML,/ANP3/);
  const output=h.api.selectionExport();
  assert.equal(output.matrix[0].at(-1),'Avg Total percentile (ANP3; 2023-2025)');
  assert.equal(output.matrix.some(row=>row[0]==='Journal B'),false);
  assert.ok(Math.abs(output.matrix.find(row=>row[0]==='Current A').at(-1) - (50+100/3)/2)<1e-10);
  assert.equal(h.api.getVisible().length,2);
});
await check('Choosing Select during the first load awaits the full averaging period', async()=>{
  const pending=new Map();
  const h=averageHarness(url=>new Promise(resolve=>pending.set(url,()=>averageFetch(url).then(resolve))));
  const initial=h.api.loadYear(2025);
  const selected=h.elements.get('preset').emit('change',{value:'vu-sbe'});
  assert.deepEqual([...pending.keys()].sort(),['data/scores_2023.parquet','data/scores_2024.parquet','data/scores_2025.parquet']);
  assert.equal(h.elements.get('download-view').disabled,true);
  assert.equal(h.elements.get('settings').hidden,true);
  pending.get('data/scores_2025.parquet')();
  await initial;
  assert.equal(h.api.getYear(),undefined);
  pending.get('data/scores_2024.parquet')();
  pending.get('data/scores_2023.parquet')();
  await selected;
  assert.equal(h.api.getYear(),2025);
  assert.equal(h.api.getState().averagePercentiles,true);
  assert.equal(h.elements.get('preset').value,'vu-sbe');
  assert.equal(h.api.getVisible().length,2);
  assert.equal(h.elements.get('download-view').disabled,false);
});
await check('Select keeps its name in unavailable years and resumes averaging only while still that preset', async()=>{
  const h=averageHarness(); await h.api.loadYear(2023);
  await h.api.applyPreset('vu-sbe');
  assert.equal(h.api.getState().averagePercentiles,false);
  assert.equal(h.elements.get('average-percentiles').disabled,true);
  assert.equal(h.elements.get('preset').value,'vu-sbe');
  await h.api.loadYear(2024);
  assert.equal(h.api.getState().averagePercentiles,true);
  assert.equal(h.elements.get('preset').value,'vu-sbe');
  assert.match(h.elements.get('table-head').innerHTML,/ANP3: 2022.2024/);
  await h.elements.get('average-percentiles').emit('change',{checked:false});
  assert.equal(h.elements.get('preset').value,'custom');
  await h.api.loadYear(2025);
  assert.equal(h.api.getState().averagePercentiles,false);
  assert.equal(h.elements.get('preset').value,'custom');
  const sparse=averageHarness(); sparse.api.init({years},P.PRESETS['vu-sbe']);
  await sparse.api.loadYear(2025);
  assert.equal(sparse.elements.get('preset').value,'vu-sbe');
  assert.equal(sparse.api.getState().averagePercentiles,false);
  await sparse.api.loadYear(2026);
  assert.equal(sparse.elements.get('preset').value,'vu-sbe');
  assert.equal(sparse.api.getState().averagePercentiles,true);
});
await check('Averaged percentile selection preserves selected-year data, requires current-year eligibility and exports the means', async()=>{
  const h=averageHarness();await h.api.loadYear(2025);
  assert.equal(h.api.getVisible().length,2);
  await h.elements.get('average-percentiles').emit('change',{checked:true});
  assert.equal(h.api.getState().averagePercentiles,true);
  assert.equal(h.elements.get('preset').value,'custom');
  assert.match(h.elements.get('table-head').innerHTML,/class="percentile-group">Percentiles<\/th>/);
  assert.match(h.elements.get('table-head').innerHTML,/ANP3: 2023–2025/);
  assert.match(h.elements.get('pool-summary').innerHTML,/<strong>2<\/strong>/);
  assert.equal(h.api.getVisible().length,2);
  const out=h.api.selectionExport(),header=out.matrix[0];
  const col=label=>header.indexOf(label),byTitle=title=>out.matrix.find(r=>r[0]===title);
  assert.ok(out.filename.includes('avg-percentiles-2023-2025'));
  assert.ok(Math.abs(byTitle('Current A')[col('Avg Field percentile (ANP3; 2023-2025)')] - (50+100/3)/2)<1e-10);
  assert.equal(byTitle('Current A')[col('ANS')],3);
  assert.equal(byTitle('Current A')[col('Publications used')],2025);
  for (const title of ['Journal B','Journal C','Journal H']) assert.equal(byTitle(title),undefined);
  assert.equal(h.api.getRows().some(r=>r.openalex_id==='H'),false);
  h.api.update({poolOnly:false});
  const unhidden=h.api.selectionExport();
  assert.equal(unhidden.matrix.find(r=>r[0]==='Journal B').at(-1),null);
  h.api.update({poolOnly:true});
  assert.doesNotMatch(h.elements.get('table-body').innerHTML,/title="Compared with|Based on .* years/);
  assert.match(h.api.csv().parts[0],/Avg Field percentile/);
  const xlsx=await h.api.xlsx(); const book=XLSX.read(xlsx.bytes,{type:'array'});
  const matrix=XLSX.utils.sheet_to_json(book.Sheets[book.SheetNames[0]],{header:1,defval:null});
  assert.deepEqual(matrix,Array.from(out.matrix,r=>Array.from(r)));
  h.api.update({fields:['Economics']});
  assert.match(h.elements.get('pool-summary').innerHTML,/<strong>2<\/strong>/);
  assert.equal(h.api.getVisible().some(r=>r.openalex_id==='H'),false);
  h.api.update({fields:[],percentileMode:'total'});
  assert.equal(h.api.getVisible().length,1);
  assert.equal(h.api.getVisible().some(r=>r.openalex_id==='A'),false);
  const total=h.api.selectionExport();
  assert.equal(total.matrix.find(r=>r[0]==='Journal D').at(-1),100);
  await h.api.applyPreset('full');
  assert.equal(h.api.getState().averagePercentiles,false);
  assert.equal(h.api.getRows().some(r=>r.openalex_id==='H'),false);
});
await check('A journal missing its current percentile disappears and returns with available years inside the calendar window', async()=>{
  const data={
    2026:[averageRow('R',2),averageRow('X',1)],
    2025:[averageRow('R',99,{reference_coverage_pct:0}),averageRow('X',1)],
    2024:[averageRow('R',1),averageRow('X',2)],
    2023:[averageRow('R',999),averageRow('X',1)],
    2022:[],
  };
  const h=averageHarness(async url=>({ok:true,arrayBuffer:async()=>data[Number(url.match(/scores_(\d+)/)[1])].map(row=>({...row}))}));
  await h.api.loadYear(2025);
  h.api.update({topPercent:100});
  await h.api.update({averagePercentiles:true});
  assert.equal(h.api.getVisible().some(row=>row.openalex_id==='R'),false);
  await h.api.loadYear(2026);
  const output=h.api.selectionExport();
  const returned=output.matrix.find(row=>row[0]==='Journal R');
  assert.equal(returned.at(-1),75); // Mean of 2026 (100) and 2024 (50); omit 2025 and exclude 2023.
  assert.equal(returned[output.matrix[0].indexOf('ANS')],2);
  assert.match(h.elements.get('table-head').innerHTML,/Publications: 2021–2025 · Citations: 2026 · ANP3: 2024–2026 · OpenAlex data:/);
});
await check('Averaging label, earliest-year gate, None and Reset remain consistent', async()=>{
  const h=averageHarness(); await h.api.loadYear(2023);
  assert.equal(h.elements.get('average-percentiles').disabled,true);
  await h.api.update({averagePercentiles:true});
  assert.equal(h.api.getState().averagePercentiles,false);
  await h.api.loadYear(2024);
  assert.equal(h.elements.get('average-percentiles').disabled,false);
  assert.equal(h.elements.get('average-percentiles-label').textContent,'Average percentiles over 2022–2024');
  await h.api.update({averagePercentiles:true});
  await h.api.update({percentileMode:'none'});
  assert.equal(h.api.getState().averagePercentiles,false);
  assert.equal(h.elements.get('average-percentiles').disabled,true);
  h.api.update({percentileMode:'field'});
  await h.api.update({averagePercentiles:true});
  await h.elements.get('reset').emit('click');
  assert.equal(h.api.getState().averagePercentiles,false);
  assert.equal(h.elements.get('average-percentiles').checked,false);
  const sparse=averageHarness();sparse.api.init({years},averageSettings);await sparse.api.loadYear(2025);
  assert.equal(sparse.elements.get('average-percentiles').disabled,true);
});
await check('Loading failure never substitutes a partial averaging window and retry recovers', async()=>{
  let fail=true;
  const h=averageHarness(async url=>{if(fail && url.includes('2023')) throw Error('offline');return averageFetch(url);});
  await h.api.loadYear(2025);
  await h.api.update({averagePercentiles:true});
  assert.equal(h.api.getState().averagePercentiles,false);
  assert.doesNotMatch(h.elements.get('table-head').innerHTML,/ANP3/);
  assert.match(h.elements.get('notice-message').textContent,/offline/);
  fail=false;await h.api.update({averagePercentiles:true});
  assert.equal(h.api.getVisible().length,2);
});
await check('Late average loading cannot overwrite a newer citation year', async()=>{
  let resolve;
  const h=averageHarness(url=>url.includes('2023') ? new Promise(done=>{resolve=()=>averageFetch(url).then(done);}) : averageFetch(url));
  await h.api.loadYear(2025);
  const old=h.api.update({averagePercentiles:true});
  assert.equal(h.elements.get('download-view').disabled,true);
  const latest=h.elements.get('year').emit('change',{value:'2026'});
  await latest;
  assert.equal(h.api.getYear(),2026);
  assert.match(h.elements.get('table-head').innerHTML,/ANP3: 2024–2026/);
  resolve();await old;
  assert.equal(h.api.getYear(),2026);
  assert.match(h.elements.get('table-head').innerHTML,/ANP3: 2024–2026/);
});


await check('Averaged ranks respond to metric, universe and treatment without redownloading years', async()=>{
  let reads=0;
  const h=averageHarness(async()=>{reads++;return {ok:true,arrayBuffer:async()=>[
    averageRow('A',1,{share_n_filtered:2,per_article_n_raw:2,per_article_oa_filtered:2}),
    averageRow('B',2,{share_n_filtered:1,per_article_n_raw:1,per_article_oa_filtered:1}),
  ]};});
  await h.api.loadYear(2025);
  h.api.update({topPercent:100,poolOnly:false});
  await h.api.update({averagePercentiles:true});
  const value=()=>h.api.selectionExport().matrix.find(r=>r[0]==='Journal A').at(-1);
  assert.equal(value(),50);
  h.api.update({metric:'share'});assert.equal(value(),100);
  assert.match(h.elements.get('table-head').innerHTML,/class="percentile-group">Avg percentiles<\/th>/);
  h.api.update({metric:'per_article',treatment:'raw'});assert.equal(value(),100);
  h.api.update({treatment:'filtered',universe:'oa'});assert.equal(value(),100);
  h.api.update({query:'Journal A'});assert.equal(value(),100);
  assert.equal(reads,3);
});
await check('Averaging can be cancelled during loading without applying partial ranks', async()=>{
  let release;
  const h=averageHarness(url=>url.includes('2023')?new Promise(done=>{release=()=>averageFetch(url).then(done);}):averageFetch(url));
  await h.api.loadYear(2025);
  const pending=h.api.update({averagePercentiles:true});
  await h.api.update({averagePercentiles:false});
  assert.equal(h.api.getVisible().length,2);
  release();await pending;
  assert.equal(h.api.getState().averagePercentiles,false);
  assert.doesNotMatch(h.elements.get('table-head').innerHTML,/ANP3/);
  assert.equal(h.api.getVisible().length,2);
  assert.ok(html.indexOf('id="pool-only"')<html.indexOf('id="average-percentiles"'));
  assert.ok(html.indexOf('id="average-percentiles"')<html.indexOf('class="advanced-general"'));
  assert.match(html,/Search by \(partial\) journal title, publisher, ISSN, or OpenAlex ID/);
  assert.match(html,/available as DOI-versioned CSV, XLSX, and Parquet Zenodo/);
});


const addColumn = (h, key, checked = true) => h.elements.get('add-columns-choices').events.change({target:{value:key,checked}});
await check('Optional metadata columns keep their order, selected-year values, safe links and CSV/XLSX output', async () => {
  const sample=[{...records[0],publications_raw:326,citations_raw:1000,publications_filtered:1,citations_n_filtered:27,
    issns:'1234-5678; 8765-4321; 1234-5678',issn_l:'1234-5678',
    norwegian_register_url:'https://kanalregister.hkdir.no/tidsskrift?id=123; https://kanalregister.hkdir.no/en/tidsskrift?id=456; https://evil.example/tidsskrift?id=999',norwegian_level:'1|2'},
    {...records[1],in_n:false,publications_raw:0,citations_raw:0,norwegian_level:'0',
      norwegian_register_url:'https://kanalregister.hkdir.no/tidsskrift?id=789',issns:'1111-1111 <script>'}];
  const h=harness(async()=>({ok:true,arrayBuffer:async()=>sample}),workbookBytes,P.PRESETS.full);await h.api.loadYear(2025);
  const original=h.api.selectionExport().matrix[0],calls=h.rankCalls();
  const keys=['active_years','publications_raw','citations_raw','openalex_id','norwegian_register_ids','norwegian_level','issns'];
  const labels=['Publication years','Publications recorded','Citations recorded','OpenAlex ID','Norwegian Register ID','Norwegian Register level','ISSNs'];
  for(const key of [...keys].reverse()) addColumn(h,key);
  assert.deepEqual(Array.from(h.api.selectionExport().matrix[0]),[...original,...labels]);
  assert.equal(h.rankCalls(),calls);
  assert.equal(h.elements.get('preset').value,'full');
  assert.equal(h.elements.get('add-columns-summary').textContent,'7 selected');
  assert.match(h.elements.get('table-head').innerHTML,/colspan="7" class="extra-group group-start">Additional columns/);
  const exported=h.api.selectionExport(),row=exported.matrix.find(r=>r[0]==='Journal 00');
  const value=label=>row[exported.matrix[0].indexOf(label)];
  assert.equal(value('Publication years'),5);assert.equal(value('OpenAlex ID'),'S0');
  assert.equal(value('Publications used'),1);assert.equal(value('Publications recorded'),326);
  assert.equal(value('Citations used'),0);assert.equal(value('Citations recorded'),1000);
  assert.equal(value('Norwegian Register ID'),'123, 456');assert.equal(value('Norwegian Register level'),'1, 2');
  assert.equal(value('ISSNs'),'1234-5678 8765-4321');
  const outside=exported.matrix.find(r=>r[0]==='Journal 01');
  assert.equal(outside[exported.matrix[0].indexOf('Publications recorded')],0);
  assert.equal(outside[exported.matrix[0].indexOf('Norwegian Register ID')],null);
  assert.equal(outside[exported.matrix[0].indexOf('Norwegian Register level')],null);
  const body=h.elements.get('table-body').innerHTML;
  assert.match(body,/href="https:\/\/openalex.org\/S0" target="_blank" rel="noopener noreferrer">S0/);
  assert.match(body,/href="https:\/\/kanalregister.hkdir.no\/en\/tidsskrift\?id=123"/);
  assert.doesNotMatch(body,/evil.example|<script>/);assert.match(body,/&lt;script&gt;/);
  const xlsx=await h.api.xlsx(),book=XLSX.read(xlsx.bytes,{type:'array'});
  assert.deepEqual(XLSX.utils.sheet_to_json(book.Sheets[book.SheetNames[0]],{header:1,defval:null}),Array.from(exported.matrix,r=>Array.from(r)));
  assert.ok(h.api.csv().parts[0].split('\r\n')[0].endsWith(labels.map(label=>JSON.stringify(label)).join(',')));
  const help=readFileSync(new URL('../site/journal-help.html',import.meta.url),'utf8');
  for(const label of labels)assert.ok(help.replace(/<dt[^>]*>/g,'<dt>').includes(`<dt>${label}</dt>`),label);
  assert.deepEqual(Array.from(h.api.getDisplayColumns().filter(c=>c.extra),c=>c.label),['Publ. years','Publ. recorded','Citations recorded','OpenAlex ID','Norw. Reg. ID','Norw. Reg. level','ISSNs']);
  for(const label of labels)assert.ok(html.replace(/<\/?span[^>]*>/g,'').includes(`>${label}</label>`),label);
  assert.doesNotMatch(help,/Opens the checkbox popup next to What is what/);
});
await check('Added columns persist across years and presets; hide/restore obeys selections and Reset clears them', async () => {
  const h=harness();await h.api.loadYear(2025);
  addColumn(h,'openalex_id');addColumn(h,'issns');
  hide(h,'issns');
  assert.equal(h.api.selectionExport().matrix[0].includes('ISSNs'),false);
  assert.equal(h.elements.get('add-issns').checked,false);
  assert.equal(h.elements.get('add-columns-summary').textContent,'1 selected');
  assert.match(h.elements.get('table-head').innerHTML,/>Add columns \(1\)<\/button>/);
  await h.api.applyPreset('ef-ais');await h.api.loadYear(2024);
  assert.equal(h.api.getColumns().some(c=>c.key==='openalex_id'),true);
  addColumn(h,'openalex_id',false);
  h.elements.get('restore-columns').emit('click');
  const labels=h.api.selectionExport().matrix[0];
  assert.equal(labels.includes('ISSNs'),true);assert.equal(labels.includes('OpenAlex ID'),false);
  assert.equal(h.elements.get('add-issns').checked,true);
  assert.equal(h.elements.get('add-columns-summary').textContent,'1 selected');
  assert.equal(labels.some(v=>v.includes('Total percentile')),false);
  const calls=h.rankCalls();
  h.elements.get('table-head').events.click({target:{closest(selector){return selector==='[data-sort]'?{dataset:{sort:'issns'}}:null;}}});
  assert.equal(h.api.getState().sortKey,'issns');assert.equal(h.rankCalls(),calls);
  h.elements.get('reset').emit('click');
  assert.equal(h.elements.get('add-columns-summary').textContent,'None');
  assert.equal(h.api.getColumns().some(c=>c.extra),false);
  assert.equal(h.elements.get('add-issns').checked,false);
});


await check('The table column popup applies choices immediately and closes with Done or the cross', async () => {
  const h=harness();await h.api.loadYear(2025);
  const open=()=>h.elements.get('table-head').events.click({target:{closest(selector){return selector==='[data-add-columns]'?{}:null;}}});
  open();assert.equal(h.elements.get('columns-dialog').open,true);
  addColumn(h,'publications_raw');
  assert.equal(h.elements.get('columns-dialog').open,true);
  assert.equal(h.api.selectionExport().matrix[0].includes('Publications recorded'),true);
  assert.match(h.elements.get('table-head').innerHTML,/aria-haspopup="dialog" aria-controls="columns-dialog">Add columns \(1\)/);
  h.elements.get('columns-done').emit('click');assert.equal(h.elements.get('columns-dialog').open,false);
  open();h.elements.get('columns-close').emit('click');assert.equal(h.elements.get('columns-dialog').open,false);
  assert.match(html,/<dialog id="columns-dialog" class="columns-dialog" aria-labelledby="columns-title">/);
  assert.equal(html.slice(html.indexOf('<section class="settings"'),html.indexOf('<div class="notice"')).includes('add-columns'),false);
  assert.match(html,/journal-help.html#added-columns/);
});


await check('Column choices during average loading wait for the complete period and remain selected', async () => {
  let release;
  const h=averageHarness(url=>url.includes('2023')?new Promise(done=>{release=()=>averageFetch(url).then(done);}):averageFetch(url));
  await h.api.loadYear(2025);
  const pending=h.api.update({averagePercentiles:true});
  addColumn(h,'publications_raw');
  assert.equal(h.elements.get('add-publications_raw').checked,true);
  release();await pending;
  assert.equal(h.api.selectionExport().matrix[0].includes('Publications recorded'),true);
  const help=readFileSync(new URL('../site/journal-help.html',import.meta.url),'utf8');
  const advanced=help.split('<h2 id="advanced-settings">')[1].split('</section>')[0];
  const table=help.split('<h2 id="journal-table">')[1].split('</section>')[0];
  assert.equal(advanced.includes('<dt id="added-columns">'),false);
  for(const label of ['Publication years','Publications recorded','Citations recorded','OpenAlex ID','Norwegian Register ID','Norwegian Register level','ISSNs'])assert.ok(table.replace(/<dt[^>]*>/g,'<dt>').includes(`<dt>${label}</dt>`),label);
});

console.log(`${passed} main-page tests passed`);

const ojfRecords = [2,4,10,0,null].map((value,i)=>({
  openalex_id:`S${i}`,title:`OJF journal ${i}`,publisher:'Open publisher',issns:'1234-5678',
  score_year:2025,metric_window_years:2,in_n:i<2,in_oa:true,oa_field:'Medicine',
  ojf_raw:i===4 ? 1:value,ojf_filtered:value,ojf_status_filtered:i===4?'ref_denominator_collapse':'defined',
  publications_raw:10,publications_filtered:i===4?0:8,citations_raw:i===4?10:value*10,
  citations_filtered:value==null?0:value*8,reference_coverage_pct:i===4?0:80,active_years:null,
}));
function ojfHarness(fetcher) {
  const h=harness(fetcher ?? (async url=>({ok:true,arrayBuffer:async()=>url.includes('/ojf/')?ojfRecords:records})));
  h.api.init({years,ojf:{annual_path:'ojf',history_path:'ojf/history',rank_path:'ojf/ranks'}},{});
  h.elements.get('year').value='2025';
  return h;
}
await check('OJF selector uses its own files, preset, two-year counts and field percentiles',async()=>{
  const h=ojfHarness(); await h.api.loadYear(2025); await h.api.switchMetric('ojf');
  assert.equal(h.api.getState().metric,'ojf');
  assert.equal(h.elements.get('universe-label').textContent,'Show journals from');
  assert.equal(h.elements.get('preset').value,'jif');
  assert.equal(h.elements.get('publication-years-control').hidden,true);
  assert.equal(h.elements.get('settings').hidden,true);
  assert.equal(h.elements.get('metric-details').attrs.href,'journal-help.html#ojf');
  assert.equal(h.elements.get('metric-independence-text').textContent,'Independently calculated OJF values, not official Journal Impact Factors');
  assert.match(h.elements.get('table-head').innerHTML,/Publications \(OJF2\): 2023\u20132024/);
  assert.match(h.elements.get('table-head').innerHTML,/Citable items used/);
  assert.doesNotMatch(h.elements.get('table-head').innerHTML,/data-sort="score:share"|data-sort="score:per_article"/);
  const result=h.api.selectionExport();
  assert.equal(result.matrix.length,3);
  assert.equal(result.matrix.find(row=>row[0]==='OJF journal 0')[6],2);
  assert.ok(result.filename.includes('ojf2-2025'));
});
await check('OJF universe changes percentiles and membership, never scores, counts or settings visibility',async()=>{
  const h=ojfHarness(); await h.api.switchMetric('ojf');
  const before=h.api.selectionExport().matrix.find(row=>row[0]==='OJF journal 0');
  assert.equal(before.at(-1),50);
  assert.equal(h.api.selectionExport().matrix.find(row=>row[0]==='OJF journal 1').at(-1),100);
  h.api.update({universe:'oa'});
  assert.equal(h.elements.get('settings').hidden,true);
  const after=h.api.selectionExport().matrix.find(row=>row[0]==='OJF journal 0');
  assert.equal(after[6],before[6]);assert.equal(after[3],before[3]);assert.equal(after[4],before[4]);
  assert.equal(h.api.selectionExport().matrix.find(row=>row[0]==='OJF journal 1').at(-1),75);
  assert.equal(after.at(-1),50); // 0, 2, 4, 10: two of four scores are at or below 2.
  h.api.update({query:'OJF journal 2'});
  assert.equal(h.api.selectionExport().matrix[1].at(-1),100);
  assert.equal(h.elements.get('preset').value,'custom');
});
await check('OJF Full coverage preserves zero and undefined scores, with no automatic raw fallback',async()=>{
  const h=ojfHarness(); await h.api.switchMetric('ojf'); h.api.applyPreset('ojf-full');
  const table=h.api.selectionExport().matrix;
  assert.equal(table.length,6);
  assert.equal(table.find(row=>row[0]==='OJF journal 3')[6],0);
  assert.equal(table.find(row=>row[0]==='OJF journal 4')[6],null);
  assert.equal(table.find(row=>row[0]==='OJF journal 4')[3],0);
  h.api.update({treatment:'raw'});
  assert.equal(h.api.selectionExport().matrix.find(row=>row[0]==='OJF journal 4')[6],1);
});
await check('Returning to ANS and Reset load the correct metric dataset and keep NF optional',async()=>{
  const h=ojfHarness(); await h.api.loadYear(2025); add(h,'score:share');
  await h.api.switchMetric('ojf');
  assert.equal(h.api.getColumns().some(col=>col.key==='score:share'),false);
  assert.equal(h.elements.get('add-nf-choice').hidden,true);
  await h.api.switchMetric('per_article');
  assert.equal(h.api.getColumns().some(col=>col.key==='score:share'),true);
  assert.equal(h.elements.get('publication-years-control').hidden,false);
  await h.api.switchMetric('ojf'); await h.elements.get('reset').emit('click');
  assert.equal(h.api.getState().metric,'per_article');
  assert.equal(h.api.getRows()[0].title,'Journal 00');
  assert.equal(h.api.getColumns().some(col=>col.key==='score:share'),false);
});
await check('OJF metric load failure restores the last committed ANS view',async()=>{
  const h=ojfHarness(async url=>{if(url.includes('/ojf/'))throw Error('offline');return {ok:true,arrayBuffer:async()=>records};});
  await h.api.loadYear(2025);await h.api.switchMetric('ojf');
  assert.equal(h.api.getState().metric,'per_article');
  assert.match(h.elements.get('table-head').innerHTML,/class="percentile-group">Percentiles<\/th>/);
  assert.match(h.elements.get('notice-message').textContent,/could not be loaded/);
});
await check('OJF has a text Details help link, independent labels and accessible metric wording',()=>{
  assert.doesNotMatch(html,/id="citing-sources"|id="metric-maturity"/);
  assert.match(html,/id="metric-details" href="journal-help.html#ans" target="_blank" rel="noopener noreferrer">Details<\/a>/);
  assert.match(html,/Article Network Score \(ANS\)/);
  assert.match(html,/Open Journal Factor · 2 years \(OJF2\)/);
  const help=readFileSync(new URL('../site/journal-help.html',import.meta.url),'utf8');
  assert.match(help,/id="ojf-citing-sources"/);
  assert.doesNotMatch(help,/Close to EF|Close to JIF|Ranking indicator<\/dt>/);
});

await check('OJF tables use one decimal and CSV/XLSX three decimals while percentiles retain full precision',async()=>{
  const precise = ojfRecords.map((row,i)=>({...row,ojf_filtered:[1.2134,1.2446,10,0.04444,null][i]}));
  const h=ojfHarness(async url=>({ok:true,arrayBuffer:async()=>url.includes('/ojf/')?precise:records}));
  await h.api.switchMetric('ojf');
  const annualScores=h.api.getRows().filter(row=>row.in_n).map(row=>E.score(row,h.api.getState()));
  assert.deepEqual(Array.from(annualScores),[1.2134,1.2446]);
  let result=h.api.selectionExport();
  const scoreCol=result.matrix[0].indexOf('OJF2');
  const fieldCol=result.matrix[0].indexOf('Field percentile (OJF2)');
  const first=result.matrix.find(row=>row[0]==='OJF journal 0');
  const second=result.matrix.find(row=>row[0]==='OJF journal 1');
  assert.equal(first[scoreCol],1.213); assert.equal(second[scoreCol],1.245);
  assert.equal(first[fieldCol],50); assert.equal(second[fieldCol],100);
  const mainTable=h.elements.get('table-body').innerHTML;
  assert.equal((mainTable.match(/title="1\.2">1\.2<\/td>/g)||[]).length,2);
  assert.doesNotMatch(mainTable,/1\.21|1\.24/);

  h.api.applyPreset('ojf-full');
  result=h.api.selectionExport();
  assert.equal(result.matrix.find(row=>row[0]==='OJF journal 3')[scoreCol],0.044);
  assert.equal(result.matrix.find(row=>row[0]==='OJF journal 4')[scoreCol],null);
  assert.match(h.elements.get('table-body').innerHTML,/title="0\.0">0\.0<\/td>/);
  const csv=h.api.csv().parts.join('');
  assert.match(csv,/,1\.213,/); assert.match(csv,/,1\.245,/);
  assert.match(csv,/,10\.000,/); assert.match(csv,/,0\.044,/);
  assert.doesNotMatch(csv,/1\.2134|1\.2446|0\.04444/);

  const file=await h.api.xlsx();
  const book=XLSX.read(file.bytes,{type:'array',cellNF:true});
  const sheet=book.Sheets['Journal selection'];
  const exported=XLSX.utils.sheet_to_json(sheet,{header:1,defval:null});
  const rowIndex=exported.findIndex(row=>row[0]==='OJF journal 0');
  const cell=sheet[XLSX.utils.encode_cell({r:rowIndex,c:scoreCol})];
  assert.equal(cell.v,1.213); assert.equal(cell.z,'0.000');

  const tiny=h.api.getRows().find(row=>row.openalex_id==='S3');
  const details=D.metricsTable([[years[1],tiny]],2025,{...h.api.getState(),percentileMode:'none',showPercentiles:false},{ojf:{short:'OJF',digits:1}});
  assert.match(details,/<td>0\.0<\/td>/); assert.doesNotMatch(details,/4\.00e|0\.040/);
});
console.log('7 OJF integration tests passed');

const fiveValues=[8.2468,3.1111,1.5,0,2.2222];
const dualTwo=ojfRecords.map((row,i)=>({...row,ojf2_raw:row.ojf_raw,ojf2_filtered:row.ojf_filtered,
  ojf5_raw:fiveValues[i]+1,ojf5_filtered:fiveValues[i]}));
const dualFive=dualTwo.map((row,i)=>({...row,metric_window_years:5,oa_field:'Economics',
  publications_raw:100,publications_filtered:80,citations_raw:900,citations_filtered:660,
  reference_coverage_pct:80,ojf_raw:row.ojf5_raw,ojf_filtered:row.ojf5_filtered,ojf_status_filtered:'defined'}));
function dualHarness(fetcher) {
  const fetchRows=async url=>({ok:true,arrayBuffer:async()=>{
    const data=url.includes('/ojf5/') ? dualFive : url.includes('/ojf/') ? dualTwo : records;
    return data.map(row=>({...row,score_year:Number(url.match(/scores_(\d+)/)?.[1] ?? 2025)}));
  }});
  const h=harness(fetcher ?? fetchRows);
  h.api.init({years:averageYears,ojf:{annual_path:'ojf',history_path:'ojf/history',rank_path:'ojf/ranks'},
    ojf5:{annual_path:'ojf5',history_path:'ojf5/history',rank_path:'ojf5/ranks'}},{});
  h.elements.get('year').value='2025';
  return h;
}
await check('OJF5 selection aligns its five-year counts, field, sort, presets and percentiles',async()=>{
  assert.equal(E.score({ojf_filtered:2.,metric_window_years:5},{metric:'ojf5',treatment:'filtered'}),2.);
  assert.equal(E.score({ojf_filtered:2.,metric_window_years:2},{metric:'ojf5',treatment:'filtered'}),null);
  const h=dualHarness();await h.api.switchMetric('ojf5');
  assert.equal(h.api.getState().metric,'ojf5');assert.equal(h.api.getState().sortKey,'score:ojf5');
  assert.equal(h.elements.get('preset').value,'jif5');
  assert.equal(h.elements.get('settings').hidden,true);
  assert.equal(h.elements.get('publication-years-control').hidden,true);
  assert.equal(h.elements.get('metric-details').attrs.href,'journal-help.html#ojf5');
  assert.match(h.elements.get('table-head').innerHTML,/Publications \(OJF5\): 2020–2024/);
  assert.match(h.elements.get('table-head').innerHTML,/Field · OJF5/);
  const out=h.api.selectionExport(),row=out.matrix.find(r=>r[0]==='OJF journal 0');
  assert.ok(out.filename.includes('ojf5-2025'));assert.equal(row[1],'Economics');
  assert.equal(row[out.matrix[0].indexOf('Citable items used')],80);
  assert.equal(row[out.matrix[0].indexOf('Citations used')],660);
  assert.equal(row[out.matrix[0].indexOf('OJF5')],8.247);
  assert.equal(row.at(-1),100);
  assert.equal(out.matrix[0].includes('OJF2'),false);
  assert.equal(h.elements.get('add-ojf2-choice').hidden,false);
  assert.equal(h.elements.get('add-ojf5-choice').hidden,true);
  assert.equal(h.elements.get('add-nf-choice').hidden,true);
  await h.api.switchMetric('ojf');
  const two=h.api.selectionExport();assert.equal(two.matrix.find(r=>r[0]==='OJF journal 0').at(-1),50);
  assert.match(h.elements.get('table-head').innerHTML,/Publications \(OJF2\): 2023–2024/);
});
await check('Optional other OJF is formatted and exported without changing the selected window or percentiles',async()=>{
  const h=dualHarness();await h.api.switchMetric('ojf5');h.api.applyPreset('ojf5-full');
  const before=h.api.selectionExport().matrix.find(r=>r[0]==='OJF journal 0').at(-1);
  add(h,'score:ojf');
  assert.equal(h.elements.get('add-columns-summary').textContent,'1 selected');
  let out=h.api.selectionExport(),head=out.matrix[0];
  assert.ok(head.includes('OJF2'));assert.ok(head.includes('OJF5'));
  assert.equal(out.matrix.find(r=>r[0]==='OJF journal 0')[head.indexOf('Field percentile (OJF5)')],before);
  assert.equal(out.matrix.find(r=>r[0]==='OJF journal 4')[head.indexOf('OJF2')],null);
  assert.equal(out.matrix.find(r=>r[0]==='OJF journal 4')[head.indexOf('OJF5')],2.222);
  assert.match(h.api.csv().parts.join(''),/,8\.247,/);
  const file=await h.api.xlsx(),book=XLSX.read(file.bytes,{type:'array',cellNF:true});
  const sheet=book.Sheets['Journal selection'],matrix=XLSX.utils.sheet_to_json(sheet,{header:1,defval:null});
  const row=matrix.findIndex(r=>r[0]==='OJF journal 0');
  for(const label of ['OJF2','OJF5'])assert.equal(sheet[XLSX.utils.encode_cell({r:row,c:head.indexOf(label)})].z,'0.000');
  hide(h,'score:ojf');assert.equal(h.elements.get('add-columns-summary').textContent,'None');
  assert.equal(h.api.selectionExport().matrix[0].includes('OJF2'),false);
  h.elements.get('restore-columns').emit('click');
  assert.equal(h.api.selectionExport().matrix[0].includes('OJF2'),true);
  assert.equal(h.api.selectionExport().matrix[0].includes('Network Factor (NF)'),false);
});
await check('OJF window switches preserve Full or Custom settings and average only selected-metric percentiles',async()=>{
  const h=dualHarness();await h.api.switchMetric('ojf');h.api.applyPreset('ojf-full');
  await h.api.switchMetric('ojf5');assert.equal(h.elements.get('preset').value,'ojf5-full');
  assert.equal(h.api.getState().universe,'oa');assert.equal(h.api.getState().poolOnly,false);
  h.api.update({minCoverage:20,treatment:'raw'});
  await h.api.switchMetric('ojf');
  assert.equal(h.api.getState().minCoverage,20);assert.equal(h.api.getState().treatment,'raw');
  assert.equal(h.elements.get('settings').hidden,true);
  await h.api.switchMetric('ojf5');
  await h.api.update({averagePercentiles:true});
  const out=h.api.selectionExport();
  assert.ok(out.matrix[0].includes('Avg Field percentile (OJF5; 2023-2025)'));
  assert.equal(out.matrix.find(r=>r[0]==='OJF journal 0')[out.matrix[0].indexOf('Citable items used')],100);
  assert.match(h.elements.get('table-head').innerHTML,/Percentiles avg: 2023–2025/);
  assert.match(h.elements.get('table-head').innerHTML,/Field · OJF5/);
});
await check('OJF5 load failures restore OJF2; unavailable OJF5 cannot replace the current view',async()=>{
  const h=dualHarness(async url=>{if(url.includes('/ojf5/'))throw Error('offline');return {ok:true,arrayBuffer:async()=>dualTwo};});
  await h.api.switchMetric('ojf');await h.api.switchMetric('ojf5');
  assert.equal(h.api.getState().metric,'ojf');assert.match(h.elements.get('table-head').innerHTML,/OJF2/);
  assert.match(h.elements.get('notice-message').textContent,/could not be loaded/);
  const absent=ojfHarness();await absent.api.switchMetric('ojf');await absent.api.switchMetric('ojf5');
  assert.equal(absent.api.getState().metric,'ojf');
  assert.equal(absent.elements.get('metric').querySelector('option[value="ojf5"]').disabled,true);
});
console.log('4 OJF2/OJF5 integration tests passed');
