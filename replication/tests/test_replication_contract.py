import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from opindx_replication import pipeline
from opindx_replication.datasets import description

class ReplicationContract(unittest.TestCase):
    def test_pinned_annual_decisions_validate_for_every_published_year(self):
        for year in range(2022,2027):
            maps,matches,reps=pipeline.decisions(year)
            maps.validate(year)
            self.assertTrue(matches)
            self.assertTrue(reps)
            self.assertEqual(len({m.journal_id for m in matches}),len(matches))

    def test_changed_correction_bundle_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'corrections').mkdir();(root/'configs').mkdir()
            config=pipeline.profile()
            (root/'configs/september-2026.json').write_text(json.dumps(config))
            original=json.loads((pipeline.PACKAGE/'corrections/annual-2022.json').read_text(encoding='utf-8'))
            original['identity_map']['alias_to_canonical']={}
            (root/'corrections/annual-2022.json').write_text(json.dumps(original))
            with patch.object(pipeline,'PACKAGE',root):
                with self.assertRaisesRegex(ValueError,'decisions changed'):pipeline.decisions(2022)

    def test_metric_names_and_units_are_explicit(self):
        self.assertIn('NF (Network Factor)',description('share_oa_filtered'))
        self.assertIn('ANS (Article Network Score)',description('per_article_n_raw'))
        self.assertIn('0-1',description('oa_field_modal_share'))
        self.assertIn('0-100',description('reference_coverage_pct'))
        with self.assertRaises(ValueError):description('commercial_benchmark_score')

if __name__=='__main__':unittest.main()
