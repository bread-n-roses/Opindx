import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import * as D from '../site/journal-details.js';
import * as E from '../site/engine.js';

let passed = 0;
async function check(name, test) { await test(); passed++; console.log(`PASS ${name}`); }
const metrics = {share:{short:'NF',digits:5},per_article:{short:'ANS',digits:3}};
const settings = {universe:'n',treatment:'filtered',metric:'per_article',minCoverage:20,minYears:4,topPercent:50,showPercentiles:true};
const entry = year => ({year,status:'live',openalex_snapshot:'2026-09-23'});
const journal = (id, score) => ({openalex_id:id, title:'Example', issns:'1111-1111; 2222-2222',publisher:'Publisher',
  oa_field:'Psychology',in_n:true,in_oa:true,active_years:5,reference_coverage_pct:90,norwegian_level:'1',
  norwegian_field:'Business and Finance | Economics',norwegian_register_url:'https://kanalregister.hkdir.no/tidsskrift?id=10; https://kanalregister.hkdir.no/tidsskrift?id=20',
  publications_raw:100,publications_filtered:80,citations_raw:7,citations_filtered:3,citations_n_filtered:0,citations_oa_filtered:2,per_article_n_filtered:score,share_n_filtered:score/10});
const meta = journal('S20',20);
const detail = {oa_field_classified_works:3,oa_field_1:'A',oa_field_1_works:1,oa_field_2:'B',oa_field_2_works:1,
  oa_field_3:'C',oa_field_3_works:1,oa_field_other_works:0,norwegian_primary_id:'20',
  norwegian_entries_json:JSON.stringify([{id:'10',field:'Economics',title:'Previous title'}, {id:'20',field:'Business and Finance',title:'Example'}])};

