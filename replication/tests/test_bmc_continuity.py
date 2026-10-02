"""BMC continuation preflight and current-title correction."""

from pathlib import Path
import sys
import unittest

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bmc_continuity import (  # noqa: E402
    CURRENT_TITLE, PREDECESSOR_TITLE, SOURCE_ID, adjust_latest_sources,
    audit_bmc_sources,
)


def source(**changes):
    row = {
        "source_id": SOURCE_ID, "raw_issn_l": "1471-2296",
        "source_type": "journal", "title": PREDECESSOR_TITLE,
        "issns": ["1471-2296", "2731-4553"], "publisher": "Springer",
    }
    row.update(changes)
    return row


class BmcContinuityTests(unittest.TestCase):
    def test_old_title_is_corrected_without_changing_graph_key(self):
        latest = pd.DataFrame([source(), {
            "source_id": "https://openalex.org/S2", "raw_issn_l": "1234-5679",
            "source_type": "journal", "title": "Other", "issns": ["1234-5679"],
            "publisher": "Other publisher",
        }])
        corrected, report = adjust_latest_sources(latest)
        self.assertEqual(report["status"], "APPROVED_CONTINUITY")
        self.assertTrue(report["metadata_changed"])
        self.assertEqual(corrected.loc[0, "title"], CURRENT_TITLE)
        self.assertEqual(corrected.loc[0, "raw_issn_l"], "1471-2296")
        self.assertEqual(corrected.loc[0, "issns"], ["1471-2296", "2731-4553"])
        self.assertEqual(corrected.loc[1, "title"], "Other")
        self.assertEqual(latest.loc[0, "title"], PREDECESSOR_TITLE)

    def test_future_snapshot_already_has_current_title(self):
        latest = pd.DataFrame([source(title=CURRENT_TITLE)])
        corrected, report = adjust_latest_sources(latest)
        self.assertFalse(report["metadata_changed"])
        self.assertEqual(corrected.loc[0, "title"], CURRENT_TITLE)

    def test_unreviewed_source_split_is_blocked(self):
        latest = pd.DataFrame([source(), source(
            source_id="https://openalex.org/S4210213938", raw_issn_l="2731-4553",
            title=CURRENT_TITLE, issns=["2731-4553"])])
        report = audit_bmc_sources(latest)
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertIn("expected_single_continuation_source_changed", report["review_reasons"])
        with self.assertRaisesRegex(ValueError, "BMC continuation needs review"):
            adjust_latest_sources(latest)

    def test_unreviewed_alias_or_key_is_blocked(self):
        for changed in (source(issns=["1471-2296"]),
                        source(raw_issn_l="2731-4553")):
            with self.subTest(changed=changed):
                self.assertEqual(audit_bmc_sources(pd.DataFrame([changed]))["status"],
                                 "REVIEW_REQUIRED")

    def test_work_counts_can_update_but_identity_preimages_cannot(self):
        latest = pd.DataFrame([source()])
        inventory = pd.DataFrame([
            {"source_id": SOURCE_ID, "raw_issn_l": "1471-2296", "year": 2022, "work_count": 341},
            {"source_id": SOURCE_ID, "raw_issn_l": "1471-2296", "year": 2026, "work_count": 405},
        ])
        report = audit_bmc_sources(latest, inventory)
        self.assertEqual(report["status"], "APPROVED_CONTINUITY")
        self.assertEqual(report["observed_work_count"], 746)
        inventory.loc[1, "raw_issn_l"] = "2731-4553"
        report = audit_bmc_sources(latest, inventory)
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertIn("work_source_or_issn_l_changed", report["review_reasons"])


if __name__ == "__main__":
    unittest.main()
