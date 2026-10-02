"""Source discovery and targeted Work coverage guards."""

from pathlib import Path
import sys
import tempfile
import unittest

import duckdb
import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_source_repair_preflight as preflight  # noqa: E402


def latest_sources():
    common = {"source_type": "journal", "updated_date": "2026-09-23",
              "is_open_access": False, "oa_domain": "Social Sciences"}
    return pd.DataFrame([
        {**common, "source_id": "https://openalex.org/S4210169982",
         "raw_issn_l": "1309-422X", "title": "Eurasian economic review :",
         "publisher": "Springer Nature", "oa_field": "Business, Management and Accounting",
         "issns": ("1309-422X", "1309-4297", "2147-4281", "2147-429X")},
        {**common, "source_id": "https://openalex.org/S207178839",
         "raw_issn_l": "0148-0227", "title": "Journal of Geophysical Research Atmospheres",
         "publisher": "American Geophysical Union", "oa_field": "Physics and Astronomy",
         "issns": ("0148-0227", "2156-2202", "2169-897X", "2169-8996")},
        {**common, "source_id": "https://openalex.org/S153257185",
         "raw_issn_l": "1471-2296", "title": "BMC Family Practice",
         "publisher": "Springer Science+Business Media", "oa_field": "Health Professions",
         "issns": ("1471-2296", "2731-4553")},
    ])


class SourceRepairPreflightTests(unittest.TestCase):
    def test_source_discovery_finds_two_targeted_sources_and_bmc(self):
        report = preflight.discover_source_cases(latest_sources())
        self.assertEqual(report["jgr_source_id"], "https://openalex.org/S207178839")
        self.assertEqual(report["required_target_source_ids"], [
            "https://openalex.org/S207178839", "https://openalex.org/S4210169982"])
        self.assertEqual(report["bmc_source_id"], "https://openalex.org/S153257185")

    def test_new_eurasian_id_requires_source_review(self):
        latest = latest_sources()
        unexpected = latest.iloc[0].copy()
        unexpected["source_id"] = "https://openalex.org/S999999999"
        latest = pd.concat([latest, unexpected.to_frame().T], ignore_index=True)
        with self.assertRaisesRegex(ValueError, "Eurasian source IDs changed"):
            preflight.discover_source_cases(latest)

    def test_targeted_coverage_checks_ids_source_keys_and_years(self):
        source_ids = ["https://openalex.org/S207178839",
                      "https://openalex.org/S4210169982"]
        baseline = pd.DataFrame([
            {"work_id": "W1", "source_id": source_ids[0], "raw_issn_l": "0148-0227", "year": 2021},
            {"work_id": "W2", "source_id": source_ids[1], "raw_issn_l": "1309-422X", "year": 2022},
            {"work_id": "W3", "source_id": "S-unrelated", "raw_issn_l": "0000-0000", "year": 2022},
        ])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "works.parquet"
            with duckdb.connect() as con:
                con.register("fixture", baseline)
                con.execute("COPY fixture TO ? (FORMAT PARQUET)", [str(path)])
            discovery = {"required_target_source_ids": source_ids}
            metadata = baseline.iloc[:2].copy()
            report = preflight._verify_targeted_coverage(path, metadata, discovery)
            self.assertEqual(report["affected_work_rows"], 2)
            with self.assertRaisesRegex(RuntimeError, "omits or changes"):
                preflight._verify_targeted_coverage(path, metadata.iloc[:1], discovery)
            metadata.loc[1, "year"] = 2023
            with self.assertRaisesRegex(RuntimeError, "omits or changes"):
                preflight._verify_targeted_coverage(path, metadata, discovery)


if __name__ == "__main__":
    unittest.main()
