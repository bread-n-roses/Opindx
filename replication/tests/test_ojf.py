"""Independent open-count fixtures: windows, filtering, zeros and source scope."""
import unittest
import duckdb
import pandas as pd
import tempfile
from pathlib import Path
from opindx_replication.ojf import engine
from opindx_replication.ojf.counting_base import build_scores
from opindx_replication.ojf.assembly import field_modes,primary
from opindx_replication.ojf.pipeline import annual
from opindx_replication.ojf.checkpoint import checkpoint_sql,sql_literal

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
    def test_raw_projection_then_annual_counting(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);q=sql_literal
            raw=root/'raw.parquet';works=root/'works.parquet'
            with duckdb.connect() as con:
                con.execute(f'''COPY (SELECT 'https://openalex.org/W'||i AS id,
                    {{'source':{{'id':'https://openalex.org/S1','issn_l':'1234-5679','type':'journal'}}}} AS primary_location,
                    CASE WHEN i=4 THEN 2024 ELSE 2023 END AS publication_year,'2023-01-01' AS publication_date,
                    CASE WHEN i=3 THEN 'editorial' ELSE 'article' END AS type,
                    CASE WHEN i=4 THEN ['https://openalex.org/W1','https://openalex.org/W2','https://openalex.org/W3'] ELSE [] END AS referenced_works,
                    CASE WHEN i IN (1,4) THEN 3 ELSE 0 END AS referenced_works_count,
                    '2026-09-23' AS updated_date,false AS is_paratext,false AS is_retracted,'' AS doi,
                    {{'field':{{'display_name':'Economics'}},'domain':{{'display_name':'Social Sciences'}}}} AS primary_topic
                    FROM range(1,5) r(i)) TO {q(raw)} (FORMAT PARQUET)''')
                con.execute(f'COPY ({checkpoint_sql([raw])}) TO {q(works)} (FORMAT PARQUET)')
            self.assertEqual(len(pd.read_parquet(works)),4)
            sources=pd.DataFrame([{'issn_l':'1234-5679','source_type':'journal','is_n':True,
                'openalex_id':'S1','title':'Fictional journal','publisher':'Fictional publisher','issns':'1234-5679',
                'oa_field':'Economics','oa_domain':'Social Sciences','is_open_access':True}])
            lookup=sources.copy();lookup['source_id']='https://openalex.org/S1'
            frames={'sources':sources,'source_lookup':lookup,
                'norwegian':pd.DataFrame([{'issn_l':'1234-5679','journal_id':'1','norwegian_level':1,'field':'Economics','original_title':'Fictional journal'}]),
                'identity_source_overrides':pd.DataFrame(columns=['source_id','expected_raw_issn_l','target_issn_l'],dtype='string'),
                'identity_aliases':pd.DataFrame(columns=['from_issn_l','to_issn_l'],dtype='string')}
            for name,frame in frames.items():frame.to_parquet(root/f'{name}_2024.parquet',index=False)
            overrides=root/'overrides.parquet';exclusions=root/'exclusions.parquet'
            pd.DataFrame(columns=['work_id','expected_source_id','expected_raw_issn_l','target_source_id','target_raw_issn_l'],dtype='string').to_parquet(overrides,index=False)
            pd.DataFrame(columns=['work_id','expected_source_id','expected_raw_issn_l'],dtype='string').to_parquet(exclusions,index=False)
            website=sources.drop(columns=['source_type','is_n']).copy()
            website['norwegian_level']='1';website['norwegian_primary_id']='1';website['norwegian_entries_json']='[]'
            wp=root/'nfans.parquet';website.to_parquet(wp,index=False)
            result=annual(2024,works,frames,root,overrides,exclusions,wp,root/'output',memory='1GB',threads=1).iloc[0]
            self.assertEqual((result.d_raw,result.d_ref,result.c_raw,result.c_ref),(2,1,3,2))
            self.assertEqual((result.ojf_raw_raw,result.ojf_ref_ref),(1.5,2))
            self.assertEqual(result.freeze_eligible_from,'2026-01-01')

if __name__=='__main__':unittest.main()
