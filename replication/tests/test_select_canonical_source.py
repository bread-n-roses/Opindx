"""A reviewed display arm retains corrected Work mass and fails closed."""

import sys
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from identity import AnnualIdentityMaps, SourceKeyOverride, project_endpoints  # noqa: E402
from select_canonical_source import ReviewedRepresentative, select_canonical_sources  # noqa: E402
from sources import journal_export_view, project_sources  # noqa: E402


YEAR = 2023
BAD = "26986752"
KEY = "2198-4298"
WRONG = "1234-5679"
CORRECTED = "https://openalex.org/S7407053284"
CANONICAL = "https://openalex.org/S7407061930"
EVIDENCE = "Reviewed the correction ledger, both September Source rows, and the closed component."


def source(source_id: str, key: str, title: str) -> dict:
    return {"source_id": source_id, "raw_issn_l": key, "source_type": "journal",
            "title": title, "publisher": "Publisher", "issns": (KEY,),
            "oa_field": "Economics", "oa_domain": "Social Sciences",
            "is_open_access": False, "updated_date": "2026-09-01"}


def mapping() -> AnnualIdentityMaps:
    return AnnualIdentityMaps(
        YEAR, {CORRECTED: SourceKeyOverride((BAD,), KEY)}, {}, {}, "approved_for_score_year",
    )


def decision(**changes) -> ReviewedRepresentative:
    values = {"score_year": YEAR, "source_id": CORRECTED,
              "canonical_source_id": CANONICAL, "issn_l": KEY,
              "expected_component_source_ids": tuple(sorted((CORRECTED, CANONICAL))),
              "review_status": "approved_for_score_year", "evidence": EVIDENCE}
    values.update(changes)
    return ReviewedRepresentative(**values)


class CanonicalSourceSelectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.latest = pd.DataFrame([
            source(CORRECTED, BAD, "ZGD"),
            source(CANONICAL, KEY, "Zeitschrift für Geographiedidaktik"),
        ])
        self.maps = mapping()
        self.effective, self.provenance, self.source_audit = project_sources(YEAR, self.latest, self.maps)

    def test_documented_duplicate_keeps_mass_and_uses_existing_canonical_source(self) -> None:
        self.assertEqual(self.effective.iloc[0].canonical_arm_rows, 2)
        with self.assertRaisesRegex(ValueError, "exactly one canonical"):
            journal_export_view(self.effective)
        before_provenance = self.provenance.copy(deep=True)
        selected, audit, selection = select_canonical_sources(
            YEAR, self.effective, self.provenance, self.source_audit, self.maps, (decision(),),
        )
        pd.testing.assert_frame_equal(self.provenance, before_provenance)
        self.assertEqual(selected.iloc[0].source_ids, tuple(sorted((CORRECTED, CANONICAL))))
        self.assertEqual(selected.iloc[0].canonical_arm_rows, 1)
        self.assertEqual(selected.iloc[0].openalex_id, "S7407061930")
        self.assertEqual(selected.iloc[0].journal_title, "Zeitschrift für Geographiedidaktik")
        self.assertEqual(journal_export_view(selected).iloc[0].openalex_id, "S7407061930")
        self.assertEqual(audit["multiple_canonical_arm_nodes"], 0)
        self.assertEqual(len(selection["decisions_applied"]), 1)
        self.assertFalse(selection["provenance_changed"])
        raw_counts = pd.DataFrame({"source_id": [CORRECTED], "raw_issn_l": [BAD],
                                   "a_raw": [5], "a_filtered": [0]})
        raw_edges = pd.DataFrame(columns=["citing_source_id", "raw_citing_issn_l",
                                          "cited_source_id", "raw_cited_issn_l", "n_raw", "n_filtered"])
        counts, _, _ = project_endpoints(YEAR, raw_counts, raw_edges, self.maps)
        self.assertEqual(counts.iloc[0].issn_l, KEY)
        self.assertEqual(counts.iloc[0].a_raw, 5)

    def test_fails_if_component_id_set_changes(self) -> None:
        with self.assertRaisesRegex(ValueError, "component IDs differ"):
            select_canonical_sources(YEAR, self.effective, self.provenance, self.source_audit,
                                     self.maps, (decision(expected_component_source_ids=tuple(sorted((CORRECTED, CANONICAL, "S3")))),))
        extra = pd.concat([self.latest, pd.DataFrame([source("S3", KEY, "Other")])], ignore_index=True)
        effective, provenance, audit = project_sources(YEAR, extra, self.maps)
        with self.assertRaisesRegex(ValueError, "component IDs differ"):
            select_canonical_sources(YEAR, effective, provenance, audit, self.maps, (decision(),))

    def test_fails_if_selected_id_or_key_is_absent_or_mismatched(self) -> None:
        with self.assertRaisesRegex(ValueError, "component IDs differ"):
            select_canonical_sources(YEAR, self.effective, self.provenance, self.source_audit,
                                     self.maps, (decision(canonical_source_id="S999",
                                                           expected_component_source_ids=tuple(sorted((CORRECTED, "S999")))),))
        changed = self.latest.copy()
        changed.loc[1, "raw_issn_l"] = WRONG
        effective, provenance, audit = project_sources(YEAR, changed, self.maps)
        with self.assertRaisesRegex(ValueError, "component IDs differ"):
            select_canonical_sources(YEAR, effective, provenance, audit, self.maps, (decision(),))
        with self.assertRaisesRegex(ValueError, "matching source-specific"):
            select_canonical_sources(YEAR, self.effective, self.provenance, self.source_audit,
                                     AnnualIdentityMaps(YEAR, {}, {}, {}, "approved_for_score_year"), (decision(),))

    def test_unreviewed_decision_and_single_source_typo_behavior(self) -> None:
        with self.assertRaisesRegex(ValueError, "approved, closed"):
            select_canonical_sources(YEAR, self.effective, self.provenance, self.source_audit,
                                     self.maps, (decision(review_status="pending_annual_review"),))
        with self.assertRaisesRegex(ValueError, "approved, closed"):
            select_canonical_sources(YEAR, self.effective, self.provenance, self.source_audit,
                                     self.maps, (decision(evidence=""),))
        single_latest = pd.DataFrame([source(CORRECTED, BAD, "Single typo")])
        effective, provenance, audit = project_sources(YEAR, single_latest, self.maps)
        kept, kept_audit, selection = select_canonical_sources(YEAR, effective, provenance, audit, self.maps, ())
        self.assertIs(kept, effective)
        self.assertIs(kept_audit, audit)
        self.assertEqual(journal_export_view(kept).iloc[0].openalex_id, "S7407053284")
        self.assertEqual(selection["decisions_applied"], [])


if __name__ == "__main__":
    unittest.main()
