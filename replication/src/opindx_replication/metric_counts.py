"""Add metric-input citation counts without changing previously calculated scores.

The optional CLI enriches an archived release from verified annual graph and
native-score checkpoints. It never recalculates metrics or publishes files.
"""
from __future__ import annotations
import argparse
from datetime import date
import json
from pathlib import Path

import duckdb
import pandas as pd

from .datasets import digest

COLUMNS = [f'citations_{u}_{t}' for u in ('n', 'oa') for t in ('raw', 'filtered')]


def summarize(con, scores):
    """Aggregate a canonical effective_edges relation over the exact score states."""
    con.register('score_rows', scores)
    totals = {}
    result = pd.DataFrame(index=pd.Index(sorted(set(scores.issn_l)), name='issn_l'))
    for universe in ('n', 'oa'):
        state = scores.loc[scores.universe.eq(universe.upper()), ['issn_l']].drop_duplicates()
        con.register('universe_state', state)
        incoming = con.execute('''
            SELECT e.cited_issn_l AS issn_l, sum(e.n_raw)::BIGINT AS n_raw,
                   sum(e.n_filtered)::BIGINT AS n_filtered
            FROM effective_edges e JOIN universe_state a ON e.citing_issn_l=a.issn_l
            JOIN universe_state b ON e.cited_issn_l=b.issn_l
            WHERE e.citing_issn_l<>e.cited_issn_l GROUP BY e.cited_issn_l
        ''').df().set_index('issn_l').reindex(state.issn_l, fill_value=0)
        for treatment in ('raw', 'filtered'):
            name = f'citations_{universe}_{treatment}'
            result[name] = incoming[f'n_{treatment}'].astype('Int64')
            totals[f'{universe}:{treatment}'] = int(incoming[f'n_{treatment}'].sum())
    return result, totals


def attach(frame, used):
    """Keep recorded columns intact; null means outside this calculation universe."""
    result = frame.copy()
    for name in COLUMNS:
        universe = name.split('_')[1]
        values = result.issn_l.map(used[name]).astype('Int64')
        members = result[f'in_{universe}']
        if values[members].isna().any():
            raise ValueError(f'Missing metric-input counts for members: {name}')
        result[name] = values.where(members, pd.NA)
        if (result[name].dropna() < 0).any():
            raise ValueError(f'Negative metric-input count: {name}')
        if (result[name][members] > result[f'citations_{name.rsplit("_",1)[1]}'][members]).any():
            raise ValueError(f'Used citations exceed recorded citations: {name}')
    return result


def from_effective_graph(frame, edges, scores):
    """Replication pipeline hook; inputs are the same projected graph used to score."""
    with duckdb.connect() as con:
        con.register('effective_edges', edges)
        used, _ = summarize(con, scores)
    return attach(frame, used)


