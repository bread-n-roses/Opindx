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
const years = [2026,2025,2024].map(year => ({year,universes:{n:'Norwegian Register',oa:'OpenAlex'},status:year === 2024 ? 'frozen':'live',openalex_snapshot:'2026-09-23'}));
const records = Array.from({length:40}, (_,i) => ({openalex_id:`S${i}`,title:`Journal ${String(i).padStart(2,'0')}`,in_oa:true,in_n:i < 20,
  publisher:i < 20 ? 'Shared Publisher':'Outside Publisher',oa_field:i < 20 ? 'Economics':'Medicine',is_open_access:i % 2 === 0,
  oa_field_modal_share:0.625,reference_coverage_pct:90,active_years:5,share_oa_filtered:i/100,per_article_oa_filtered:i === 39 ? null:i,
  share_n_filtered:i/100,per_article_n_filtered:i,per_article_oa_raw:1000+i,publications_filtered:i,citations_filtered:i,
  issns:'1234-5678'}));

function harness(fetchOverride, workbookWriter = workbookBytes) {
  const elements = new Map();
  function element(id) {
    return {id, hidden:false, disabled:false, checked:false, value:'', innerHTML:'', textContent:'', open:false,
      attrs:{},events:{},children:new Map(),
      addEventListener(type, fn) { this.events[type] = fn; },
      setAttribute(key,value) { this.attrs[key] = value; },
      querySelector(key) { if (!this.children.has(key)) this.children.set(key, element(key)); return this.children.get(key); },
      focus() {}, contains(target) { return target === this; },
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
    init(data) { index = data; }, loadYear, applyPreset, update, fillFilters,
    getState:()=>state, getYear:()=>year, getRows:()=>rows, getVisible:()=>visible,
    getColumns, getDisplayColumns:()=>columns,
    csv() { let output; saveCsv=(filename,parts)=>{output={filename,parts}}; downloadView(); return output; },
    selectionExport,
    async xlsx() { let output; saveBinary=(filename,bytes)=>{output={filename,bytes}}; await downloadXlsx(); return output; },
  })`,context);
  api.init({years,releases_url:'https://example.org/releases'});
  return {api,elements,rankCalls:()=>rankCalls};
}

await check('Calendar defaults and VU bookmark choose an available complete year', () => {
  const now = new Date(2026,9,1);
  assert.deepEqual(P.startingSelection(years,'',now),{year:2025,preset:'full'});
  assert.deepEqual(P.startingSelection([...years].reverse(),'?preset=vu-sbe',now),{year:2025,preset:'vu-sbe'});
  assert.equal(P.startingSelection(years,'?year=2026',now).year,2026);
  assert.equal(P.startingSelection(years,'?year=2099',now).year,2025);
  assert.equal(P.startingSelection([{year:2026}],'',now).year,2026);
  assert.throws(()=>P.startingSelection([]),/no data/);
});
await check('Full shows percentiles by default and preserves journals missing ANS', async () => {
  const h = harness(); await h.api.loadYear(2025);
  assert.equal(h.api.getVisible().length,40);
  assert.equal(h.api.getVisible().at(-1).openalex_id,'S39');
  assert.equal(h.api.getVisible().at(-1).share_oa_filtered,.39);
  assert.equal(h.api.getState().treatment,'filtered');
  assert.equal(h.rankCalls(),1);
  assert.equal(h.api.getState().showPercentiles,true);
  assert.equal(h.elements.get('percentiles-field').checked,true);
  assert.equal(h.elements.get('pool-only').checked,false);
  assert.match(h.elements.get('table-head').innerHTML,/Percentiles · ANS/);
  assert.equal(h.elements.get('preset').value,'full');
  assert.equal(h.elements.get('notice').hidden,false);
  assert.equal(h.elements.get('selection-actions').hidden,false);
  assert.equal(h.elements.get('notice-message').hidden,true);
  assert.equal(h.elements.get('selection-summary').hidden,false);
  assert.equal(h.elements.get('settings').hidden,true);
  assert.equal(h.elements.get('settings-help').hidden,true);
  assert.equal(h.api.getState().minCoverage,20);
  assert.equal(h.api.getState().minYears,4);
  assert.equal(h.api.getState().topPercent,100);
  assert.match(h.elements.get('table-head').innerHTML,/2020–2024 · Cited in 2025 · OpenAlex snapshot 2026-09 · Live metrics/);
  assert.doesNotMatch(h.elements.get('table-body').innerHTML,/journal-id|1234-5678|openalex\.org/);
});
await check('Full percentile requirements exclude boundary coverage and short histories without hiding journals', async () => {
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
  assert.equal(h.elements.get('preset').value,'full');
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
  for (const [key,value] of Object.entries(P.PRESETS['vu-sbe'])) assert.equal(state[key],value,key);
  assert.equal(h.api.getVisible().length,15);
  assert.match(h.elements.get('table-head').innerHTML,/Percentiles · ANS/);
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
  assert.equal(h.elements.get('preset').value,'full');
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
await check('Broaden search switches visible controls to filtered Full and preserves query/year', async () => {
  const h = harness(); await h.api.loadYear(2025);
  h.api.applyPreset('vu-sbe'); h.api.update({query:'Journal 39'});
  assert.equal(h.api.getVisible().length,0);
  assert.match(h.elements.get('table-body').innerHTML,/Search all OpenAlex journals/);
  h.elements.get('table-body').events.click({target:{closest:selector=>selector === '[data-search-all]' ? {}:null}});
  assert.equal(h.elements.get('preset').value,'full');
  assert.equal(h.api.getYear(),2025);
  assert.equal(h.api.getState().query,'Journal 39');
  assert.equal(h.api.getVisible().length,1);
  assert.equal(h.api.getVisible()[0].per_article_oa_filtered,null);
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
await check('Selection CSV matches visible headers and includes NF, ANS and separate field percentages across every page', async () => {
  const h = harness(); await h.api.loadYear(2025);
  const csv = h.api.csv();
  const lines = csv.parts.join('').split('\r\n');
  assert.equal(lines.length,41);
  assert.equal(lines[0], '"Journal title","OpenAlex field","OpenAlex field (%)","Publications","Citations","Pubs. w/out refs.","NF","ANS","Field percentile (ANS)"');
  assert.match(lines.at(-1),/^"Journal 39","Medicine",62.5,39,39,10,0.39,,/);
  const matrix = h.api.selectionExport().matrix;
  const row = matrix.find(row => row[0] === 'Journal 01');
  assert.equal(row[6],0.01); assert.equal(row[7],1);
  assert.doesNotMatch(lines[0],/openalex_id|publisher|issn|exclusion|snapshot|treatment/);
  h.api.update({query:'Journal 01',treatment:'raw'});
  assert.equal(h.api.selectionExport().matrix.length,2);
  assert.equal(h.api.selectionExport().matrix[1][7],1001);
});

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
  assert.deepEqual(Array.from(h.api.selectionExport().matrix[0]),['Journal title','OpenAlex field','OpenAlex field (%)','Publications','Citations','NF']);
  assert.equal(h.elements.get('restore-columns').hidden,false);
  assert.doesNotMatch(h.elements.get('table-head').innerHTML,/Percentiles ·|data-sort="poolPct"|data-sort="score:per_article"/);
  const popup = D.metricsTable([[years[1],records[0]]],2025,h.api.getState(),{share:{short:'NF',digits:5},per_article:{short:'ANS',digits:3}});
  assert.match(popup,/Pctiles &middot; ANS/);
  assert.doesNotMatch(popup,/>Field<\/th>/); assert.match(popup,/>Total<\/th>/);
  h.elements.get('restore-columns').emit('click');
  assert.equal(h.api.getDisplayColumns().length,8);
  assert.equal(h.elements.get('restore-columns').hidden,true);
  assert.equal(h.api.csv().parts.join(''),csv);
  assert.match(h.elements.get('table-head').innerHTML,/Percentiles · ANS/);
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
  assert.equal(h.api.getDisplayColumns().length,8);
  assert.equal(h.elements.get('preset').value,'full');
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
  assert.equal(h.elements.get('preset').value,'full');
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
await check('Summary shows only the retained percentile count and stays independent of display filters', async () => {
  const h = harness(); await h.api.loadYear(2025);
  const summary = h.elements.get('pool-summary').innerHTML;
  h.api.update({fields:['Medicine']});
  assert.match(h.elements.get('result-count').innerHTML,/<strong>20<\/strong>/);
  assert.equal(h.elements.get('pool-summary').innerHTML,summary);
  assert.equal(summary,' · <strong>39</strong> journals with percentile');
  assert.doesNotMatch(summary,/ fields|meet the requirements|whole OpenAlex universe/);
});
await check('Help covers main, historical, field-breakdown and About headers', async () => {
  const h = harness(); await h.api.loadYear(2025);
  const help = readFileSync(new URL('../site/journal-help.html',import.meta.url),'utf8');
  const section = id => help.split(`<h2 id="${id}">`)[1]?.split('</section>')[0] ?? '';
  const mainHelp = section('journal-table');
  for (const col of h.api.getColumns().filter(col => col.key !== 'title')) {
    const label = col.key.endsWith('Pct') ? `Percentiles \u00b7 ${col.label}` : col.label;
    assert.ok(mainHelp.replaceAll('&middot;', '\u00b7').includes(`<dt>${label}`),col.label);
  }
  for (const header of ['Citing year','Journal universe','Level','Publications','Citations','NF','ANS']) assert.ok(section('yearly-metrics').includes(`<dt>${header}`),header);
  for (const header of ['OpenAlex primary fields','Norwegian Register field']) assert.ok(section('fields-covered').includes(`<dt>${header}`),header);
  for (const header of ['Score year','Status','Data run','OpenAlex snapshot','Norwegian Register snapshot']) assert.ok(section('data-versions').includes(`<dt>${header}`),header);
  assert.match(h.elements.get('table-head').innerHTML,/journal-help.html#journal-table/);
  assert.match(html,/>Download selection<\/button>/);
  assert.match(html,/>Hide journals without percentiles<\/label>/);
  assert.match(html,/>VU Amsterdam \(SBE\)<\/option>/);
  assert.match(html,/>Include publications without references \(not recommended\)<\/label>/);
});
await check('Percentile choices propagate to columns, CSV, filtering and summary counts', async () => {
  const h = harness(); await h.api.loadYear(2025);
  h.api.update({topPercent:75,poolOnly:true});
  assert.equal(h.api.getVisible().length,39);
  assert.match(h.elements.get('pool-summary').innerHTML,/<strong>39<\/strong>/);
  selectPercentiles(h,'total');
  assert.equal(h.api.getVisible().length,30);
  assert.match(h.elements.get('pool-summary').innerHTML,/<strong>30<\/strong>/);
  assert.doesNotMatch(h.api.csv().parts[0].split('\r\n')[0],/Field percentile/);
  assert.match(h.api.csv().parts[0].split('\r\n')[0],/Total percentile/);
  selectPercentiles(h,'both');
  assert.equal(h.api.getVisible().length,30);
  assert.equal(h.elements.get('percentiles-none').checked,false);
  selectPercentiles(h,'none');
  assert.equal(h.api.getVisible().length,40);
  assert.equal(h.elements.get('percentiles-none').checked,true);
  assert.equal(h.elements.get('percentiles-field').checked,false);
  assert.equal(h.elements.get('percentiles-total').checked,false);
  assert.equal(h.elements.get('percentile-settings').disabled,true);
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
  const h = harness(async()=>({ok:true,arrayBuffer:async()=>sample})); await h.api.loadYear(2025);
  for (const mode of ['field','total','both','none']) {
    selectPercentiles(h,mode);
    hide(h,'oa_field'); hide(h,'citations');
    const selection = h.api.selectionExport();
    assert.ok(selection.matrix[0].includes('NF'));
    assert.ok(selection.matrix[0].includes('ANS'));
    assert.ok(!selection.matrix[0].includes('OpenAlex field (%)'));
    assert.ok(!selection.matrix[0].includes('OpenAlex field'));
    assert.ok(!selection.matrix[0].includes('Citations'));
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
console.log(`${passed} main-page tests passed`);
