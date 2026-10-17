"""Public OJF annual schema and browser sidecars, without private inputs."""
from pathlib import Path
import argparse
import hashlib
import json
import pandas as pd

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def normalize(f, nf, votes, registry):
    nf=nf.set_index('openalex_id')
    named = votes.loc[votes.primary_field.ne('Unknown')].sort_values(
        ['journal_id','works','primary_field'],ascending=[True,False,True])
    named['position'] = named.groupby('journal_id').cumcount()+1
    top = named.loc[named.position.le(3)]
    names = top.pivot(index='journal_id',columns='position',values='primary_field')
    counts = top.pivot(index='journal_id',columns='position',values='works')
    out = f[['openalex_id','title','publisher','issns','is_open_access','score_year',
             'norwegian_level','norwegian_primary_id','norwegian_entries_json']].copy()
    out['canonical_journal_key'] = f.journal_id
    out['issn_l'] = f.journal_id.where(f.journal_id.str.fullmatch(r'[0-9]{4}-[0-9]{3}[0-9X]'))
    for col in ('title','publisher','issns','is_open_access'):
        out[col] = out[col].replace('', None).fillna(out.openalex_id.map(nf[col]))
    out['in_n'] = f.reporting_in_n.astype(bool)
    # Display all recipients with at least one OJF; zero is a defined score.
    out['in_oa'] = f.reporting_in_oa.astype(bool)
    out['oa_field'] = f.ojf_oa_field
    out['oa_domain'] = f.oa_domain
    out['metric_window_years'] = 2
    out['publications_raw'] = f.d_raw
    out['publications_filtered'] = f.d_ref
    out['citations_raw'] = f.c_raw
    out['citations_filtered'] = f.c_ref
    out['reference_coverage_pct'] = (100*f.d_ref/f.d_raw.where(f.d_raw.gt(0)))
    out['active_years'] = pd.Series([None]*len(f),dtype='Int64')
    for source_col, dest_col in [('ojf_raw_raw','ojf_raw'),('ojf_ref_ref','ojf_filtered'),
        ('status_raw_raw','ojf_status_raw'),('status_ref_ref','ojf_status_filtered'),
        ('maturity_status','maturity_status'),('citation_year_complete','citation_year_complete'),
        ('freeze_eligible_from','freeze_eligible_from')]:
        out[dest_col] = f[source_col]
    out['oa_field_classified_works'] = f.ojf_field_classified_works
    out['oa_field_modal_works'] = f.ojf_field_modal_works
    out['oa_field_tied_modes'] = f.ojf_field_tied_modes
    out['oa_field_modal_share'] = f.ojf_field_modal_works/f.ojf_field_classified_works.where(f.ojf_field_classified_works.gt(0))
    out['oa_field_classification_coverage'] = f.ojf_field_classified_works/f.ojf_field_raw_ar_works.where(f.ojf_field_raw_ar_works.gt(0))
    for i in range(1,4):
        out[f'oa_field_{i}'] = f.journal_id.map(names[i]) if i in names else None
        out[f'oa_field_{i}_works'] = f.journal_id.map(counts[i]).fillna(0).astype('int64') if i in counts else 0
    out['oa_field_other_works'] = out.oa_field_classified_works-out[[f'oa_field_{i}_works' for i in range(1,4)]].sum(axis=1)
    assert out.oa_field_other_works.ge(0).all()
    registry = registry.copy()
    registry['journal_id'] = registry.journal_id.astype(str)
    by_id = registry.drop_duplicates('journal_id').set_index('journal_id').to_dict('index')
    def entries(value):
        ids = [str(v) for v in json.loads(value or '[]')]
        result = [dict(id=i,field=str(by_id.get(i,{}).get('field') or ''),
            title=str(by_id.get(i,{}).get('original_title') or '')) for i in ids]
        return ids,result
    register = f.norwegian_register_ids_json.map(entries)
    out['norwegian_register_url'] = register.map(lambda p:'; '.join(f'https://kanalregister.hkdir.no/en/tidsskrift?id={i}' for i in p[0]))
    out['norwegian_field'] = register.map(lambda p:' | '.join(dict.fromkeys(e['field'] for e in p[1] if e['field'])))
    out['norwegian_entries_json'] = register.map(lambda p:json.dumps(p[1]))
    out['norwegian_primary_id'] = [str(p) if pd.notna(p) and str(p) in ids else ids[0] if ids else ''
        for p,(ids,_) in zip(out.norwegian_primary_id,register)]
    assert not out.duplicated(['openalex_id','score_year']).any()
    for treatment,denom,citations in [('raw','d_raw','c_raw'),('filtered','d_ref','c_ref')]:
        expected = f[citations]/f[denom].where(f[denom].gt(0))
        pd.testing.assert_series_equal(out[f'ojf_{treatment}'],f['ojf_raw_raw' if treatment=='raw' else 'ojf_ref_ref'],check_names=False,check_exact=True)
        assert ((out[f'ojf_{treatment}']-expected).abs().fillna(0)<1e-12).all()
    for source,destination in {'c_non_citable':'citations_to_non_citable',
        'c_self':'citations_self_raw','c_ref_self':'citations_self_filtered',
        'c_raw_n_nonjournal':'citations_nonjournal_raw','c_ref_n_nonjournal':'citations_nonjournal_filtered',
        'snapshot_date':'openalex_snapshot','citable_policy':'citable_policy',
        'citing_universe':'citing_sources','primary_retraction_mode':'retraction_policy',
        'target_start_year':'publication_start_year','target_end_year':'publication_end_year'}.items():
        out[destination]=f[source]
    return out