def enrich(run, source, output, tag='2026-09-v3'):
    from .pipeline import decisions, verified
    from .core.identity import _project_one, _valid_issn_l
    run, source, output = Path(run), Path(source), Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a new empty output folder; preserve prior releases')
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((source/'manifest.json').read_text(encoding='utf-8'))
    manifest.update(run=tag, schema_version=3, created=date.today().isoformat())
    reports = []
    for year in manifest['years']:
        maps, _, _ = decisions(year)
        graph = run/'intermediates/source-repair-v1/graph'/str(year)
        paths = {name:verified(graph/f'{name}_raw_identity.parquet') for name in ('counts','edges')}
        paths['pairs'] = verified(graph/'annual_pairs.parquet')
        report_path = run/f'checks/source-repair-v1/{year}/run_report.json'
        old_report = json.loads(report_path.read_text(encoding='utf-8'))
        native = run/old_report['outputs']['native_scores']['path']
        if old_report['status'] != 'PASS' or digest(native) != old_report['outputs']['native_scores']['sha256']:
            raise ValueError('Native scores differ from their validated checkpoint')
        baseline = source/f'scores_{year}.parquet'
        if digest(baseline) != manifest['assets'][baseline.name]['sha256']:
            raise ValueError('Archived release integrity check failed')
        frame, scores = pd.read_parquet(baseline), pd.read_parquet(native)
        mapping = pd.read_parquet(paths['pairs'], columns=['source_id','raw_issn_l']).drop_duplicates()
        mapping['issn_l'] = [_project_one(s,k,maps) for s,k in mapping.itertuples(index=False,name=None)]
        if not mapping.issn_l.map(_valid_issn_l).all():
            raise ValueError('Unresolved graph identities')
        with duckdb.connect() as con:
            con.execute("SET memory_limit='6GB'")
            con.register('mapping', mapping)
            con.from_parquet(str(paths['edges'])).create_view('raw_edges')
            con.execute('''CREATE TEMP TABLE effective_edges AS
                SELECT a.issn_l citing_issn_l,b.issn_l cited_issn_l,
                       sum(e.n_raw)::BIGINT n_raw,sum(e.n_filtered)::BIGINT n_filtered
                FROM raw_edges e JOIN mapping a ON e.citing_source_id IS NOT DISTINCT FROM a.source_id
                    AND e.raw_citing_issn_l IS NOT DISTINCT FROM a.raw_issn_l
                JOIN mapping b ON e.cited_source_id IS NOT DISTINCT FROM b.source_id
                    AND e.raw_cited_issn_l IS NOT DISTINCT FROM b.raw_issn_l
                GROUP BY a.issn_l,b.issn_l''')
            if con.sql('select sum(n_raw),sum(n_filtered) from raw_edges').fetchone() != con.sql('select sum(n_raw),sum(n_filtered) from effective_edges').fetchone():
                raise ValueError('Identity projection changed citation mass')
            used, totals = summarize(con, scores)
            recorded = con.sql('''select cited_issn_l issn_l,sum(n_raw)::BIGINT n_raw,
                sum(n_filtered)::BIGINT n_filtered from effective_edges
                where citing_issn_l<>cited_issn_l group by cited_issn_l''').df().set_index('issn_l')
            counts = pd.read_parquet(paths['counts'])
            counts['issn_l'] = [_project_one(s,k,maps) for s,k in zip(counts.source_id,counts.raw_issn_l)]
            counts = counts.groupby('issn_l')[['a_raw','a_filtered']].sum()
            for treatment in ('raw','filtered'):
                observed = frame.issn_l.map(counts[f'a_{treatment}']).fillna(0).astype('int64')
                pd.testing.assert_series_equal(observed, frame[f'publications_{treatment}'], check_names=False, check_dtype=False, check_exact=True)
                observed = frame.issn_l.map(recorded[f'n_{treatment}']).fillna(0).astype('int64')
                pd.testing.assert_series_equal(observed, frame[f'citations_{treatment}'], check_names=False, check_dtype=False, check_exact=True)
            for audit in old_report['solver_audit']:
                u,t = audit['universe'].lower(),audit['treatment'].lower()
                if totals[f'{u}:{t}'] != audit['nonself_edge_weight']:
                    raise ValueError('Used citation totals differ from solver input audit')
                state = scores.loc[(scores.universe.str.lower()==u)&(scores.treatment.str.lower()==t)].set_index('issn_l')
                if int(counts[f'a_{t}'].reindex(state.index).fillna(0).sum()) != audit['article_mass']:
                    raise ValueError('Publication totals differ from solver input audit')
                for public, original in [('share','ef'),('per_article','ais')]:
                    pd.testing.assert_series_equal(frame.issn_l.map(state[original]), frame[f'{public}_{u}_{t}'],check_names=False,check_dtype=False,check_exact=True)
        enriched = attach(frame, used)
        pd.testing.assert_frame_equal(enriched[frame.columns], frame, check_exact=True)
        target = output/baseline.name
        enriched.to_parquet(target,index=False)
        pd.testing.assert_frame_equal(pd.read_parquet(target),enriched,check_exact=True)
        asset = {'sha256':digest(target),'bytes':target.stat().st_size,'rows':len(enriched)}
        manifest['assets'][target.name] = asset
        reports.append({'year':year,'status':'PASS','rows':len(enriched),
            'original_columns_exact':True,'scores_exact':True,'recorded_counts_exact':True,
            'used_count_solver_totals_exact':True,'used_citation_totals':totals,
            'source_parquet_sha256':digest(baseline),'native_scores_sha256':digest(native),
            'graph_sha256':{k:digest(v) for k,v in paths.items()},'output':asset})
        print(f'{year}: PASS; original values unchanged; universe counts match solver totals',flush=True)
    manifest['metric_input_counts'] = {'version':1,'scope':'selected universe, treatment and citation year; no journal self-citations',
        'recorded_scope':'all cached citing sources, same publication window, excluding journal self-citations'}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    validation = {'status':'PASS','scope':'Additive count enrichment from validated graph and score checkpoints; no metric recalculation',
        'annual':reports,'total_rows':sum(r['rows'] for r in reports),'publication_performed':False}
    (output/'metric-counts-validation.json').write_text(json.dumps(validation,indent=2)+'\n',encoding='utf-8')
    return validation


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',required=True,type=Path)
    p.add_argument('--source',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--tag',default='2026-09-v3')
    args=p.parse_args()
    enrich(args.run,args.source,args.output,args.tag)
