"""Public OJF annual schema and browser sidecars, without private inputs."""
from pathlib import Path
import argparse
import hashlib
import json
import pandas as pd

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def normalize(f, nf, votes, registry, *, window_years=2):
    nf=nf.set_index('openalex_id')
    named = votes.loc[votes.primary_field.ne('Unknown')].sort_values(
        ['journal_id','works','primary_field'],ascending=[True,False,True])
    named['position'] = named.groupby('journal_id').cumcount()+1
    top = named.loc[named.position.le(3)]
    names = top.pivot(index='journal_id',columns='position',values='primary_field')
    counts = top.pivot(index='journal_id',columns='position',values='works')
    out = f[['openalex_id','title','publisher','issns','is_open_access','score_year',
             'norwegian_level','norwegian_primary_id','norwegian_entries_json']].copy()
    out['norwegian_level'] = out['norwegian_level'].astype('string')
    out['canonical_journal_key'] = f.journal_id
    out['issn_l'] = f.journal_id.where(f.journal_id.str.fullmatch(r'[0-9]{4}-[0-9]{3}[0-9X]'))
    for col in ('title','publisher','issns','is_open_access'):
        out[col] = out[col].replace('', None).fillna(out.openalex_id.map(nf[col]))
    out['in_n'] = f.reporting_in_n.astype(bool)
    # Display all recipients with at least one OJF; zero is a defined score.
    out['in_oa'] = f.reporting_in_oa.astype(bool)
    out['oa_field'] = f.ojf_oa_field
    out['oa_domain'] = f.oa_domain
    out['metric_window_years'] = window_years
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

def export_run(work, nfans, output, *, window_years=2):
    work,nfans,output=map(Path,(work,nfans,output));output.mkdir(parents=True,exist_ok=True)
    assets={}
    for year in range(2022,2027):
        f=pd.read_parquet(work/str(year)/'annual.parquet')
        votes=pd.read_parquet(work/str(year)/'field_votes.parquet')
        registry=pd.read_parquet(work/f'inputs/norwegian_{year}.parquet')
        out=normalize(f,pd.read_parquet(nfans/f'scores_{year}.parquet'),votes,registry,window_years=window_years)
        target=output/f'scores_{year}.parquet';out.to_parquet(target,index=False)
        assets[target.name]={'sha256':sha(target),'bytes':target.stat().st_size,'rows':len(out)}
    manifest={'run':'2026-09-v4' if window_years==2 else '2026-09-ojf5-working','dummy':False,'years':list(range(2022,2027)),
        'openalex_snapshot':'2026-09-23','norwegian_register_snapshot':'2026-07-26',
        'metric':f'OJF{window_years}','window_years':window_years,'assets':assets,'maturity':{'freeze_delay_months_after_citation_year_end':12},
        'source_licenses':{'OpenAlex':'CC0-1.0','Norwegian Register':'NLOD-2.0'}}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    return manifest

def _annual_manifest(annual, window_years):
    prefix='ojf' if window_years==2 else 'ojf5'
    released=Path(annual)/f'{prefix}_manifest.json'
    return released if released.exists() else Path(annual)/'manifest.json'


def _read_verified_annual(annual, year, window_years):
    annual=Path(annual)
    manifest_file=_annual_manifest(annual,window_years)
    manifest=json.loads(manifest_file.read_text(encoding='utf-8'))
    if manifest['maturity']['freeze_delay_months_after_citation_year_end']!=12:raise ValueError('Unexpected maturity policy')
    if manifest.get('window_years',2)!=window_years:raise ValueError('Wrong OJF publication window')
    prefix='ojf' if window_years==2 else 'ojf5'
    name=f'{prefix}_scores_{year}.parquet' if (annual/f'{prefix}_scores_{year}.parquet').exists() else f'scores_{year}.parquet'
    source=annual/name
    if sha(source)!=manifest['assets'][source.name]['sha256']:raise ValueError('Unverified OJF annual file')
    frame=pd.read_parquet(source)
    if {'benchmark','official_jif','is_j','jcr_title'}&set(frame):raise ValueError('Restricted OJF column')
    if frame.openalex_id.isna().any() or frame.openalex_id.duplicated().any():raise ValueError('Invalid OJF source identities')
    if not frame.metric_window_years.eq(window_years).all():raise ValueError('Wrong row publication window')
    return source,frame,manifest


