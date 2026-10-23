"""Pure assembly helpers for the open OJF calculation."""
from pathlib import Path
import numpy as np
import pandas as pd
from . import checkpoint as io

def primary(year):
    return 'exclude_snapshot' if year>=2024 else 'all'

def projection_sql(year,inputs,relation='repaired'):
    """The preserved identity cascade, generalized only in score-year range."""
    q=io.sql_literal;d=Path(inputs)
    overrides=q(d/f'identity_source_overrides_{year}.parquet');aliases=q(d/f'identity_aliases_{year}.parquet')
    sources=q(d/f'sources_{year}.parquet');lookup=q(d/f'source_lookup_{year}.parquet')
    source="CASE WHEN regexp_full_match(trim(coalesce(source_id,'')), 'S[0-9]+') THEN 'https://openalex.org/'||trim(source_id) ELSE trim(coalesce(source_id,'')) END"
    raw="upper(trim(coalesce(raw_issn_l,'')))"
    clean=f'SELECT *,{source} AS _src,{raw} AS _raw FROM {relation}'
    audit=f"""WITH c AS ({clean}) SELECT c._src,c._raw,count(*) FROM c
      JOIN (SELECT DISTINCT source_id FROM read_parquet({overrides})) p ON c._src=p.source_id
      LEFT JOIN read_parquet({overrides}) o ON c._src=o.source_id AND c._raw=o.expected_raw_issn_l
      WHERE o.source_id IS NULL GROUP BY ALL"""
    checksum='+'.join(f'try_cast(substr(_key,{p},1) AS INTEGER)*{w}' for p,w in zip((1,2,3,4,6,7,8),range(8,1,-1)))
    checksum+="+CASE WHEN right(_key,1)='X' THEN 10 ELSE try_cast(right(_key,1) AS INTEGER) END"
    query=f"""WITH c AS ({clean}),k AS (
      SELECT c.*,coalesce(o.target_issn_l,a.to_issn_l,c._raw) AS _key FROM c
      LEFT JOIN read_parquet({overrides}) o ON c._src=o.source_id AND c._raw=o.expected_raw_issn_l
      LEFT JOIN read_parquet({aliases}) a ON c._raw=a.from_issn_l),r AS (
      SELECT *,CASE WHEN regexp_full_match(_key,'[0-9]{{4}}-[0-9]{{3}}[0-9X]') AND ({checksum})%11=0
        THEN _key WHEN _src<>'' THEN 'oa:'||_src ELSE NULL END AS effective_issn_l FROM k)
      SELECT r.* EXCLUDE(_src,_raw,_key),coalesce(m.source_type,l.source_type,r.source_type,'Unknown') AS effective_source_type,
        coalesce(m.is_n,false) AS in_n
      FROM r LEFT JOIN read_parquet({sources}) m ON r.effective_issn_l=m.issn_l
      LEFT JOIN read_parquet({lookup}) l ON r._src=l.source_id"""
    return audit,query

def field_modes(votes):
    """Treatment-independent named modes; zero named votes stay Unknown."""
    totals=votes.groupby('journal_id',sort=True).works.sum()
    named=votes.loc[votes.primary_field.ne('Unknown')].copy()
    top=named.sort_values(['journal_id','works','primary_field'],ascending=[True,False,True],kind='stable').drop_duplicates('journal_id').set_index('journal_id')
    classified=named.groupby('journal_id').works.sum()
    highest=named.groupby('journal_id').works.transform('max')
    ties=named.loc[named.works.eq(highest)].groupby('journal_id').size()
    result=pd.DataFrame({'journal_id':totals.index,'ojf_oa_field':top.primary_field.reindex(totals.index).fillna('Unknown').to_numpy(),
        'ojf_field_raw_ar_works':totals.to_numpy(dtype='int64'),
        'ojf_field_classified_works':classified.reindex(totals.index).fillna(0).to_numpy(dtype='int64'),
        'ojf_field_modal_works':top.works.reindex(totals.index).fillna(0).to_numpy(dtype='int64'),
        'ojf_field_tied_modes':ties.reindex(totals.index).fillna(0).to_numpy(dtype='int64')})
    return result

def assert_denominator_parity(counts,expected):
    """Compare exact integer values and identity labels, allowing index storage types."""
    for column in ['d_raw','d_ref']:
        actual=counts.set_index('journal_id')[column].sort_index()
        wanted=expected[column].astype('int64').sort_index()
        pd.testing.assert_series_equal(actual,wanted,check_names=False,check_index_type=False,check_exact=True)

