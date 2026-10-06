"""Portable, explicitly configured replay of the approved September website run."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

import duckdb
import pandas as pd

PACKAGE = Path(__file__).resolve().parents[2]
CORE = Path(__file__).with_name('core')
sys.path.insert(0, str(CORE))
import extract
import source_checkpoint
import targeted_work_metadata
import source_repairs_jgr
import run_source_repair_preflight
import rebuild_source_repair_graph
from identity import AnnualIdentityMaps, SourceKeyOverride, project_endpoints, project_retention, _project_one, _valid_issn_l
from sources import project_sources
from select_canonical_source import ReviewedRepresentative, select_canonical_sources
from norwegian import build_annual_rosters
from match_norwegian import (ReviewedMatchDecision, match_memberships, source_aliases_from_provenance,
    graph_aliases_from_annual_pairs, group_memberships, validate_url_cells)
from score import score_annual
from assemble import assemble_annual
from handoff import validate_handoff_frame
from build_rolling_fields import rolling, METHOD, EXTRA_COLUMNS
from build_field_breakdown import aggregate, COUNTS, NAMES
from build_journal_details_preview import register_details
import import_run
from .datasets import digest
from .metric_counts import from_effective_graph

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() and read(path)==value:
        return
    temp=path.with_suffix(path.suffix+'.partial')
    temp.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    temp.replace(path)

def copy_if_changed(source,target):
    """Preserve provenance-bound timestamps when resuming identical inputs."""
    source,target=Path(source),Path(target)
    if target.exists() and digest(source)==digest(target):
        return
    shutil.copyfile(source,target)

def profile():
    return read(PACKAGE/'configs/september-2026.json')

def decisions(year):
    p=PACKAGE/f'corrections/annual-{year}.json'
    if digest(p)!=profile()['approval_sha256'][str(year)]:
        raise ValueError('Packaged annual decisions changed; a reviewed new software version is required')
    b=read(p);m=b['identity_map']
    rules={r['source_id']:SourceKeyOverride(tuple(r['expected_raw_issn_ls']),r['target_issn_l']) for r in m['source_id_override']}
    maps=AnnualIdentityMaps(year,rules,m['alias_to_canonical'],m['key_correction'],m['review_status'])
    maps.validate(year)
    matches=[ReviewedMatchDecision(r['score_year'],r['journal_id'],tuple(r['canonical_issn_ls']),r['review_status'],r['evidence'],tuple(r['evidence_by_key'])) for r in b['reviewed_match_decisions']]
    reps=[ReviewedRepresentative(**{**r,'expected_component_source_ids':tuple(r['expected_component_source_ids'])}) for r in b['canonical_source_representatives']]
    for r in reps:r.validate(year)
    return maps,matches,reps

def prepare(snapshot,register,run):
    """Inputs are local public-source files. No downloading or publishing occurs."""
    snapshot,register,run=Path(snapshot).resolve(),Path(register).resolve(),Path(run).resolve()
    p=profile()
    if run==snapshot or snapshot.is_relative_to(run) or run.is_relative_to(snapshot):
        raise ValueError('Keep the run directory separate from the raw snapshot')
    if digest(register)!=p['norwegian_sha256']:
        raise ValueError('This release reproduces the recorded 2026-07-26 Norwegian list; input hash differs')
    entities={}
    for entity in ['works','sources']:
        path=snapshot/entity/'manifest.json'
        if digest(path)!=p['manifests'][entity]:
            raise ValueError('Snapshot manifest differs from the reviewed September profile: '+entity)
        m=read(path)
        entities[entity]={'manifest_sha256':digest(path),'files':len(m['files']),'bytes':m['content_length']}
    config={'snapshot_date':p['snapshot_date'],'raw_snapshot_root':snapshot.as_posix(),'score_years':p['years'],
        'year_convention':{'score_year_is_citing_year':True,'cited_window_start_offset':-5,'cited_window_end_offset':-1},
        'planned_work_extract':{'publication_year_start':2017,'publication_year_end':2026,'retain_reference_arrays_for_years':p['years']},
        'paths_relative_to_run_root':{'intermediates':'intermediates'},
        'norwegian_register_input':'inputs/norwegian_register_journals_2026-07-26.csv',
        'norwegian_register_sha256':p['norwegian_sha256']}
    config_path=run/'config/run.json'
    if config_path.exists() and read(config_path)!=config:raise ValueError('Existing run configuration differs')
    run.mkdir(parents=True,exist_ok=True)
    for directory in ['config','inputs','snapshot-manifests','code','checks']: (run/directory).mkdir(exist_ok=True)
    for entity in entities:copy_if_changed(snapshot/entity/'manifest.json',run/f'snapshot-manifests/{entity}-manifest.json')
    copy_if_changed(register,run/config['norwegian_register_input'])
    for path in CORE.glob('*.py'):copy_if_changed(path,run/'code'/path.name)
    save(config_path,config)
    save(run/'run-record.json',{'snapshot':{'snapshot_date':p['snapshot_date'],'entities':entities},'purpose':'public replication'})
    # Existing readers validate the complete manifest inventory before calculation.
    extract._snapshot(run,config);source_checkpoint._binding(run)
    save(run/'checks/input-audit.json',{'status':'PASS','data_sources':['OpenAlex','Norwegian Register'],
        'norwegian_sha256':digest(register),'manifest_sha256':p['manifests'],
        'corrections':'Packaged author-curated identity decisions; see corrections provenance.',
        'raw_payload_verification':'Manifest hashes and file sizes/mtimes; no full raw-file content hashes supplied by this profile.'})
    return run

def verified(path):
    path=Path(path);receipt=read(path.with_suffix('.receipt.json'))
    if receipt['sha256']!=digest(path) or receipt.get('bytes',path.stat().st_size)!=path.stat().st_size:
        raise ValueError('Checkpoint does not match its content receipt: '+str(path))
    return path

def fields(run,memory='8GB',threads=4):
    """Re-extract exact primary fields from the downloaded snapshot; no research cache."""
    config=read(run/'config/run.json')
    out=run/'intermediates/fields';out.mkdir(parents=True,exist_ok=True)
    graph=verified(run/'intermediates/source-repair-v1/graph/works.parquet')
    dedup=verified(run/'intermediates/extraction/dedup_choice.parquet')
    raw=out/'raw.parquet';corrected=out/'corrected.parquet';counts=out/'counts.parquet'
    binding={'graph':digest(graph),'dedup':digest(dedup),'works_manifest':digest(run/'snapshot-manifests/works-manifest.json'),
             'pipeline':digest(Path(__file__))}
    report_path=out/'report.json'
    if report_path.exists():
        report=read(report_path)
        if report['binding']!=binding or report['sha256']!=digest(counts):raise ValueError('Field checkpoint changed')
        return counts
    q=extract._sql
    with duckdb.connect() as con:
        con.execute(f'SET memory_limit={q(memory)}');con.execute(f'SET threads={int(threads)}')
        con.execute(f'SET temp_directory={q(out/"temp")}');con.execute('SET preserve_insertion_order=false')
        con.execute(f"""COPY (SELECT id AS work_id, filename AS source_file, file_row_number AS source_row,
            primary_topic.field.display_name AS primary_field
            FROM read_parquet({q(Path(config['raw_snapshot_root'])/'works/updated_date=*/*.parquet')},filename=true,file_row_number=true,hive_partitioning=false)
            WHERE publication_year BETWEEN 2017 AND 2025 AND type IN ('article','review')
            AND primary_location.source.issn_l IS NOT NULL AND is_paratext IS NOT TRUE)
            TO {q(raw)} (FORMAT PARQUET, COMPRESSION ZSTD)""")
        con.execute(f"""COPY (SELECT g.work_id,g.source_id,g.raw_issn_l,g.year,
            coalesce(nullif(r.primary_field,''),'Unknown') AS oa_field
            FROM read_parquet({q(raw)}) r JOIN read_parquet({q(dedup)}) d
            ON r.work_id=d.work_id AND replace(r.source_file,chr(92),'/')=d.source_file AND r.source_row=d.source_row
            JOIN read_parquet({q(graph)}) g ON r.work_id=g.work_id WHERE g.is_ar AND g.year BETWEEN 2017 AND 2025)
            TO {q(corrected)} (FORMAT PARQUET, COMPRESSION ZSTD)""")
        actual=con.execute(f'SELECT count(*),count(DISTINCT work_id) FROM read_parquet({q(corrected)})').fetchone()
        expected=con.execute(f'SELECT count(*) FROM read_parquet({q(graph)}) WHERE is_ar AND year BETWEEN 2017 AND 2025').fetchone()[0]
        if actual!=(expected,expected):raise ValueError('Exact Work-version field join is incomplete or duplicated')
        con.execute(f"COPY (SELECT source_id,raw_issn_l,year,oa_field,count(*)::BIGINT AS works_n FROM read_parquet({q(corrected)}) GROUP BY 1,2,3,4) TO {q(counts)} (FORMAT PARQUET,COMPRESSION ZSTD)")
    save(report_path,{'status':'PASS','binding':binding,'sha256':digest(counts),'classified_and_unclassified_works':expected})
    return counts

def annual(run,year,field_counts,output,*,reference=None):
    maps,match_decisions,reps=decisions(year)
    graph=run/'intermediates/source-repair-v1/graph'
    inputs={name:verified(path) for name,path in {
        'counts':graph/f'{year}/counts_raw_identity.parquet','edges':graph/f'{year}/edges_raw_identity.parquet',
        'pairs':graph/f'{year}/annual_pairs.parquet','retention':graph/'raw_articles_by_publication_year.parquet',
        'sources':run/'intermediates/source-repair-v1/latest_sources.parquet'}.items()}
    register=run/'inputs/norwegian_register_journals_2026-07-26.csv'
    if digest(register)!=profile()['norwegian_sha256']:raise ValueError('Register changed')
    latest=source_checkpoint._read_cache(inputs['sources'])
    effective,provenance,source_report=project_sources(year,latest,maps)
    effective,source_report,representative_audit=select_canonical_sources(year,effective,provenance,source_report,maps,reps)
    counts,edges,projection=project_endpoints(year,pd.read_parquet(inputs['counts']),pd.read_parquet(inputs['edges']),maps)
    retention_raw=pd.read_parquet(inputs['retention'],filters=[('year','>=',year-5),('year','<=',year-1)])
    retention,retention_report=project_retention(year,retention_raw,maps)
    expected=counts.set_index('issn_l')[['a_raw','a_filtered']]
    observed=retention.groupby('issn_l')[['a_raw','a_filtered']].sum()
    pd.testing.assert_frame_equal(observed.reindex(expected.index,fill_value=0),expected,check_dtype=False)
    members=build_annual_rosters(register,years=(year,))
    aliases=source_aliases_from_provenance(year,provenance)
    graph_aliases=graph_aliases_from_annual_pairs(year,pd.read_parquet(inputs['pairs']),provenance,maps)
    match=match_memberships(members,aliases,match_decisions,graph_aliases)
    active=set(counts.loc[counts.a_raw.gt(0),'issn_l'])|set(edges.loc[edges.n_raw.gt(0),'citing_issn_l'])|set(edges.loc[edges.n_raw.gt(0),'cited_issn_l'])
    if any(r.reason=='ambiguous_exact_issn_match' and active.intersection(r.candidate_keys) for r in match.review_queue):
        raise ValueError('Unresolved active Norwegian membership ambiguity')
    groups=group_memberships(match.resolved)
    scores,solver=score_annual(year,counts,edges,effective,{g.issn_l for g in groups})
    frame=assemble_annual(year,sources=effective,counts=counts,edges=edges,retention=retention,scores=scores,
        norwegian=groups,oa_snapshot_version='2026-09-23',norwegian_register_snapshot='2026-07-26')
    validate_handoff_frame(frame,annual_n_members=pd.DataFrame({'score_year':[year]*len(groups),'issn_l':[g.issn_l for g in groups]}),
        expected_years=(year,),expected_oa_snapshot='2026-09-23',expected_norwegian_snapshot='2026-07-26',native_full_scores=scores)
    validate_url_cells(match.resolved,frame)
    output.mkdir(parents=True,exist_ok=True)
    csv=output/f'pipeline_{year}.csv';frame.to_csv(csv,index=False,encoding='utf-8-sig',float_format='%.17g')
    result,_,_=import_run.read_input(csv)
    hierarchy=effective[['oa_field','oa_domain']].drop_duplicates()
    # The same hierarchy construction used by the original all-years exporter.
    if hierarchy.oa_field.duplicated().any():raise ValueError('Inconsistent OpenAlex field/domain hierarchy')
    domains=hierarchy.set_index('oa_field').oa_domain.to_dict()
    with duckdb.connect() as con:
        mapping=con.execute('SELECT DISTINCT source_id,raw_issn_l FROM read_parquet(?) WHERE year BETWEEN ? AND ?',
            [str(field_counts),year-5,year-1]).df()
        mapping['issn_l']=[_project_one(s,k,maps) for s,k in mapping.itertuples(index=False,name=None)]
        if not mapping.issn_l.map(_valid_issn_l).all():raise ValueError('Invalid field identity')
        modal=rolling(con,field_counts,mapping,year)
        detail=aggregate(con,field_counts,mapping,year)
    result=result.drop(columns=['oa_field','oa_domain']).merge(modal.drop(columns='total'),on='issn_l',how='left',validate='one_to_one')
    result['oa_field']=result.oa_field.fillna('Unknown')
    result['oa_domain']=result.oa_field.map(domains).fillna('Unknown')
    for col in ['oa_field_modal_works','oa_field_classified_works','oa_field_tied_modes']:result[col]=result[col].fillna(0).astype('int64')
    result=result.merge(detail.drop(columns='oa_field_classified_works'),on='issn_l',how='left',validate='one_to_one',suffixes=('','_check'))
    if result.publications_raw_check.fillna(0).tolist()!=result.publications_raw.tolist():raise ValueError('Field counts differ from annual publication counts')
    result=result.drop(columns='publications_raw_check')
    for c in COUNTS+['oa_field_other_works']:result[c]=result[c].fillna(0).astype('int64')
    result[NAMES]=result[NAMES].fillna('')
    roster={(m.score_year,m.journal_id):m for m in members}
    details=[register_details(row,roster) for row in result.fillna({'norwegian_register_url':'','norwegian_field':''}).itertuples(index=False)]
    result['norwegian_entries_json']=[x[0] for x in details];result['norwegian_primary_id']=[x[1] for x in details]
    result=result[import_run.COLUMNS+EXTRA_COLUMNS+NAMES+COUNTS+['oa_field_other_works','norwegian_entries_json','norwegian_primary_id']]
    result=from_effective_graph(result,edges,scores)
    result=result.sort_values('openalex_id',kind='stable').reset_index(drop=True)
    target=output/f'scores_{year}.parquet';result.to_parquet(target,index=False)
    parity=None
    if reference:
        expected=pd.read_parquet(Path(reference)/target.name).sort_values('openalex_id',kind='stable').reset_index(drop=True)
        pd.testing.assert_frame_equal(result[expected.columns],expected,check_dtype=False,check_exact=False,rtol=1e-12,atol=0)
        for c in expected:
            if not pd.api.types.is_float_dtype(result[c]):
                pd.testing.assert_series_equal(result[c],expected[c],check_dtype=False,check_exact=True)
        parity='PASS: all reference columns match; metadata/counts exact, score relative tolerance 1e-12'
    report={'status':'PASS','year':year,'rows':len(result),'output_sha256':digest(target),'reference_parity':parity,
        'inputs':{k:digest(v) for k,v in inputs.items()},'field_counts_sha256':digest(field_counts),
        'corrections_sha256':profile()['approval_sha256'][str(year)],'solver':json.loads(solver.to_json(orient='records'))}
    save(output/f'validation_{year}.json',report)
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot',required=True,type=Path);p.add_argument('--norwegian',required=True,type=Path)
    p.add_argument('--work',required=True,type=Path);p.add_argument('--memory',default='8GB');p.add_argument('--threads',type=int,default=4)
    p.add_argument('--reference',type=Path,help='Optional archived annual Parquets to compare against')
    p.add_argument('--check-inputs',action='store_true')
    args=p.parse_args()
    run=prepare(args.snapshot,args.norwegian,args.work)
    if args.check_inputs:print('PASS: pinned input manifests, inventories, Norwegian list and configuration');return
    started=time.time()
    print('Stage 1/7: OpenAlex Sources',flush=True)
    latest,_=source_checkpoint.checkpoint_sources(run)
    discovery=run_source_repair_preflight.discover_source_cases(latest)
    print('Stage 2/7: OpenAlex Works extraction and deduplication',flush=True)
    extract.extract(run,memory_limit=args.memory,threads=args.threads)
    print('Stage 3/7: source corrections',flush=True)
    targeted=run/'intermediates/source-repairs/targeted_work_metadata.parquet'
    if targeted.exists():verified(targeted)
    else:targeted_work_metadata.extract(run,tuple(discovery['required_target_source_ids']))
    jgr_report=run/'intermediates/source-repairs/jgr_review.json'
    if not jgr_report.exists():source_repairs_jgr.review(run,discovery['jgr_source_id'])
    run_source_repair_preflight.preflight(run)
    print('Stage 4/7: corrected citation graphs',flush=True)
    repair=run/'intermediates/source-repair-v1'
    rebuild_source_repair_graph.rebuild(run,ledger=repair/'work_overrides.parquet',exclusions=repair/'excluded_works.parquet',memory_limit=args.memory,threads=args.threads)
    print('Stage 5/7: publication fields',flush=True)
    field_counts=fields(run,args.memory,args.threads)
    print('Stage 6/7: annual metrics and exports',flush=True)
    out=run/'exports';reports=[]
    for year in profile()['years']:
        reports.append(annual(run,year,field_counts,out,reference=args.reference))
        print(str(year)+': PASS',flush=True)
    manifest={'run':'replication-2026-09','created':'2026-10-02','dummy':False,'schema_version':3,
        'openalex_snapshot':'2026-09-23','norwegian_register_snapshot':'2026-07-26','years':profile()['years'],
        'universes':{'n':'Norwegian Register','oa':'OpenAlex'},'field_assignment':METHOD,
        'assets':{f'scores_{r["year"]}.parquet':{'sha256':r['output_sha256'],'rows':r['rows'],'bytes':(out/f'scores_{r["year"]}.parquet').stat().st_size} for r in reports}}
    save(out/'manifest.json',manifest)
    print('Stage 7/7: complete annual CSV/XLSX/Parquet',flush=True)
    from .datasets import export
    export(out,run/'release-data')
    save(run/'checks/completion.json',{'status':'PASS','seconds':time.time()-started,'annual':reports,'publication_performed':False})
    print('Complete; all outputs remain local.',flush=True)

if __name__=='__main__':main()
