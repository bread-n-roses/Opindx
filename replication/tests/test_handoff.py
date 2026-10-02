"""Synthetic contract tests for the September Opindx CSV handoff."""

import json
import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from handoff import (  # noqa: E402
    DEFAULT_YEARS,
    EXPORT_COLUMNS,
    OPINDX_REQUIRED_COLUMNS,
    SCORE_COLUMNS,
    validate_handoff_frame,
)


def synthetic_handoff() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Include an N-only native identity absent from the OA-journal export."""
    rows = []
    members = []
    native = []
    for year in DEFAULT_YEARS:
        for key in ("1111-1111", "9999-9999"):
            members.append({"score_year": year, "issn_l": key})
        for universe, identities in (
            ("N", (("1111-1111", 60.0), ("9999-9999", 40.0))),
            ("OA", (("1111-1111", 100.0), ("2222-2222", 0.0))),
        ):
            for treatment in ("Raw", "Filtered"):
                for key, ef in identities:
                    native.append({"score_year": year, "universe": universe,
                                   "treatment": treatment, "issn_l": key, "ef": ef})
        for openalex_id, issn_l, is_n in (
            ("S100000", "1111-1111", True),
            ("S200000", "2222-2222", False),
        ):
            a_raw, a_filtered = (10, 8) if is_n else (5, 0)
            row = {column: pd.NA for column in EXPORT_COLUMNS}
            row.update({
                "openalex_id": openalex_id,
                "openalex_url": f"https://openalex.org/{openalex_id}",
                "journal_title": "Journal A" if is_n else "Journal B",
                "issn_l": issn_l,
                "issns": issn_l,
                "publisher": "Synthetic Publisher",
                "oa_domain": "Physical Sciences",
                "oa_field": "Physics and Astronomy",
                "norwegian_area": "Science" if is_n else pd.NA,
                "norwegian_field": "Physics" if is_n else pd.NA,
                "norwegian_level": "1" if is_n else pd.NA,
                "is_open_access": False,
                "score_year": year,
                "publication_window_start": year - 5,
                "publication_window_end": year - 1,
                "eligible_publications_raw": a_raw,
                "eligible_publications_filtered": a_filtered,
                "incoming_citations_raw": 7 if is_n else 0,
                "incoming_citations_filtered": 5 if is_n else 0,
                "reference_coverage_pct": 80.0 if is_n else 0.0,
                "active_publication_years_of_5": 5 if is_n else 2,
                "ef_oa_raw": 100.0 if is_n else 0.0,
                "ais_oa_raw": 1.0 if is_n else 0.0,
                "ef_oa_filtered": 100.0 if is_n else 0.0,
                "ais_oa_filtered": 1.0 if is_n else pd.NA,
                "citation_scope": "score_year_full_cached_source_graph_excluding_journal_self_citations",
                "source_metadata_updated": "2026-09-23",
                "oa_snapshot_version": "2026-09-23",
                "norwegian_register_snapshot": "2026-07-26",
                "field_assignment_scope": "fixed_2024_oa_classification;annual_norwegian_roster_metadata",
                "sample_scope": "oa_journals_with_any_nonmissing_n_or_oa_ef_or_ais_in_score_year",
                "data_source": "synthetic",
                "norwegian_register_url": "https://kanalregister.hkdir.no/tidsskrift?id=123" if is_n else "",
            })
            if is_n:
                row.update({"ef_n_raw": 60.0, "ais_n_raw": 1.0,
                            "ef_n_filtered": 60.0, "ais_n_filtered": 1.0})
            rows.append(row)
    return (pd.DataFrame(rows, columns=EXPORT_COLUMNS),
            pd.DataFrame(members), pd.DataFrame(native))


class HandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.frame, self.members, self.native = synthetic_handoff()

    def validate(self, frame=None, *, members=None, native=None):
        return validate_handoff_frame(
            self.frame if frame is None else frame,
            annual_n_members=self.members if members is None else members,
            expected_norwegian_snapshot="2026-07-26",
            native_full_scores=self.native if native is None else native,
        )

    def test_valid_contract_preserves_zero_and_native_n_only_mass(self) -> None:
        self.assertEqual(len(EXPORT_COLUMNS), 37)
        self.assertEqual(len(OPINDX_REQUIRED_COLUMNS), 29)
        self.assertTrue(set(OPINDX_REQUIRED_COLUMNS) <= set(EXPORT_COLUMNS))
        audit = self.validate()
        self.assertEqual(audit["rows"], 10)
        self.assertEqual(audit["years"], list(DEFAULT_YEARS))
        self.assertEqual(audit["zero_scores"]["ef_oa_raw"], 5)
        self.assertEqual(audit["defined_scores"]["ef_oa_filtered"], 10)
        self.assertEqual(audit["defined_scores"]["ais_oa_filtered"], 5)
        self.assertEqual(audit["native_full_state_ef_sums"]["2022:N:Raw"], 100.0)
        self.assertEqual(self.frame.loc[self.frame.score_year.eq(2022), "ef_n_raw"].sum(), 60.0)

    def test_export_columns_match_preserved_june_audit_exactly(self) -> None:
        baseline = Path(__file__).resolve().parent / "fixtures" / "handoff_schema.json"
        expected = json.loads(baseline.read_text(encoding="utf-8"))["columns"]
        self.assertEqual(list(EXPORT_COLUMNS), expected)

    def test_duplicate_identity_and_all_missing_score_row_fail(self) -> None:
        duplicate = pd.concat([self.frame, self.frame.iloc[[0]]], ignore_index=True)
        with self.assertRaisesRegex(ValueError, "duplicate \\(openalex_id, score_year\\)"):
            self.validate(duplicate)
        all_missing = self.frame.copy()
        all_missing.loc[0, list(SCORE_COLUMNS)] = pd.NA
        with self.assertRaisesRegex(ValueError, "all eight score values are missing"):
            self.validate(all_missing)

    def test_year_window_and_snapshot_fail(self) -> None:
        wrong_year = self.frame.copy()
        wrong_year.loc[0, "score_year"] = 2021
        with self.assertRaisesRegex(ValueError, "score years must be exactly"):
            self.validate(wrong_year)
        wrong_window = self.frame.copy()
        wrong_window.loc[0, "publication_window_start"] = 2018
        with self.assertRaisesRegex(ValueError, "publication window"):
            self.validate(wrong_window)
        wrong_snapshot = self.frame.copy()
        wrong_snapshot.loc[0, "oa_snapshot_version"] = "2026-06-26"
        with self.assertRaisesRegex(ValueError, "one snapshot"):
            self.validate(wrong_snapshot)

    def test_membership_requires_annual_url_and_no_nonmember_n_score(self) -> None:
        missing_url = self.frame.copy()
        missing_url.loc[0, "norwegian_register_url"] = ""
        with self.assertRaisesRegex(ValueError, "membership and nonempty register URL"):
            self.validate(missing_url)
        false_n_score = self.frame.copy()
        false_n_score.loc[1, "ef_n_raw"] = 0.0
        false_n_score.loc[1, "ais_n_raw"] = 0.0
        with self.assertRaisesRegex(ValueError, "nonmembers have Norwegian-universe scores"):
            self.validate(false_n_score)
        multiple_links = self.frame.copy()
        multiple_links.loc[0, "norwegian_register_url"] += "; https://kanalregister.hkdir.no/tidsskrift?id=456"
        self.validate(multiple_links)

    def test_score_values_and_ais_definition_fail(self) -> None:
        negative = self.frame.copy()
        negative.loc[0, "ef_oa_raw"] = -1.0
        with self.assertRaisesRegex(ValueError, "negative scores"):
            self.validate(negative)
        infinite = self.frame.copy()
        infinite.loc[0, "ef_oa_raw"] = float("inf")
        with self.assertRaisesRegex(ValueError, "nonfinite"):
            self.validate(infinite)
        text_missing = self.frame.copy()
        text_missing["ef_oa_raw"] = text_missing["ef_oa_raw"].astype(object)
        text_missing.loc[0, "ef_oa_raw"] = ""
        with self.assertRaisesRegex(ValueError, "masquerading as missing"):
            self.validate(text_missing)
        ais_at_zero_articles = self.frame.copy()
        ais_at_zero_articles.loc[1, "ais_oa_filtered"] = 0.0
        with self.assertRaisesRegex(ValueError, "AIS defined with zero articles"):
            self.validate(ais_at_zero_articles)

    def test_native_normalization_uses_full_state_only(self) -> None:
        without_native = validate_handoff_frame(self.frame, annual_n_members=self.members)
        self.assertIsNone(without_native["native_full_state_ef_sums"])
        broken_native = self.native.copy()
        mask = broken_native.score_year.eq(2022) & broken_native.universe.eq("N") & broken_native.treatment.eq("Raw") & broken_native.issn_l.eq("9999-9999")
        broken_native.loc[mask, "ef"] = 39.0
        with self.assertRaisesRegex(ValueError, "EF does not sum to 100"):
            self.validate(native=broken_native)

    def test_schema_is_exact_and_no_extra_columns_leak(self) -> None:
        extra = self.frame.assign(commercial_metric=1.0)
        with self.assertRaisesRegex(ValueError, "extra=.*commercial_metric"):
            self.validate(extra)
        missing = self.frame.drop(columns="oa_field")
        with self.assertRaisesRegex(ValueError, "missing=.*oa_field"):
            self.validate(missing)


if __name__ == "__main__":
    unittest.main()
