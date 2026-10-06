import unittest
import pandas as pd
from opindx_replication.metric_counts import from_effective_graph

class MetricCountsTests(unittest.TestCase):
    def test_membership_self_citations_zero_and_recorded_counts(self):
        frame=pd.DataFrame({'issn_l':['A','B','C'],'in_n':[True,True,False],'in_oa':[True,True,True],
            'citations_raw':[12,0,0],'citations_filtered':[6,0,0], 'per_article_n_filtered':[2.0,0.0,None]})
        # X lies outside both universes; C lies outside N; A->A is a self-citation.
        edges=pd.DataFrame({'citing_issn_l':['B','C','X','A'],'cited_issn_l':['A']*4,
            'n_raw':[3,4,5,9],'n_filtered':[2,1,3,7]})
        scores=pd.DataFrame({'universe':['N','N','OA','OA','OA'],'issn_l':['A','B','A','B','C']})
        out=from_effective_graph(frame,edges,scores).set_index('issn_l')
        self.assertEqual(out.loc['A','citations_n_raw'],3)
        self.assertEqual(out.loc['A','citations_n_filtered'],2)
        self.assertEqual(out.loc['A','citations_oa_raw'],7)
        self.assertEqual(out.loc['A','citations_oa_filtered'],3)
        self.assertEqual(out.loc['B','citations_n_filtered'],0)
        self.assertTrue(pd.isna(out.loc['C','citations_n_filtered']))
        pd.testing.assert_frame_equal(out.reset_index()[frame.columns],frame)

if __name__=='__main__':unittest.main()