def export_run(work, nfans, output):
    work,nfans,output=map(Path,(work,nfans,output));output.mkdir(parents=True,exist_ok=True)
    assets={}
    for year in range(2022,2027):
        f=pd.read_parquet(work/str(year)/'annual.parquet')
        votes=pd.read_parquet(work/str(year)/'field_votes.parquet')
        registry=pd.read_parquet(work/f'inputs/norwegian_{year}.parquet')
        out=normalize(f,pd.read_parquet(nfans/f'scores_{year}.parquet'),votes,registry)
        target=output/f'scores_{year}.parquet';out.to_parquet(target,index=False)
        assets[target.name]={'sha256':sha(target),'bytes':target.stat().st_size,'rows':len(out)}
    manifest={'run':'2026-09-v4','dummy':False,'years':list(range(2022,2027)),
        'openalex_snapshot':'2026-09-23','norwegian_register_snapshot':'2026-07-26',
        'metric':'OJF','assets':assets,'maturity':{'freeze_delay_months_after_citation_year_end':12},
        'source_licenses':{'OpenAlex':'CC0-1.0','Norwegian Register':'NLOD-2.0'}}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    return manifest

def install(annual, data):
    """Install released annual files and derived history/rank browser sidecars."""
    annual,data=map(Path,(annual,data))
    manifest_file=annual/'ojf_manifest.json' if (annual/'ojf_manifest.json').exists() else annual/'manifest.json'
    manifest=json.loads(manifest_file.read_text(encoding='utf-8'))
    index=json.loads((data/'index.json').read_text(encoding='utf-8'))
    if manifest['maturity']['freeze_delay_months_after_citation_year_end']!=12:raise ValueError('Unexpected maturity policy')
    destination=data/'ojf';destination.mkdir(parents=True,exist_ok=True);frames=[];assets={}
    for entry in index['years']:
        year=entry['year'];name=f'ojf_scores_{year}.parquet' if (annual/f'ojf_scores_{year}.parquet').exists() else f'scores_{year}.parquet'
        source=annual/name
        if sha(source)!=manifest['assets'][source.name]['sha256']:raise ValueError('Unverified released OJF file')
        out=pd.read_parquet(source)
        if {'benchmark','official_jif','is_j','jcr_title'}&set(out):raise ValueError('Restricted OJF column')
        target=destination/f'scores_{year}.parquet'
        if source.resolve()!=target.resolve():target.write_bytes(source.read_bytes())
        assets[target.name]={'sha256':sha(target),'rows':len(out),'bytes':target.stat().st_size}
        entry['ojf_fields']={u:sorted(out.loc[out[f'in_{u}'],'oa_field'].dropna().unique()) for u in entry['universes']}
        frames.append(out)
    history=pd.concat(frames,ignore_index=True)
    (destination/'history').mkdir(exist_ok=True);(destination/'ranks').mkdir(exist_ok=True)
    for suffix,frame in history.groupby(history.openalex_id.str[-2:]):frame.to_parquet(destination/'history'/f'{suffix}.parquet',index=False)
    for frame in frames:
        year=int(frame.score_year.iloc[0])
        for universe in ('n','oa'):
            for treatment in ('raw','filtered'):
                columns=['openalex_id','oa_field','reference_coverage_pct','active_years',f'in_{universe}',f'ojf_{treatment}']
                frame.loc[frame[f'in_{universe}'],columns].to_parquet(destination/'ranks'/f'{year}-{universe}-ojf-{treatment}.parquet',index=False)
    index['ojf']={'version':1,'annual_path':'ojf','history_path':'ojf/history','rank_path':'ojf/ranks',
        'citing_sources':'Norwegian Register sources','window_years':2,'assets':assets}
    (data/'index.json').write_text(json.dumps(index,indent=2)+'\n',encoding='utf-8')
    return {'status':'PASS','years':manifest['years'],'annual_sha256':{n:v['sha256'] for n,v in assets.items()}}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('annual',type=Path);p.add_argument('site_data',type=Path)
    a=p.parse_args();print(json.dumps(install(a.annual,a.site_data),indent=2))
if __name__=='__main__':main()