def reporting_population(website,sources,denominators,fields,year):
    """Current website rows plus independently eligible canonical journal keys."""
    website=website.copy();sources=sources.copy()
    sources=sources.loc[sources.source_type.eq('journal')].copy()
    if sources.issn_l.duplicated().any() or website.issn_l.duplicated().any():raise ValueError('Unique source/website identities required')
    keys=set(website.issn_l)
    eligible=set(denominators.loc[denominators.d_raw.gt(0),'journal_id'])
    add=sources.loc[sources.issn_l.isin(eligible-keys)].sort_values('issn_l').copy()
    basic=['issn_l','openalex_id','title','publisher','issns','oa_domain','oa_field','is_open_access']
    base=website[basic].copy();base['in_existing_website']=True
    extra=add[basic].copy();extra['in_existing_website']=False
    pop=pd.concat([base,extra],ignore_index=True).rename(columns={'issn_l':'journal_id'})
    # Website metadata use semicolon-delimited text; prepared Source metadata
    # retain arrays. Keep one explicit interchange type across recipient arms.
    pop['issns']=pop.issns.map(lambda v:v if isinstance(v,str) else ('; '.join(str(x) for x in v) if isinstance(v,(list,tuple,np.ndarray)) else '')).astype('string')
    pop['score_year']=year;pop['source_type']='journal'
    pop['reporting_in_n']=pop.journal_id.map(sources.set_index('issn_l').is_n).fillna(False).astype(bool)
    pop['reporting_in_oa']=True;pop['website_row']=np.arange(len(pop),dtype=np.int64)
    ncols=[c for c in ['norwegian_level','norwegian_register_url','norwegian_primary_id','norwegian_entries_json'] if c in website]
    lookup=website.set_index('issn_l')
    for col in ncols:pop[col]=pop.journal_id.map(lookup[col])
    pop=pop.merge(fields,on='journal_id',how='left',sort=False,validate='one_to_one')
    pop['ojf_oa_field']=pop.ojf_oa_field.fillna('Unknown')
    for col in ['ojf_field_raw_ar_works','ojf_field_classified_works','ojf_field_modal_works','ojf_field_tied_modes']:
        pop[col]=pop[col].fillna(0).astype('int64')
    if not pop.journal_id.head(len(website)).eq(website.issn_l.reset_index(drop=True)).all():raise AssertionError('Existing row order changed')
    if pop.journal_id.isna().any() or pop.journal_id.duplicated().any():raise ValueError('Invalid recipient keys')
    return pop

def wide_export(pop,counts,scores,year,*,window_years=2):
    mode=primary(year);suffix='' if mode=='all' else '_exclude_snapshot'
    cols=['c_raw','c_ref','c_non_citable','c_self','c_ref_self','c_raw_n_nonjournal','c_ref_n_nonjournal']
    c=counts[['journal_id','d_raw','d_ref']+[n+suffix for n in cols]].rename(columns={n+suffix:n for n in cols})
    out=pop.merge(c,on='journal_id',how='left',sort=False,validate='one_to_one')
    if out[['c_raw','c_ref','d_raw','d_ref']].isna().any().any():raise ValueError('Missing count row')
    for e,d in [('RAW','RAW'),('REF','RAW'),('RAW','REF'),('REF','REF')]:
        tag=e.lower()+'_'+d.lower()
        v=scores.loc[scores.retraction_mode.eq(mode)&scores.numerator_eligibility.eq(e)&scores.denominator_eligibility.eq(d),['journal_id','score','score_status']]
        out=out.merge(v.rename(columns={'score':'ojf_'+tag,'score_status':'status_'+tag}),on='journal_id',how='left',sort=False,validate='one_to_one')
    out['zero_ref_citable_items']=out.d_raw-out.d_ref;out['zero_ref_citable_target_citations']=out.c_raw-out.c_ref
    out['snapshot_date']='2026-09-23';out['method_version']='ojf-n-citing-v1';out['citable_policy']='AR_DP_SP';out['citing_universe']='N'
    out['primary_retraction_mode']=mode;out['target_start_year']=year-window_years;out['target_end_year']=year-1
    out['maturity_status']='provisional' if year>=2025 else 'freeze_eligible'
    out['citation_year_complete']=year<2026;out['freeze_eligible_from']=f'{year+2}-01-01'
    if not out.journal_id.equals(pop.journal_id):raise AssertionError('Recipient order changed')
    if {'benchmark','official_jif','is_j','jcr_title'}&set(out):raise AssertionError('Commercial field in operational output')
    return out
