"""JGR content repair must preserve publisher sections and reject drift."""

import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from source_repairs_jgr import (  # noqa: E402
    ATMOSPHERES_ISSN_L, MAG_SOURCE_NAME, PARENT_ISSN_L,
    PUBLISHER_SOURCE_NAME, SOURCE_ID, build_exclusions, build_ledger,
    repair_latest_sources,
)


class JgrSourceRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.metadata = pd.DataFrame([
            {"work_id": "W1", "source_id": SOURCE_ID, "raw_issn_l": PARENT_ISSN_L,
             "doi": "https://doi.org/10.1029/2020JD012345", "raw_source_name": PUBLISHER_SOURCE_NAME},
            {"work_id": "W2", "source_id": SOURCE_ID, "raw_issn_l": PARENT_ISSN_L,
             "doi": "https://doi.org/10.1002/jgrd.54397", "raw_source_name": PUBLISHER_SOURCE_NAME},
            {"work_id": "W3", "source_id": SOURCE_ID, "raw_issn_l": PARENT_ISSN_L,
             "doi": None, "raw_source_name": MAG_SOURCE_NAME},
            {"work_id": "W4", "source_id": SOURCE_ID, "raw_issn_l": PARENT_ISSN_L,
             "doi": "https://doi.org/10.5281/zenodo.1", "raw_source_name": None},
            {"work_id": "W5", "source_id": SOURCE_ID, "raw_issn_l": ATMOSPHERES_ISSN_L,
             "doi": "https://doi.org/10.1029/2021JD012346", "raw_source_name": PUBLISHER_SOURCE_NAME},
        ])
        self.extra = pd.DataFrame([
            {"work_id": "W2", "provenance": "crossref",
             "location_raw_source_name": PUBLISHER_SOURCE_NAME,
             "landing_page_url": "https://doi.org/10.1002/jgrd.54397"},
            {"work_id": "W3", "provenance": "mag",
             "location_raw_source_name": MAG_SOURCE_NAME,
             "landing_page_url": "https://jglobal.jst.go.jp/example"},
            {"work_id": "W4", "provenance": "datacite",
             "location_raw_source_name": None,
             "landing_page_url": "https://doi.org/10.5281/zenodo.1"},
        ])
        self.flags = pd.DataFrame([
            {"work_id": "W1", "work_type": "article", "is_ar": True, "has_refs": True},
            {"work_id": "W2", "work_type": "retraction", "is_ar": False, "has_refs": True},
            {"work_id": "W3", "work_type": "article", "is_ar": True, "has_refs": False},
            {"work_id": "W4", "work_type": "article", "is_ar": True, "has_refs": False},
            {"work_id": "W5", "work_type": "article", "is_ar": True, "has_refs": True},
        ])

    def test_publisher_work_rekey_and_nonpublisher_exclusion(self) -> None:
        ledger = build_ledger(self.metadata, self.extra, self.flags)
        excluded = build_exclusions(self.metadata, self.extra, self.flags)
        self.assertEqual(ledger.work_id.tolist(), ["W1", "W2"])
        self.assertEqual(set(ledger.target_raw_issn_l), {ATMOSPHERES_ISSN_L})
        self.assertEqual(excluded.work_id.tolist(), ["W3", "W4"])
        self.assertEqual(set(ledger.work_id) & set(excluded.work_id), set())
        self.assertEqual(len(ledger) + len(excluded) + 1, len(self.metadata))

    def test_other_jgr_section_doi_forces_review(self) -> None:
        data = self.metadata.copy()
        data.loc[data.work_id.eq("W1"), "doi"] = "https://doi.org/10.1029/2017JF004361"
        with self.assertRaisesRegex(ValueError, "Unreviewed JGR content"):
            build_ledger(data, self.extra, self.flags)

    def test_mirror_with_references_forces_review(self) -> None:
        flags = self.flags.copy()
        flags.loc[flags.work_id.eq("W3"), "has_refs"] = True
        with self.assertRaisesRegex(ValueError, "Unreviewed JGR content"):
            build_exclusions(self.metadata, self.extra, flags)

    def test_source_metadata_separates_parent_and_section(self) -> None:
        latest = pd.DataFrame([
            {"source_id": SOURCE_ID, "raw_issn_l": PARENT_ISSN_L,
             "source_type": "journal", "title": MAG_SOURCE_NAME,
             "publisher": "American Geophysical Union",
             "issns": (PARENT_ISSN_L, "2156-2202", *('2169-897X', '2169-8996'))},
            {"source_id": "SOTHER", "raw_issn_l": "1234-5679",
             "source_type": "journal", "title": "Other", "publisher": "Other",
             "issns": ("1234-5679",)},
        ])
        fixed = repair_latest_sources(latest)
        self.assertEqual(fixed.loc[0, "raw_issn_l"], ATMOSPHERES_ISSN_L)
        self.assertEqual(fixed.loc[0, "issns"], ("2169-897X", "2169-8996"))
        self.assertEqual(fixed.loc[1].to_dict(), latest.loc[1].to_dict())
        self.assertEqual(repair_latest_sources(fixed).to_dict("records"), fixed.to_dict("records"))


if __name__ == "__main__":
    unittest.main()