await check('Metadata order, comma-separated ISSNs, and persistent selected-year register row', () => {
  const rows = D.metadataRows(meta);
  assert.deepEqual(rows.map(r=>r[0]),['ISSN / Publisher','OpenAlex details','Norwegian Register details','Open access journal','Publications recorded','Citations recorded','Publication years','Share of publications w/out references']);
  assert.equal(rows[0][1],'1111-1111, 2222-2222 / Publisher');
  assert.match(rows[1][1],/>Journal page<\/a> for ID: S20/);
  const absent = D.metadataRows({...meta,in_n:false});
  assert.deepEqual(absent.map(r=>r[0]), rows.map(r=>r[0]));
  assert.equal(absent[2][1], '<span id="register-details">Not in Norwegian Register</span>');
});
await check('A unique primary link preserves plain primary ID and linked alternative IDs', () => {
  const html = D.registerLinks(meta,detail);
  assert.match(html,/en\/tidsskrift\?id=20/);
  assert.match(html,/>Journal page<\/a> for ID: 20/);
  assert.match(html,/>10<\/a>/);
  const equal = D.registerLinks(meta,{...detail,norwegian_primary_id:''});
  assert.match(equal,/Register entries:/); assert.doesNotMatch(equal,/Journal page/);
  assert.equal(D.registerLinks({...meta,in_n:false},detail),'Not in Norwegian Register');
  assert.equal(D.registerLinks({...meta,norwegian_register_url:''},detail),'Not in Norwegian Register');
});
await check('Field shares total in the header; each Norwegian assignment keeps its correct ID', () => {
  const html = D.fieldsTable(meta,detail,2026);
  assert.match(html,/2021–2025 publications/); assert.match(html,/>100%<\/th>/);
  assert.doesNotMatch(html,/<tfoot>|>Total</);
  assert.match(html,/Norwegian Register field \(2026\)<\/th>/);
  assert.match(html,/Economics <span class="register-field-id">\(ID: 10\)/);
  assert.match(html,/Business and Finance <span class="register-field-id">\(ID: 20\)/);
  assert.doesNotMatch(html,/Business and Finance \| Economics/);
  assert.match(html,/Assigned field/);
  const single = D.fieldsTable({...meta,norwegian_field:'Economics'},detail,2025);
  assert.match(single,/Norwegian Register field \(2025\): Economics/);
  assert.doesNotMatch(D.fieldsTable({...meta,in_n:false},detail,2026),/Norwegian Register/);
});
await check('Unclassified publications and data errors do not display 100 percent', () => {
  const unknown = {...detail,oa_field_classified_works:0,oa_field_1_works:0,oa_field_2_works:0,oa_field_3_works:0};
  assert.match(D.fieldsTable(meta,unknown,2026),/No classified publications/);
  assert.doesNotMatch(D.fieldsTable(meta,unknown,2026),/>100%/);
  assert.match(D.fieldsTable(meta,null,2026),/Field breakdown unavailable/);
});
await check('Grouped percentile header follows the selected indicator and disappears when disabled', () => {
  const history = [[entry(2026),meta]];
  const html = D.metricsTable(history,2026,settings,metrics);
  assert.match(html,/scope="colgroup" colspan="2"[^>]*>Pctiles &middot; ANS/);
  assert.match(html,/<th scope="col">Field<\/th><th scope="col">Total<\/th>/);
  assert.match(html,/rowspan="2"/); assert.match(html,/OA · NR/); assert.match(html,/Citations/);
  assert.doesNotMatch(html,/Ref\. coverage/);
  assert.match(D.metricsTable(history,2026,{...settings,metric:'share'},metrics),/Pctiles &middot; NF/);
  const off = D.metricsTable(history,2026,{...settings,showPercentiles:false},metrics);
  assert.doesNotMatch(off,/Pctiles|rowspan="2"|Loading field/);
});
await check('Each historical year uses its own ranking population, including field-only percentiles', () => {
  const first = [journal('S10',10),meta,journal('S30',30)], second = first.slice(0,2);
  const ranks = new Map([[2025,{ranks:E.rank(first,settings)}],[2026,{ranks:E.rank(second,settings)}]]);
  const html = D.metricsTable([[entry(2025),meta],[entry(2026),meta]],2026,settings,metrics,ranks);
  assert.match(html,/<td>66\.7<\/td><td>50\.0<\/td>/);
  assert.match(html,/<td>100\.0<\/td><td>100\.0<\/td>/);
  const dropped = D.metricsTable([[entry(2026),second[0]]],2026,settings,metrics,ranks);
  assert.match(dropped,/<td>50\.0<\/td><td title="Outside the retained top share of its field">—<\/td>/);
  assert.match(html,/<td>80<\/td><td>0<\/td>/);
  assert.match(D.metricsTable([[entry(2024),null]],2026,settings,metrics),/colspan="8"/);
});
await check('Journal and register labels are escaped', () => {
  assert.match(D.metadataRows({...meta,publisher:'<img src=x>'})[0][1],/&lt;img/);
  const html = D.fieldsTable({...meta,norwegian_field:'<script>'},detail,2026);
  assert.doesNotMatch(html,/<script>/); assert.match(html,/&lt;script&gt;/);
});

