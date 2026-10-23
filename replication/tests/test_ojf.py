"""Independent open-count fixtures: windows, filtering, zeros and source scope."""
import unittest
import duckdb
import pandas as pd
import tempfile
import json
from pathlib import Path
from opindx_replication.ojf import engine
from opindx_replication.ojf.counting_base import build_scores
from opindx_replication.ojf.assembly import field_modes,primary
from opindx_replication.ojf.pipeline import annual
from opindx_replication.ojf.checkpoint import checkpoint_sql,sql_literal
from opindx_replication.ojf.publication import _with_counterpart,install,sha
from opindx_replication.datasets import ojf_description

class OJFTests(unittest.TestCase):
    def setUp(self):
        self.con=duckdb.connect()
        self.con.execute('''CREATE TABLE targets(work_id VARCHAR,journal_id VARCHAR,publication_year INTEGER,
          work_type VARCHAR,observed_references INTEGER,is_retracted BOOLEAN);
          INSERT INTO targets VALUES ('T1','X',2022,'article',0,false),('T2','X',2023,'review',2,true),
          ('T3','X',2022,'data-paper',0,false),('T4','X',2023,'software-paper',0,false),
          ('T5','X',2023,'conference-paper',0,false),('T6','X',2023,'editorial',0,false),('T7','X',2022,'data-paper',2,false);
          CREATE TABLE citing(work_id VARCHAR,journal_id VARCHAR,publication_year INTEGER,"references" VARCHAR[],
            is_retracted BOOLEAN,in_n BOOLEAN,in_n_journals BOOLEAN,in_a_expanded BOOLEAN,source_component VARCHAR);
          INSERT INTO citing VALUES ('C1','X',2024,['T1','T2','T3','T4','T5','T6','T7','T3'],false,true,true,true,'N-journal');
          CREATE TABLE evaluation(journal_id VARCHAR); INSERT INTO evaluation VALUES ('X'),('empty');''')
    def tearDown(self):self.con.close()
    def counts(self):return engine.build_counts(self.con,policies={'AR_DP_SP':('article','review','data-paper','software-paper')})
    def test_four_citable_types_and_noncitable_citations(self):
        x=self.counts().set_index('journal_id').loc['X']
        self.assertEqual((x.d_raw,x.d_ref,x.c_raw,x.c_ref),(5,2,7,4))
        self.assertEqual(x.c_non_citable,2)
        self.assertEqual(set(self.counts().universe),{'N'})
    def test_self_citations_retained_and_duplicate_refs_removed(self):
        x=self.counts().set_index('journal_id').loc['X']
        self.assertEqual(x.c_self,7);self.assertEqual(x.c_ref_self,4)
    def test_nonjournal_n_source_included_outside_n_ignored(self):
        self.con.execute('''INSERT INTO citing VALUES
            ('B','book',2024,['T7'],false,true,false,true,'N-nonjournal'),
            ('outside','other',2024,['T7'],false,false,false,true,'outside-N-journal')''')
        x=self.counts().set_index('journal_id').loc['X']
        self.assertEqual((x.c_raw,x.c_ref,x.c_raw_n_nonjournal),(8,5,1))
    def test_retractions_change_citations_not_denominators(self):
        x=self.counts().set_index('journal_id').loc['X']
        self.assertEqual((x.d_raw,x.d_ref),(5,2))
        self.assertEqual((x.c_raw_exclude_snapshot,x.c_ref_exclude_snapshot),(6,3))
    def test_window_enforced(self):
        self.con.execute("INSERT INTO targets VALUES ('old','X',2021,'article',1,false); UPDATE citing SET \"references\"=['old']")
        x=self.counts().set_index('journal_id').loc['X']
        self.assertEqual(x.c_raw,0);self.assertEqual(x.d_raw,5)
    def test_five_year_window_includes_older_targets_but_not_current_or_sixth_year(self):
        self.con.execute('''INSERT INTO targets VALUES
            ('first','X',2019,'article',1,false),('middle','X',2021,'article',0,false),
            ('too_old','X',2018,'article',1,false),('current','X',2024,'article',1,false);
            UPDATE citing SET "references"=['first','middle','too_old','current','T7']''')
        two=engine.build_counts(self.con,window_years=2,policies={'K':('article','review','data-paper','software-paper')}).set_index('journal_id').loc['X']
        five=engine.build_counts(self.con,window_years=5,policies={'K':('article','review','data-paper','software-paper')}).set_index('journal_id').loc['X']
        self.assertEqual((two.d_raw,two.d_ref,two.c_raw,two.c_ref),(5,2,1,1))
        self.assertEqual((five.d_raw,five.d_ref,five.c_raw,five.c_ref),(7,3,3,2))
        self.assertEqual((five.c_self,five.c_ref_self),(3,2))
    def test_only_supported_publication_windows_are_accepted(self):
        for invalid in (0,3,True):
            with self.assertRaises(ValueError):engine.build_counts(self.con,window_years=invalid)
    def test_empty_and_collapsed_denominators_distinct_from_defined_zero(self):
        frame=pd.DataFrame([{'journal_id':i,'universe':'N','d_raw':a,'d_ref':b,'c_raw':0,'c_ref':0}
                            for i,a,b in [('empty',0,0),('collapse',2,0),('zero',2,1)]])
        s=build_scores(frame,retraction_modes=['all'])
        ref=s.loc[s.numerator_eligibility.eq('REF')&s.denominator_eligibility.eq('REF')].set_index('journal_id')
        self.assertEqual(ref.loc['empty','score_status'],'no_raw_citable_items')
        self.assertEqual(ref.loc['collapse','score_status'],'ref_denominator_collapse')
        self.assertEqual(ref.loc['zero','score'],0)
        self.assertTrue(pd.isna(ref.loc['empty','score']))
    def test_table_one_arithmetic(self):
        s=build_scores(pd.DataFrame([{'journal_id':'toy','universe':'N','d_raw':100,'d_ref':92,'c_raw':300,'c_ref':290}]),retraction_modes=['all'])
        values={(r.numerator_eligibility,r.denominator_eligibility):round(r.score,2) for r in s.itertuples()}
        self.assertEqual(values,{('RAW','RAW'):3.00,('REF','RAW'):2.90,('RAW','REF'):3.26,('REF','REF'):3.15})
    def test_field_named_mode_alphabetical_ties(self):
        f=field_modes(pd.DataFrame({'journal_id':['a','a','a','b'],'primary_field':['Zoology','Economics','Unknown','Unknown'],'works':[2,2,9,5]})).set_index('journal_id')
        self.assertEqual(f.loc['a','ojf_oa_field'],'Economics');self.assertEqual(f.loc['a','ojf_field_tied_modes'],2)
        self.assertEqual(f.loc['b','ojf_oa_field'],'Unknown')
    def test_twelve_month_maturity_retraction_selection(self):
        self.assertEqual(primary(2023),'all');self.assertEqual(primary(2024),'exclude_snapshot')