def _with_counterpart(native, other, window_years, year):
    """Add comparison scores without borrowing counts or fields across windows."""
    original=native.set_index('openalex_id')
    companion=other.set_index('openalex_id')
    additional=companion.index.difference(original.index,sort=False)
    result=original.reindex(original.index.append(additional)).copy()
    static=['title','publisher','issns','is_open_access','in_n','in_oa','canonical_journal_key','issn_l',
            'norwegian_level','norwegian_primary_id','norwegian_entries_json','norwegian_register_url','norwegian_field',
            'maturity_status','citation_year_complete','freeze_eligible_from','openalex_snapshot','citable_policy',
            'citing_sources','retraction_policy']
    for col in static:
        if col in result and col in companion:result.loc[additional,col]=companion.loc[additional,col]
    result.loc[additional,'score_year']=year
    result.loc[additional,'metric_window_years']=window_years
    result.loc[additional,'publication_start_year']=year-window_years
    result.loc[additional,'publication_end_year']=year-1
    result.loc[additional,'oa_field']='Unknown'
    for col in ['oa_field_classified_works','oa_field_modal_works','oa_field_tied_modes','oa_field_other_works',
                'oa_field_1_works','oa_field_2_works','oa_field_3_works']:
        result.loc[additional,col]=0
    for col in ['in_n','in_oa']:result[col]=result[col].fillna(False).astype(bool)
    for treatment in ('raw','filtered'):
        result[f'ojf{window_years}_{treatment}']=result[f'ojf_{treatment}']
        result[f'ojf{7-window_years}_{treatment}']=companion[f'ojf_{treatment}'].reindex(result.index)
    # Every original value survives; unmatched companion rows have no native-window counts.
    pd.testing.assert_frame_equal(result.loc[original.index,original.columns],original,check_dtype=False,check_exact=True)
    return result.reset_index()


def install(annual, data, *, window_years=2, other_annual=None):
    """Install released annual files and derived history/rank browser sidecars."""
    annual,data=map(Path,(annual,data))
    manifest_file=_annual_manifest(annual,window_years)
    manifest=json.loads(manifest_file.read_text(encoding='utf-8'))
    index=json.loads((data/'index.json').read_text(encoding='utf-8'))
    if manifest['maturity']['freeze_delay_months_after_citation_year_end']!=12:raise ValueError('Unexpected maturity policy')
    if isinstance(window_years,bool) or window_years not in (2,5):raise ValueError('window_years must be 2 or 5')
    folder='ojf' if window_years==2 else 'ojf5'
    metric='ojf' if window_years==2 else 'ojf5'
    destination=data/folder;destination.mkdir(parents=True,exist_ok=True);frames=[];assets={}
    for entry in index['years']:
        year=entry['year']
        source,out,_=_read_verified_annual(annual,year,window_years)
        if other_annual is not None:
            _,other,_=_read_verified_annual(other_annual,year,7-window_years)
            out=_with_counterpart(out,other,window_years,year)
        target=destination/f'scores_{year}.parquet'
        if other_annual is not None:out.to_parquet(target,index=False)
        elif source.resolve()!=target.resolve():target.write_bytes(source.read_bytes())
        assets[target.name]={'sha256':sha(target),'rows':len(out),'bytes':target.stat().st_size}
        entry[f'{folder}_fields']={u:sorted(out.loc[out[f'in_{u}'],'oa_field'].dropna().unique()) for u in entry['universes']}
        frames.append(out)
    history=pd.concat(frames,ignore_index=True)
    (destination/'history').mkdir(exist_ok=True);(destination/'ranks').mkdir(exist_ok=True)
    for suffix,frame in history.groupby(history.openalex_id.str[-2:]):frame.to_parquet(destination/'history'/f'{suffix}.parquet',index=False)
    for frame in frames:
        year=int(frame.score_year.iloc[0])
        for universe in ('n','oa'):
            for treatment in ('raw','filtered'):
                columns=['openalex_id','oa_field','reference_coverage_pct','active_years','metric_window_years',f'in_{universe}',f'ojf_{treatment}']
                if f'ojf{window_years}_{treatment}' in frame:columns.append(f'ojf{window_years}_{treatment}')
                frame.loc[frame[f'in_{universe}'],columns].to_parquet(destination/'ranks'/f'{year}-{universe}-{metric}-{treatment}.parquet',index=False)
    index[folder]={'version':1,'annual_path':folder,'history_path':f'{folder}/history','rank_path':f'{folder}/ranks',
        'citing_sources':'Norwegian Register sources','window_years':window_years,'assets':assets}
    (data/'index.json').write_text(json.dumps(index,indent=2)+'\n',encoding='utf-8')
    return {'status':'PASS','years':manifest['years'],'annual_sha256':{n:v['sha256'] for n,v in assets.items()}}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('annual',type=Path);p.add_argument('site_data',type=Path)
    a=p.parse_args();print(json.dumps(install(a.annual,a.site_data),indent=2))
if __name__=='__main__':main()
