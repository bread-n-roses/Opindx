"""OpenAlex + Norwegian Register -> annual OJF files, without benchmark inputs.

First run opindx-replicate with the pinned open inputs. This command reuses its
audited Source identities and Norwegian matching, then extracts the separate
two-year, all-type Works checkpoint needed for OJF. It does not use the NF/ANS
graph as an OJF citation graph.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import json
from pathlib import Path

import duckdb
import pandas as pd

from .. import pipeline as nf
from . import assembly, checkpoint as io, engine

TYPES = ('article', 'review', 'data-paper', 'software-paper')

def project_pair(source, raw, maps):
    source = '' if pd.isna(source) else str(source).strip()
    if source.startswith('S') and source[1:].isdigit(): source='https://openalex.org/'+source
    raw = '' if pd.isna(raw) else str(raw).strip().upper()
    key = nf._project_one(source, raw, maps)
    return key if nf._valid_issn_l(key) else 'oa:'+source if source else None

def annual_inputs(year, latest_path, pairs_path, register, destination):
    """Recreate Sources and N membership using the public NF/ANS identity rules."""
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    maps, reviewed, representatives=nf.decisions(year)
    latest=nf.source_checkpoint._read_cache(Path(latest_path))
    policy=nf.read(nf.PACKAGE/f'corrections/annual-{year}.json')
    identity=dict(policy['identity_map']);identity.pop('score_year',None)
    display=[{k:v for k,v in r.items() if k not in ['score_year','evidence']}
             for r in policy['canonical_source_representatives']]
    cache_key=io.hash_json({'latest':io.hash_file(latest_path),'identity':identity,'display':display,
        'source_code':io.hash_file(nf.CORE/'sources.py'),'representative_code':io.hash_file(nf.CORE/'select_canonical_source.py')})
    cache=destination/('source-projection-'+cache_key[:16])
    if cache.with_suffix('.json').exists():
        receipt=io.read_json(cache.with_suffix('.json'))
        if receipt['binding']!=cache_key:raise ValueError('Changed Source projection cache')
        for suffix in ['.sources.parquet','.provenance.parquet']:
            if io.hash_file(cache.with_suffix(suffix))!=receipt['sha256'][suffix]:raise ValueError('Changed Source projection cache bytes')
        sources=pd.read_parquet(cache.with_suffix('.sources.parquet'))
        provenance=pd.read_parquet(cache.with_suffix('.provenance.parquet'))
        for frame in [sources,provenance]:
            if 'score_year' in frame:frame['score_year']=year
            for col in ['source_ids','issns']:
                if col in frame:frame[col]=frame[col].map(lambda v:tuple(v) if hasattr(v,'__iter__') and not isinstance(v,str) else v)
    else:
        print(f'OJF {year}: projecting all open Source metadata',flush=True)
        sources,provenance,report=nf.project_sources(year,latest,maps)
        sources,report,_=nf.select_canonical_sources(year,sources,provenance,report,maps,representatives)
        sources.to_parquet(cache.with_suffix('.sources.parquet'),index=False)
        provenance.to_parquet(cache.with_suffix('.provenance.parquet'),index=False)
        io.atomic_json(cache.with_suffix('.json'),{'binding':cache_key,'sha256':{
            suffix:io.hash_file(cache.with_suffix(suffix)) for suffix in ['.sources.parquet','.provenance.parquet']}})
    members=nf.build_annual_rosters(register,years=(year,))
    aliases=nf.source_aliases_from_provenance(year,provenance)
    graph=nf.graph_aliases_from_annual_pairs(year,pd.read_parquet(pairs_path),provenance,maps)
    match=nf.match_memberships(members,aliases,reviewed,graph)
    # Never force ambiguous matches into the fixed citing set.
    # Unmatched register records stay unmatched; only resolved entries enter N.
    # Preserve a visible audit rather than inferring a membership.
    nf.save(destination/f'norwegian-unresolved-{year}.json',{
        'year':year,'review_queue':[asdict(r) for r in match.review_queue],
        'membership_policy':'Only explicitly resolved open-register matches enter N'})
    norwegian=pd.DataFrame([asdict(r.member)|{'issn_l':r.canonical_issn_l,'match_method':r.method}
                            for r in match.resolved])
    nkeys=set(norwegian.issn_l)
    sources=sources.copy();sources['is_n']=sources.issn_l.isin(nkeys)
    sources['effective_issn_l']=sources.issn_l
    sources['is_a_journals']=sources.source_type.eq('journal')
    sources['is_a_books_proceedings']=sources.source_type.isin(['book series','conference'])
    lookup=latest[['source_id','raw_issn_l','source_type','title','publisher','issns','is_open_access','oa_field','oa_domain']].copy()
    lookup['raw_issn_l']=lookup.raw_issn_l.fillna('').str.strip().str.upper()
    lookup['issn_l']=[project_pair(s,k,maps) for s,k in zip(lookup.source_id,lookup.raw_issn_l)]
    canonical=sources.set_index('issn_l')
    for col in ['source_type','oa_field','oa_domain']:
        values=lookup.issn_l.map(canonical[col]);lookup[col]=values.where(values.notna(),lookup[col])
    lookup['is_n']=lookup.issn_l.isin(nkeys)
    fallback=lookup.loc[~lookup.issn_l.map(nf._valid_issn_l)].copy()
    fallback['journal_title']=fallback.title
    fallback['openalex_id']=fallback.source_id.str.replace('https://openalex.org/','',regex=False)
    fallback['openalex_url']=fallback.source_id
    sources=pd.concat([sources,fallback],ignore_index=True)
    overrides=[]
    for source,rule in sorted(maps.source_id_override.items()):
        for raw in rule.expected_raw_issn_ls:
            overrides.append({'source_id':source,'expected_raw_issn_l':raw,'target_issn_l':project_pair(source,raw,maps)})
    keys=sorted(set(maps.alias_to_canonical)|set(maps.key_correction))
    aliases=[{'from_issn_l':k,'to_issn_l':maps.key_correction.get(maps.alias_to_canonical.get(k,k),maps.alias_to_canonical.get(k,k))} for k in keys]
    frames={'sources':sources,'source_lookup':lookup,'norwegian':norwegian,
        'identity_source_overrides':pd.DataFrame(overrides,columns=['source_id','expected_raw_issn_l','target_issn_l']),
        'identity_aliases':pd.DataFrame(aliases,columns=['from_issn_l','to_issn_l'])}
    for name,frame in frames.items(): frame.to_parquet(destination/f'{name}_{year}.parquet',index=False)
    return frames

def annual(year, checkpoint, frames, inputs, overrides, exclusions, website, out, *, memory='8GB',threads=4):
    """Count OJF directly from the deduplicated all-type checkpoint."""
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    q=io.sql_literal
    contract={'year':year,'checkpoint_sha256':io.hash_file(checkpoint),
        'inputs':{n:io.hash_file(Path(inputs)/f'{n}_{year}.parquet') for n in frames},
        'work_overrides_sha256':io.hash_file(overrides),'work_exclusions_sha256':io.hash_file(exclusions),
        'website_sha256':io.hash_file(website),
        'source_sha256':{p.name:io.hash_file(p) for p in Path(__file__).parent.glob('*.py')}}
    contract_file=out/'contract.json'
    if contract_file.exists() and io.read_json(contract_file)!=contract: raise ValueError('Changed completed OJF contract')
    if not contract_file.exists():io.atomic_json(contract_file,contract)
    with duckdb.connect() as con:
        con.execute(f'SET threads={int(threads)}');con.execute(f'SET memory_limit={q(memory)}')
        con.execute(f'SET temp_directory={q(out/"temp")}');con.execute('SET preserve_insertion_order=false')
        con.execute(f'CREATE TEMP VIEW raw AS SELECT * FROM read_parquet({q(checkpoint)})')
        for path in [overrides,exclusions]:
            bad=con.execute(f'''SELECT count(*) FROM read_parquet({q(path)}) o JOIN raw w USING(work_id)
              WHERE w.source_id IS DISTINCT FROM o.expected_source_id OR w.raw_issn_l IS DISTINCT FROM o.expected_raw_issn_l''').fetchone()[0]
            if bad:raise ValueError('Changed Work correction preimage')
        con.execute(f'''CREATE TEMP VIEW repaired AS SELECT w.* EXCLUDE(source_id,raw_issn_l,refs),
          coalesce(o.target_source_id,w.source_id) AS source_id,coalesce(o.target_raw_issn_l,w.raw_issn_l) AS raw_issn_l
          FROM raw w LEFT JOIN read_parquet({q(overrides)}) o USING(work_id)
          LEFT JOIN read_parquet({q(exclusions)}) x USING(work_id)
          WHERE x.work_id IS NULL AND w.year BETWEEN {year-2} AND {year}''')
        audit,query=assembly.projection_sql(year,inputs)
        if len(con.execute(audit).df()):raise ValueError('Changed Source correction preimage')
        metadata=io.completed_stage(con,out/'work_metadata.parquet',query,io.hash_json(contract))
        con.execute(f'CREATE TEMP VIEW m AS SELECT * FROM read_parquet({q(metadata["path"])})')
        totals=con.execute(f'''SELECT effective_issn_l AS journal_id,count(*)::BIGINT AS d_raw,
          count(*) FILTER(refs_count_reported>0)::BIGINT AS d_ref FROM m
          WHERE year BETWEEN {year-2} AND {year-1} AND NOT is_paratext AND work_type IN {TYPES}
          GROUP BY 1''').df()
        votes=con.execute(f'''SELECT effective_issn_l AS journal_id,primary_field,count(*)::BIGINT AS works
          FROM m WHERE year BETWEEN {year-2} AND {year-1} AND NOT is_paratext AND is_ar GROUP BY 1,2''').df()
        fields=assembly.field_modes(votes)
        population=assembly.reporting_population(pd.read_parquet(website),frames['sources'],totals,fields,year)
        norwegian=frames['norwegian']
        ids=norwegian.groupby('issn_l').journal_id.apply(lambda v:json.dumps(sorted(set(v.astype(str)))))
        population['norwegian_register_ids_json']=population.journal_id.map(ids).fillna('[]')
        level=norwegian.groupby('issn_l').norwegian_level.max()
        population['norwegian_level']=population.norwegian_level.fillna(population.journal_id.map(level))
        con.register('evaluation',population[['journal_id']])
        con.execute(f'''CREATE TEMP VIEW targets AS SELECT work_id,effective_issn_l AS journal_id,year AS publication_year,
          coalesce(work_type,'other') AS work_type,refs_count_reported AS observed_references,
          (is_retracted OR coalesce(work_type='retraction',false)) AS is_retracted
          FROM m WHERE year BETWEEN {year-2} AND {year-1} AND NOT is_paratext;
          CREATE TEMP VIEW citing AS SELECT m.work_id,m.effective_issn_l AS journal_id,m.year AS publication_year,w.refs AS "references",
          (m.is_retracted OR coalesce(m.work_type='retraction',false)) AS is_retracted,
          true AS in_n,(m.effective_source_type='journal') AS in_n_journals,true AS in_a_expanded,
          CASE WHEN m.effective_source_type='journal' THEN 'N-journal' ELSE 'N-nonjournal' END AS source_component
          FROM m JOIN raw w USING(work_id) WHERE m.year={year} AND m.in_n AND NOT m.is_paratext''')
        grouped=engine.build_type_counts(con,citation_year=year)
        counts=engine.derive_counts(grouped,policies={'AR_DP_SP':TYPES});scores=engine.build_scores(counts)
        expected=population[['journal_id']].merge(totals,on='journal_id',how='left').fillna({'d_raw':0,'d_ref':0}).set_index('journal_id')
        assembly.assert_denominator_parity(counts,expected)
        output=assembly.wide_export(population,counts,scores,year)
        for name,frame in [('population',population),('field_votes',votes),('counts',counts),('scores',scores),('annual',output)]:
            frame.to_parquet(out/f'{name}.parquet',index=False)
    return output

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--nfans-run',type=Path,required=True,help='Completed public opindx-replicate working directory')
    p.add_argument('--work',type=Path,required=True,help='Separate OJF working directory')
    p.add_argument('--threads',type=int,default=4);p.add_argument('--memory',default='8GB')
    args=p.parse_args();nfans=args.nfans_run.resolve();work=args.work.resolve()
    config=nf.read(nfans/'config/run.json')
    nf.prepare(config['raw_snapshot_root'],nfans/config['norwegian_register_input'],nfans)
    if work==nfans or work.is_relative_to(nfans):raise ValueError('Use a separate OJF work directory')
    extracted=io.run(nfans,work/'extraction',threads=args.threads,memory_limit=args.memory)
    repair=nfans/'intermediates/source-repair-v1'
    overrides=nf.verified(repair/'work_overrides.parquet');exclusions=nf.verified(repair/'excluded_works.parquet')
    latest=nf.verified(repair/'latest_sources.parquet')
    for year in nf.profile()['years']:
        print(f'OJF {year}: preparing open Source identities and Norwegian membership',flush=True)
        frames=annual_inputs(year,latest,nf.verified(repair/f'graph/{year}/annual_pairs.parquet'),
                             nfans/config['norwegian_register_input'],work/'inputs')
        annual(year,Path(extracted['works']['path']),frames,work/'inputs',overrides,exclusions,
               nfans/f'exports/scores_{year}.parquet',work/str(year),memory=args.memory,threads=args.threads)
    from .publication import export_run
    export_run(work,nfans/'exports',work/'annual')
    print('OJF complete annual Parquets and manifest created; use opindx-ojf-export for CSV/XLSX.',flush=True)

if __name__=='__main__': main()
