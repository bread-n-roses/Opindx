"""Identity projection must preserve mass and expose merged self-citations."""

import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from identity import AnnualIdentityMaps, SourceKeyOverride, project_endpoints, project_retention  # noqa: E402


class IdentityProjectionTests(unittest.TestCase):
    def test_two_arms_merge_and_new_self_edge(self) -> None:
        counts = pd.DataFrame({
            "source_id": ["S1", "S2"],
            "raw_issn_l": ["0000-0000", "1234-5679"],
            "a_raw": [3, 2], "a_filtered": [2, 1],
        })
        edges = pd.DataFrame({
            "citing_source_id": ["S1"], "raw_citing_issn_l": ["0000-0000"],
            "cited_source_id": ["S2"], "raw_cited_issn_l": ["1234-5679"],
            "n_raw": [4], "n_filtered": [2],
        })
        maps = AnnualIdentityMaps(
            2022, {}, {"0000-0000": "1234-5679"}, {}, "approved_for_score_year"
        )
        projected_counts, projected_edges, audit = project_endpoints(2022, counts, edges, maps)
        self.assertEqual(projected_counts.iloc[0].a_raw, 5)
        self.assertEqual(projected_counts.iloc[0].a_filtered, 3)
        self.assertEqual(projected_edges.iloc[0].n_raw, 4)
        self.assertEqual(audit["effective_self_n_raw"], 4)
        self.assertEqual(audit["quarantined_n_raw"], 0)

    def test_rejects_unreviewed_year_map(self) -> None:
        maps = AnnualIdentityMaps(2024, {}, {}, {}, "approved_for_score_year")
        with self.assertRaisesRegex(ValueError, "reviewed annual"):
            maps.validate(2026)

    def test_blank_generic_keys_are_rejected_in_both_map_families(self) -> None:
        for family in ("alias_to_canonical", "key_correction"):
            for blank in ("", " ", "\t"):
                mappings = {"alias_to_canonical": {}, "key_correction": {}}
                mappings[family] = {blank: "1234-5679"}
                maps = AnnualIdentityMaps(2022, {}, **mappings, review_status="approved_for_score_year")
                with self.subTest(family=family, blank=blank), self.assertRaisesRegex(ValueError, "blank generic keys"):
                    maps.validate(2022)

    def test_blank_preimage_only_corrects_its_reviewed_source_id(self) -> None:
        counts = pd.DataFrame({"source_id": ["S1", "S2"], "raw_issn_l": [None, "1234-5679"],
                               "a_raw": [3, 2], "a_filtered": [2, 1]})
        edges = pd.DataFrame(columns=["citing_source_id", "raw_citing_issn_l", "cited_source_id",
                                      "raw_cited_issn_l", "n_raw", "n_filtered"])
        maps = AnnualIdentityMaps(2022, {"S1": SourceKeyOverride(("",), "2049-3630")},
                                  {}, {}, "approved_for_score_year")
        projected, _, audit = project_endpoints(2022, counts, edges, maps)
        self.assertEqual(projected.set_index("issn_l").a_raw.to_dict(), {"1234-5679": 2, "2049-3630": 3})
        self.assertEqual(audit["quarantined_a_raw"], 0)
        counts.loc[1, "raw_issn_l"] = None
        with self.assertRaisesRegex(ValueError, "Positive article or citation mass was quarantined"):
            project_endpoints(2022, counts, edges, maps)

    def test_positive_quarantine_stops_projection(self) -> None:
        counts = pd.DataFrame({
            "source_id": ["S1", "S2"], "raw_issn_l": ["1234-5679", "BAD"],
            "a_raw": ["10", "2"], "a_filtered": ["2", "1"],
        })
        edges = pd.DataFrame({
            "citing_source_id": ["S1"], "raw_citing_issn_l": ["1234-5679"],
            "cited_source_id": ["S2"], "raw_cited_issn_l": ["BAD"],
            "n_raw": ["4"], "n_filtered": ["1"],
        })
        maps = AnnualIdentityMaps(2022, {}, {}, {}, "approved_for_score_year")
        with self.assertRaisesRegex(ValueError, "Positive article or citation mass was quarantined"):
            project_endpoints(2022, counts, edges, maps)

    def test_source_override_requires_the_reviewed_raw_preimage(self) -> None:
        counts = pd.DataFrame({
            "source_id": ["S1"], "raw_issn_l": ["0000-0000"],
            "a_raw": [3], "a_filtered": [2],
        })
        edges = pd.DataFrame({
            "citing_source_id": ["S1"], "raw_citing_issn_l": ["0000-0000"],
            "cited_source_id": ["S1"], "raw_cited_issn_l": ["0000-0000"],
            "n_raw": [1], "n_filtered": [1],
        })
        approved = SourceKeyOverride(("0000-0000", "1234-5679"), "1234-5679")
        maps = AnnualIdentityMaps(2022, {"S1": approved}, {}, {}, "approved_for_score_year")
        projected, _, _ = project_endpoints(2022, counts, edges, maps)
        self.assertEqual(projected.iloc[0].issn_l, "1234-5679")
        counts.loc[0, "raw_issn_l"] = "2049-3630"
        with self.assertRaisesRegex(ValueError, "preimage changed"):
            project_endpoints(2022, counts, edges, maps)
        counts.loc[0, "raw_issn_l"] = "1234-5679"
        projected, _, _ = project_endpoints(2022, counts, edges, maps)
        self.assertEqual(projected.iloc[0].issn_l, "1234-5679")

    def test_annual_retention_uses_the_same_map_and_rejects_lost_mass(self) -> None:
        raw = pd.DataFrame({
            "source_id": ["S1", "S2"], "raw_issn_l": ["0000-0000", "1234-5679"],
            "year": [2020, 2020], "a_raw": [2, 3], "a_filtered": [1, 2],
        })
        maps = AnnualIdentityMaps(2022, {}, {"0000-0000": "1234-5679"}, {}, "approved_for_score_year")
        projected, report = project_retention(2022, raw, maps)
        self.assertEqual(len(projected), 1)
        self.assertEqual((projected.iloc[0].a_raw, projected.iloc[0].a_filtered), (5, 3))
        self.assertEqual(report["quarantined_a_raw"], 0)
        raw.loc[1, "raw_issn_l"] = "BAD"
        with self.assertRaisesRegex(ValueError, "Positive annual article mass was quarantined"):
            project_retention(2022, raw, maps)


if __name__ == "__main__":
    unittest.main()
