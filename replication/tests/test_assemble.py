"""Tiny annual source-to-score-to-website handoff integration check."""

import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assemble import assemble_annual  # noqa: E402
from handoff import EXPORT_COLUMNS, validate_handoff_frame  # noqa: E402
from identity import AnnualIdentityMaps  # noqa: E402
from match_norwegian import SourceAlias, group_memberships, match_memberships  # noqa: E402
from norwegian import NorwegianMembership  # noqa: E402
from score import score_annual  # noqa: E402
from sources import project_sources  # noqa: E402


A, B, C = "1234-5679", "2049-3630", "0378-5955"


class AssembleTests(unittest.TestCase):
    def setUp(self) -> None:
        latest = pd.DataFrame([
            {"source_id": f"S{index}", "raw_issn_l": key, "source_type": "journal",
             "title": f"Journal {index}", "publisher": "Publisher", "issns": (key,),
             "oa_field": "Economics", "oa_domain": "Social Sciences",
             "is_open_access": False, "updated_date": "2026-09-01 13:14:15"}
            for index, key in ((1, A), (2, B), (3, C))
        ])
        maps = AnnualIdentityMaps(2022, {}, {}, {}, "approved_for_score_year")
        self.sources, _, _ = project_sources(2022, latest, maps)
        self.counts = pd.DataFrame({
            "issn_l": [A, B, C], "a_raw": [3, 2, 2], "a_filtered": [2, 1, 0],
        })
        self.edges = pd.DataFrame({
            "citing_issn_l": [A, B, C, A], "cited_issn_l": [B, A, C, A],
            "n_raw": [2, 1, 3, 1], "n_filtered": [1, 1, 0, 1],
        })
        self.retention = pd.DataFrame({
            "issn_l": [A, A, B, C], "year": [2020, 2021, 2021, 2021],
            "a_raw": [1, 2, 2, 2], "a_filtered": [1, 1, 1, 0],
        })
        members = [
            NorwegianMembership(2022, str(index + 6), 1, key, "", f"Journal {index}",
                                f"Journal {index}", "Social Sciences", "Economics", "0", None)
            for index, key in ((1, A), (2, B))
        ]
        resolved = match_memberships(members, [
            SourceAlias(2022, "S1", A, (A,)), SourceAlias(2022, "S2", B, (B,)),
        ])
        self.assertFalse(resolved.review_queue)
        self.norwegian = group_memberships(resolved.resolved)
        self.scores, _ = score_annual(2022, self.counts, self.edges, self.sources, {A, B})

    def assemble(self, **changes):
        arguments = dict(
            sources=self.sources, counts=self.counts, edges=self.edges,
            retention=self.retention, scores=self.scores, norwegian=self.norwegian,
            oa_snapshot_version="2026-09-23", norwegian_register_snapshot="2026-07-26",
        )
        arguments.update(changes)
        return assemble_annual(2022, **arguments)

    def test_canonical_date_self_exclusion_and_handoff_contract(self) -> None:
        frame = self.assemble()
        self.assertEqual(tuple(frame.columns), EXPORT_COLUMNS)
        self.assertEqual(len(frame), 3)
        by_key = frame.set_index("issn_l")
        self.assertEqual(by_key.loc[A, "source_metadata_updated"], "2026-09-01")
        self.assertEqual(by_key.loc[A, "incoming_citations_raw"], 1)
        self.assertEqual(by_key.loc[A, "active_publication_years_of_5"], 2)
        self.assertEqual(by_key.loc[A, "norwegian_register_url"],
                         "https://kanalregister.hkdir.no/tidsskrift?id=7")
        self.assertEqual(by_key.loc[C, "norwegian_register_url"], "")
        audit = validate_handoff_frame(
            frame, annual_n_members=pd.DataFrame({"score_year": [2022, 2022], "issn_l": [A, B]}),
            expected_years=(2022,), expected_norwegian_snapshot="2026-07-26",
            native_full_scores=self.scores,
        )
        self.assertEqual(audit["rows"], 3)

    def test_retention_must_reconcile_to_annual_counts(self) -> None:
        retention = self.retention.copy()
        retention.loc[0, "a_raw"] += 1
        with self.assertRaisesRegex(ValueError, "does not reconcile"):
            self.assemble(retention=retention)


if __name__ == "__main__":
    unittest.main()
