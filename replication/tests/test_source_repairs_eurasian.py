"""The Eurasian repair separates Work endpoints and rejects source drift."""

from pathlib import Path
import sys
import unittest

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import source_repairs_eurasian as repair


def merged_source(**changes):
    row = {
        "source_id": repair.ECONOMIC_ID,
        "raw_issn_l": repair.ECONOMIC_ISSN_L,
        "source_type": "journal",
        "title": "Eurasian economic review :",
        "publisher": "Springer Nature",
        "issns": tuple(sorted(repair.ALL_ISSNS)),
        "oa_field": "Business, Management and Accounting",
        "oa_domain": "Social Sciences",
        "is_open_access": False,
        "updated_date": pd.Timestamp("2026-09-23"),
    }
    row.update(changes)
    return row


def work(work_id, doi, title="A journal article", **changes):
    row = {
        "work_id": work_id,
        "source_id": repair.ECONOMIC_ID,
        "raw_issn_l": repair.ECONOMIC_ISSN_L,
        "doi": doi,
        "title": title,
    }
    row.update(changes)
    return row


def example_works():
    return pd.DataFrame([
        work("https://openalex.org/W1", "https://doi.org/10.1007/s40821-025-00001-1"),
        work(repair.PUBLISHED_WORK_ID, "https://doi.org/" + repair.PUBLISHED_DOI,
             repair.ZENODO_TITLE),
        work("https://openalex.org/W2", "https://doi.org/10.1007/s40822-025-00002-2"),
        work(repair.ZENODO_WORK_ID, "https://doi.org/" + repair.ZENODO_DOI,
             repair.ZENODO_TITLE),
    ])


class EurasianRepairTests(unittest.TestCase):
    def test_merged_source_becomes_two_real_historical_ids(self):
        fixed, audit = repair.repair_latest_sources(pd.DataFrame([merged_source()]))
        self.assertEqual(audit["source_state"], "merged_source_split")
        self.assertEqual(len(fixed), 2)
        business = fixed.set_index("source_id").loc[repair.BUSINESS_ID]
        economic = fixed.set_index("source_id").loc[repair.ECONOMIC_ID]
        self.assertEqual((business.raw_issn_l, business.title),
                         ("1309-4297", "Eurasian Business Review"))
        self.assertEqual(set(business.issns), repair.BUSINESS_ISSNS)
        self.assertEqual((economic.raw_issn_l, economic.oa_field),
                         ("1309-422X", "Economics, Econometrics and Finance"))
        self.assertEqual(set(economic.issns), repair.ECONOMIC_ISSNS)
        again, repeat = repair.repair_latest_sources(fixed)
        self.assertEqual(repeat["source_state"], "already_separate")
        pd.testing.assert_frame_equal(fixed, again)

    def test_unfamiliar_source_claiming_journal_issn_stops(self):
        source = merged_source(source_id="https://openalex.org/S999")
        with self.assertRaisesRegex(ValueError, "unreviewed Source IDs"):
            repair.repair_latest_sources(pd.DataFrame([source]))

    def test_separated_source_field_drift_stops(self):
        fixed, _ = repair.repair_latest_sources(pd.DataFrame([merged_source()]))
        fixed.loc[fixed.source_id.eq(repair.ECONOMIC_ID), "oa_field"] = "History"
        with self.assertRaisesRegex(ValueError, "title or field changed"):
            repair.repair_latest_sources(fixed)

    def test_split_ledger_and_duplicate_exclusion(self):
        works = example_works()
        excluded = repair.build_exclusions(works)
        self.assertEqual(excluded.work_id.tolist(), [repair.ZENODO_WORK_ID])
        ledger = repair.build_ledger(works, expected_work_count=4)
        self.assertEqual(len(ledger), 2)
        self.assertEqual(set(ledger.work_id), {"https://openalex.org/W1", repair.PUBLISHED_WORK_ID})
        self.assertEqual(set(ledger.target_source_id), {repair.BUSINESS_ID})
        self.assertEqual(set(ledger.target_raw_issn_l), {repair.BUSINESS_ISSN_L})

    def test_unfamiliar_work_doi_stops_for_review(self):
        works = example_works()
        works.loc[works.work_id.eq("https://openalex.org/W2"), "doi"] = "https://doi.org/10.9999/unexpected"
        with self.assertRaisesRegex(ValueError, "Unresolved Eurasian Work DOI"):
            repair.build_ledger(works)

    def test_duplicate_exclusion_requires_published_version_and_exact_title(self):
        works = example_works()
        with self.assertRaisesRegex(ValueError, "duplicate/version Work IDs changed"):
            repair.build_exclusions(works.loc[~works.work_id.eq(repair.PUBLISHED_WORK_ID)])
        works.loc[works.work_id.eq(repair.ZENODO_WORK_ID), "title"] = "Different paper"
        with self.assertRaisesRegex(ValueError, "duplicate changed"):
            repair.build_exclusions(works)

    def test_already_separated_work_needs_no_override(self):
        works = example_works().loc[lambda frame: ~frame.work_id.eq(repair.ZENODO_WORK_ID)].copy()
        business = works.doi.str.contains("s40821", regex=False)
        works.loc[business, "source_id"] = repair.BUSINESS_ID
        works.loc[business, "raw_issn_l"] = repair.BUSINESS_ISSN_L
        self.assertTrue(repair.build_ledger(works).empty)
        self.assertTrue(repair.build_exclusions(works).empty)


if __name__ == "__main__":
    unittest.main()