class PortableWorkflowTests(unittest.TestCase):
    def test_published_release_reads_both_window_manifests_and_prefixed_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);annual=root/'release';data=root/'data'
            annual.mkdir();data.mkdir()
            (data/'index.json').write_text(json.dumps({'years':[{'year':2025,'universes':{'n':'Norwegian Register','oa':'OpenAlex'}}]}))
            for window,prefix,value in [(2,'ojf',2.),(5,'ojf5',5.)]:
                source=annual/f'{prefix}_scores_2025.parquet'
                pd.DataFrame([{'openalex_id':'S1','title':'One','score_year':2025,'metric_window_years':window,
                    'oa_field':'Economics','reference_coverage_pct':80.,'active_years':None,
                    'in_n':True,'in_oa':True,'ojf_raw':value,'ojf_filtered':value}]).to_parquet(source,index=False)
                (annual/f'{prefix}_manifest.json').write_text(json.dumps({'years':[2025],'window_years':window,
                    'maturity':{'freeze_delay_months_after_citation_year_end':12},
                    'assets':{source.name:{'sha256':sha(source)}}}))
            install(annual,data,other_annual=annual)
            install(annual,data,window_years=5,other_annual=annual)
            for prefix in ['ojf','ojf5']:
                row=pd.read_parquet(data/prefix/'scores_2025.parquet').iloc[0]
                self.assertEqual(row.ojf2_filtered,2.)
                self.assertEqual(row.ojf5_filtered,5.)
    def test_five_year_only_history_rank_files_preserve_the_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);annual=root/'annual';data=root/'data'
            annual.mkdir();data.mkdir()
            source=annual/'scores_2024.parquet'
            pd.DataFrame([{'openalex_id':'S1','score_year':2024,'metric_window_years':5,
                'oa_field':'Economics','reference_coverage_pct':80.,'active_years':None,
                'in_n':True,'in_oa':True,'ojf_raw':3.,'ojf_filtered':2.}]).to_parquet(source,index=False)
            (annual/'manifest.json').write_text(json.dumps({'years':[2024],'window_years':5,
                'maturity':{'freeze_delay_months_after_citation_year_end':12},
                'assets':{source.name:{'sha256':sha(source)}}}))
            (data/'index.json').write_text(json.dumps({'years':[{'year':2024,'universes':{'n':'Norwegian Register','oa':'OpenAlex'}}]}))
            install(annual,data,window_years=5)
            row=pd.read_parquet(data/'ojf5/ranks/2024-n-ojf5-filtered.parquet').iloc[0]
            self.assertEqual(row.metric_window_years,5)
            self.assertEqual(row.ojf_filtered,2.)
    def test_other_ojf_column_does_not_borrow_counts_fields_or_missing_scores(self):
        native=pd.DataFrame([{'openalex_id':'S1','score_year':2025,'metric_window_years':2,
            'title':'One','in_n':True,'in_oa':True,'publications_raw':10,'publications_filtered':0,
            'citations_raw':7,'citations_filtered':0,'oa_field':'Medicine','reference_coverage_pct':0,
            'ojf_raw':.7,'ojf_filtered':None}])
        other=pd.DataFrame([{'openalex_id':'S1','score_year':2025,'metric_window_years':5,
            'title':'One','in_n':True,'in_oa':True,'publications_raw':100,'publications_filtered':50,
            'citations_raw':70,'citations_filtered':25,'oa_field':'Economics','reference_coverage_pct':50,
            'ojf_raw':.7,'ojf_filtered':.5},
            {'openalex_id':'S2','score_year':2025,'metric_window_years':5,'title':'Earlier output',
             'in_n':False,'in_oa':True,'publications_raw':2,'publications_filtered':2,
             'citations_raw':0,'citations_filtered':0,'oa_field':'Economics','reference_coverage_pct':100,
             'ojf_raw':0.,'ojf_filtered':0.}])
        result=_with_counterpart(native,other,2,2025).set_index('openalex_id')
        self.assertEqual(result.loc['S1','publications_raw'],10)
        self.assertEqual(result.loc['S1','oa_field'],'Medicine')
        self.assertTrue(pd.isna(result.loc['S1','ojf2_filtered']))
        self.assertEqual(result.loc['S1','ojf5_filtered'],.5)
        self.assertEqual(result.loc['S2','ojf5_filtered'],0.)
        self.assertTrue(pd.isna(result.loc['S2','publications_raw']))
        self.assertTrue(pd.isna(result.loc['S2','reference_coverage_pct']))
        self.assertEqual(result.loc['S2','oa_field'],'Unknown')
    def test_dictionary_uses_the_correct_window(self):
        self.assertIn('five years',ojf_description('metric_window_years',5))
        self.assertIn('t-5',ojf_description('publication_start_year',5))
        self.assertIn('t-2',ojf_description('publication_start_year',2))
    def test_raw_projection_then_annual_counting(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);q=sql_literal
            raw=root/'raw.parquet';works=root/'works.parquet'
            with duckdb.connect() as con:
                con.execute(f'''COPY (SELECT 'https://openalex.org/W'||i AS id,
                    {{'source':{{'id':CASE WHEN i=5 THEN 'https://openalex.org/S2' ELSE 'https://openalex.org/S1' END,
                       'issn_l':CASE WHEN i=5 THEN '2049-3630' ELSE '1234-5679' END,'type':'journal'}}}} AS primary_location,
                    CASE WHEN i=4 THEN 2024 ELSE 2023 END AS publication_year,'2023-01-01' AS publication_date,
                    CASE WHEN i=3 THEN 'editorial' ELSE 'article' END AS type,
                    CASE WHEN i=4 THEN ['https://openalex.org/W1','https://openalex.org/W2','https://openalex.org/W3'] ELSE [] END AS referenced_works,
                    CASE WHEN i IN (1,4) THEN 3 ELSE 0 END AS referenced_works_count,
                    '2026-09-23' AS updated_date,false AS is_paratext,false AS is_retracted,'' AS doi,
                    {{'field':{{'display_name':'Economics'}},'domain':{{'display_name':'Social Sciences'}}}} AS primary_topic
                    FROM range(1,6) r(i)) TO {q(raw)} (FORMAT PARQUET)''')
                con.execute(f'COPY ({checkpoint_sql([raw])}) TO {q(works)} (FORMAT PARQUET)')
            self.assertEqual(len(pd.read_parquet(works)),5)
            sources=pd.DataFrame([{'issn_l':'1234-5679','source_type':'journal','is_n':True,
                'openalex_id':'S1','title':'Fictional journal','publisher':'Fictional publisher','issns':'1234-5679',
                'oa_field':'Economics','oa_domain':'Social Sciences','is_open_access':True}])
            extra=sources.iloc[0].to_dict();extra.update(issn_l='2049-3630',openalex_id='S2',title='New recipient')
            sources=pd.concat([sources,pd.DataFrame([extra])],ignore_index=True)
            lookup=sources.copy();lookup['source_id']='https://openalex.org/'+lookup.openalex_id
            frames={'sources':sources,'source_lookup':lookup,
                'norwegian':pd.DataFrame([{'issn_l':'1234-5679','journal_id':'1','norwegian_level':1.,'field':'Economics','original_title':'Fictional journal'},
                    {'issn_l':'2049-3630','journal_id':'2','norwegian_level':2.,'field':'Economics','original_title':'New recipient'}]),
                'identity_source_overrides':pd.DataFrame(columns=['source_id','expected_raw_issn_l','target_issn_l'],dtype='string'),
                'identity_aliases':pd.DataFrame(columns=['from_issn_l','to_issn_l'],dtype='string')}
            for name,frame in frames.items():frame.to_parquet(root/f'{name}_2024.parquet',index=False)
            overrides=root/'overrides.parquet';exclusions=root/'exclusions.parquet'
            pd.DataFrame(columns=['work_id','expected_source_id','expected_raw_issn_l','target_source_id','target_raw_issn_l'],dtype='string').to_parquet(overrides,index=False)
            pd.DataFrame(columns=['work_id','expected_source_id','expected_raw_issn_l'],dtype='string').to_parquet(exclusions,index=False)
            website=sources.iloc[:1].drop(columns=['source_type','is_n']).copy()
            website['norwegian_level']='1';website['norwegian_primary_id']='1';website['norwegian_entries_json']='[]'
            wp=root/'nfans.parquet';website.to_parquet(wp,index=False)
            output=annual(2024,works,frames,root,overrides,exclusions,wp,root/'output',memory='1GB',threads=1)
            self.assertEqual(output.set_index('openalex_id').loc['S2','norwegian_level'],'2')
            result=output.iloc[0]
            self.assertEqual((result.d_raw,result.d_ref,result.c_raw,result.c_ref),(2,1,3,2))
            self.assertEqual((result.ojf_raw_raw,result.ojf_ref_ref),(1.5,2))
            self.assertEqual(result.freeze_eligible_from,'2026-01-01')

if __name__=='__main__':unittest.main()