// Exercise the actual asynchronous coordinator with plain test elements (not browser QA).
const app = readFileSync(new URL('../site/app.js',import.meta.url),'utf8');
await check('Popup links reveal headings below the padded sticky bar, whose close button dismisses the dialog', () => {
  const source = app.slice(app.indexOf('function showJournal(id) {'),app.indexOf('// One small history file'));
  const elements = Object.fromEntries(['dialog-content','journal-dialog','journal-sections','details-title','fields-title','metrics-title'].map(id=>[id,{id}]));
  const dialog = elements['journal-dialog'];
  Object.assign(dialog,{scrollTop:150,clientTop:1,scrollCalls:[],closed:false,showModal(){},close(){this.closed=true;},
    getBoundingClientRect:()=>({top:40}),scrollTo(options){this.scrolled=options;this.scrollTop=options.top;this.scrollCalls.push(options);}});
  let navHeight=54, stickyInset=30, targetContentTop=600;
  elements['journal-sections'].getBoundingClientRect=()=>({height:navHeight,bottom:40+1+stickyInset+navHeight});
  for (const id of ['details-title','fields-title','metrics-title']) {
    elements[id].getBoundingClientRect=()=>({top:40+1+targetContentTop-dialog.scrollTop});
    elements[id].focus=options=>{elements[id].focused=options;};
  }
  const context={rows:[meta],D,year:2026,state:settings,$:id=>elements[id],escape:value=>value,universeName:()=> 'Norwegian Register',showHistory(){},stopJournalTracking(){},OpindxSections:{trackSections(){return () => {}; }}};
  vm.createContext(context);vm.runInContext(source,context);
  context.showJournal(meta.openalex_id);
  assert.equal(dialog.scrollTop,0);
  const html=elements['dialog-content'].innerHTML;
  assert.ok(html.indexOf('id="dialog-title"') < html.indexOf('<nav'));
  assert.ok(html.indexOf('</nav>') < html.indexOf('<dl'));
  assert.match(html,/data-journal-section="metrics-title">Metrics by year<\/a>/);
  assert.match(html,/<h3 id="details-title" tabindex="-1">Details<\/h3>/);
  assert.match(html.split('</nav>')[0],/data-journal-close aria-label="Close journal details"/);
  for (const id of ['details-title','fields-title','metrics-title']) {
    assert.ok(html.includes(`href="#${id}" data-journal-section="${id}"`));
    assert.ok(html.includes(`id="${id}" tabindex="-1"`));
    let prevented=false;
    const event={target:{closest:selector=>selector === '[data-journal-section]' ? {dataset:{journalSection:id}}:null},preventDefault(){prevented=true;}};
    dialog.scrollTop=150;
    dialog.scrollCalls=[];
    context.navigateJournalSection(event);
    assert.equal(prevented,true);assert.equal(elements[id].focused.preventScroll,true);
    assert.equal(dialog.scrolled.top,targetContentTop-stickyInset-navHeight-12);
    assert.equal(dialog.scrollCalls.length,2); // Corrects the padding-related overlap after the first scroll.
    assert.equal(elements[id].getBoundingClientRect().top-elements['journal-sections'].getBoundingClientRect().bottom,12);
    assert.equal(dialog.scrolled.behavior,'auto');
  }
  const detailsEvent={target:{closest:selector=>selector === '[data-journal-section]' ? {dataset:{journalSection:'details-title'}}:null},preventDefault(){}};
  navHeight=88;stickyInset=24;
  context.navigateJournalSection(detailsEvent);
  assert.equal(dialog.scrolled.top,targetContentTop-stickyInset-navHeight-12);
  targetContentTop=20;dialog.scrollTop=0;
  context.navigateJournalSection(detailsEvent);
  assert.equal(dialog.scrolled.top,0);
  let intercepted=false;
  context.navigateJournalSection({target:{closest:()=>null},preventDefault(){intercepted=true;}});
  assert.equal(intercepted,false);
  context.navigateJournalSection({target:{closest:selector=>selector === '[data-journal-close]' ? {}:null}});
  assert.equal(dialog.closed,true);
});
const coordinator = app.slice(app.indexOf('const historicalRankFiles ='),app.indexOf('// ---- Downloads ----'));
function harness(percentiles) {
  const elements = Object.fromEntries(['history','field-breakdown','register-details','history-percentile-status'].map(id=>[id,{innerHTML:'',textContent:''}]));
  let reads = 0;
  const context = {E,D,METRICS:metrics,state:{...settings,showPercentiles:percentiles},year:2026,rows:[meta],historyRequest:0,
    index:{journal_details:{rank_path:'ranks-test',rank_layout:'metric-treatment'}},$:id=>elements[id],
    journalHistory:async()=>[[entry(2026),{...meta,...detail}],[entry(2025),{...meta,...detail}]],
    fetchOk:async(url)=>{assert.match(url,/2025-n-per_article-filtered\.parquet$/);reads++;return {arrayBuffer:async()=>new ArrayBuffer(0)};},parquetReadObjects:async()=>[meta]};
  vm.createContext(context); vm.runInContext(coordinator,context);
  return {context,elements,reads:()=>reads};
}
await check('Percentiles disabled make no ranking downloads; enabled results are reused', async () => {
  const off = harness(false); await off.context.showHistory(meta.openalex_id,meta); assert.equal(off.reads(),0);
  const on = harness(true); await on.context.showHistory(meta.openalex_id,meta); assert.equal(on.reads(),1);
  assert.match(on.elements.history.innerHTML,/100\.0/);
  await on.context.showHistory(meta.openalex_id,meta); assert.equal(on.reads(),1);
  on.context.state.topPercent=0;
  await on.context.showHistory(meta.openalex_id,meta);
  assert.match(on.elements.history.innerHTML,/Outside the retained top share/);
});
await check('A late journal response cannot overwrite a newer popup', async () => {
  const h = harness(false); let release;
  h.context.journalHistory=()=>new Promise(resolve=>{release=resolve;});
  const pending = h.context.showHistory(meta.openalex_id,meta);
  h.context.historyRequest++;
  h.elements.history.innerHTML='New journal';
  release([[entry(2026),{...meta,...detail}]]); await pending;
  assert.equal(h.elements.history.innerHTML,'New journal');
});
await check('Failed percentile downloads preserve metrics and can be retried', async () => {
  const h = harness(true), success=h.context.fetchOk;
  h.context.fetchOk=async()=>{throw Error('offline');};
  await h.context.showHistory(meta.openalex_id,meta);
  assert.match(h.elements.history.innerHTML,/Citations/);
  assert.match(h.elements['history-percentile-status'].textContent,/could not be loaded/);
  h.context.fetchOk=success; await h.context.showHistory(meta.openalex_id,meta);
  assert.equal(h.elements['history-percentile-status'].textContent,'');
});
await check('Every history row has only the selected percentile columns, including loading, error and missing membership', () => {
  for (const mode of ['field','total','both','none']) {
    const state = {...settings,percentileMode:mode,showPercentiles:mode !== 'none'};
    const expected = mode === 'both' ? 2 : mode === 'none' ? 0 : 1;
    for (const [row,ranks] of [[meta,new Map()], [meta,new Map([[2026,{error:true}]])],
      [{...meta,in_n:false},new Map()], [meta,new Map([[2026,{ranks:E.rank([meta],state)}]])]]) {
      const table = D.metricsTable([[entry(2026),row]],2026,state,metrics,ranks);
      const body = table.split('<tbody>')[1];
      assert.equal([...body.matchAll(/<td\b/g)].length,6+expected,mode);
      assert.equal(table.includes('>Field</th>'),['field','both'].includes(mode));
      assert.equal(table.includes('>Total</th>'),['total','both'].includes(mode));
    }
    const absent = D.metricsTable([[entry(2026),null]],2026,state,metrics);
    assert.ok(absent.includes(`colspan="${6+expected}"`));
  }
  assert.equal(D.metadataRows(meta).at(-1)[1],'10.00%');
  assert.equal(D.metadataRows({...meta,reference_coverage_pct:null}).at(-1)[1],'Unavailable');
});


await check('Recorded and used counts follow universe and treatment without a broad-count fallback', () => {
  const row={...meta,publications_raw:326,publications_filtered:1,citations_raw:224,citations_filtered:27,
    citations_n_filtered:21,citations_n_raw:181,citations_oa_filtered:25};
  const values=Object.fromEntries(D.metadataRows(row,settings));
  assert.match(values['Publications recorded'],/326.*Used in filtered metrics: 1/);
  assert.match(values['Citations recorded'],/224.*Used in filtered metrics: 21/);
  const raw=Object.fromEntries(D.metadataRows(row,{...settings,treatment:'raw'}));
  assert.match(raw['Citations recorded'],/224.*Used in raw metrics: 181/);
  assert.equal(E.columnValue(row,settings,null,'citations'),21);
  assert.equal(E.columnValue(row,{...settings,universe:'oa'},null,'citations'),25);
  assert.equal(E.usedCount({...row,in_n:false},settings,'publications'),null);
  delete row.citations_n_filtered;
  assert.equal(E.usedCount(row,settings,'citations'),null);
});

console.log(`${passed} journal-details tests passed`);
