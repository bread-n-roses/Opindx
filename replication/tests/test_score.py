"""Small graph checks for the website scorer's state and missing-value rules."""

import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from score import score_annual  # noqa: E402


class WebsiteScoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.counts = pd.DataFrame({
            "issn_l": ["A", "B", "C"],
            "a_raw": [10, 5, 2],
            "a_filtered": [8, 3, 0],
        })
        self.edges = pd.DataFrame({
            "citing_issn_l": ["A", "B", "C"],
            "cited_issn_l": ["B", "A", "C"],
            "n_raw": [4, 1, 3],
            "n_filtered": [2, 1, 0],
        })
        self.sources = pd.DataFrame({"issn_l": ["A", "B", "C"], "source_type": ["journal"] * 3})

    def test_raw_isolated_zero_and_filtered_undefined_are_distinct(self) -> None:
        scores, checks = score_annual(2022, self.counts, self.edges, self.sources, {"A", "B"})
        self.assertEqual(set(scores["universe"]), {"N", "OA"})
        self.assertEqual(len(scores), 10)
        oa_raw_c = scores.loc[
            (scores.universe == "OA") & (scores.treatment == "Raw") & (scores.issn_l == "C")
        ].iloc[0]
        oa_filtered_c = scores.loc[
            (scores.universe == "OA") & (scores.treatment == "Filtered") & (scores.issn_l == "C")
        ].iloc[0]
        self.assertEqual(oa_raw_c.ef, 0.0)
        self.assertEqual(oa_raw_c.ais, 0.0)
        self.assertFalse(pd.isna(oa_raw_c.ais))
        self.assertEqual(oa_filtered_c.ef, 0.0)
        self.assertTrue(pd.isna(oa_filtered_c.ais))
        self.assertEqual(checks.groupby("universe").state_identity_sha256.nunique().to_dict(), {"N": 1, "OA": 1})
        self.assertTrue((checks.ef_sum - 100.0).abs().lt(1e-8).all())

    def test_fails_if_a_filtered_edge_exceeds_raw(self) -> None:
        edges = self.edges.copy()
        edges.loc[0, "n_filtered"] = 5
        with self.assertRaisesRegex(ValueError, "exceed Raw"):
            score_annual(2022, self.counts, edges, self.sources, {"A", "B"})

    def test_requires_effective_endpoint_keys(self) -> None:
        edges = self.edges.rename(columns={"citing_issn_l": "raw_citing_issn_l"})
        with self.assertRaisesRegex(ValueError, "effective endpoint keys"):
            score_annual(2022, self.counts, edges, self.sources, {"A", "B"})


if __name__ == "__main__":
    unittest.main()
