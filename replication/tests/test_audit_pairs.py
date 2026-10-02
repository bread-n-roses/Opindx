"""Pair inventory checks on tiny, explicitly bound synthetic extraction outputs."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit_pairs


A, D = "1234-5679", "0378-5955"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class RawPairAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="website-pair-audit-test-")
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name) / "run"
        self.extraction = self.run / "intermediates" / "extraction"
        self.output = self.run / "intermediates" / "identity-review" / "raw-pairs"
        self.config_path = self.run / "config" / "run.json"
        write_json(self.config_path, {"score_years": [2022, 2023],
                                     "paths_relative_to_run_root": {"intermediates": "intermediates"}})
        code = self.run / "code" / "extract.py"
        code.parent.mkdir(parents=True)
        code.write_text("# Synthetic extraction receipt fixture; no extraction is run.\n", encoding="utf-8")
        self.contract = {"config_sha256": audit_pairs._hash(self.config_path),
                         "extractor_sha256": audit_pairs._hash(code), "version": "synthetic-extraction"}
        write_json(self.extraction / "contract.json", self.contract)
        self.report = {"status": "PASS", "score_years": [2022, 2023],
                       "contract_sha256": audit_pairs._digest(self.contract), "stages": {}}
        self.retention = pd.DataFrame([
            ("S1", A, 2017, 2, 1), ("S1", A, 2021, 3, 2),
            ("S1", "bad-key", 2018, 2, 1), ("S2", None, 2019, 1, 0),
            ("S3", "1111-1111", 2020, 1, 1), ("S4", "", 2021, 1, 0),
            ("S5", "2049-3630", 2020, 0, 0), ("S7", D, 2022, 3, 3),
        ], columns=["source_id", "raw_issn_l", "year", "a_raw", "a_filtered"])
        for year in (2022, 2023):
            counts = self.retention.loc[self.retention.year.between(year - 5, year - 1)].groupby(
                ["source_id", "raw_issn_l"], as_index=False, dropna=False)[["a_raw", "a_filtered"]].sum()
            self.write_stage(f"counts_{year}", self.extraction / str(year) / "counts_raw_identity.parquet", counts)
        edge_columns = ["citing_source_id", "raw_citing_issn_l", "cited_source_id", "raw_cited_issn_l", "n_raw", "n_filtered"]
        edges_2022 = pd.DataFrame([
            ("S1", A, "S1", "bad-key", 7, 4), ("S2", None, "S1", A, 3, 1),
            ("S3", "1111-1111", "S4", "", 2, 0), ("S6", D, "S1", A, 1, 0),
            (None, A, "S3", "1111-1111", 1, 1),
        ], columns=edge_columns)
        edges_2023 = pd.DataFrame([
            ("S1", "bad-key", "S7", D, 4, 2), ("S7", D, "S1", A, 2, 2),
            (None, A, "S1", A, 2, 1),
        ], columns=edge_columns)
        for year, edges in ((2022, edges_2022), (2023, edges_2023)):
            self.write_stage(f"edges_{year}", self.extraction / str(year) / "edges_raw_identity.parquet", edges)
        self.write_stage("raw_articles_by_publication_year",
                         self.extraction / "raw_articles_by_publication_year.parquet", self.retention)

    def write_stage(self, name, path, frame):
        path.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect() as con:
            con.register("fixture", frame)
            con.execute("COPY fixture TO ? (FORMAT PARQUET)", [str(path)])
        write_json(path.with_suffix(".receipt.json"), {
            "contract_sha256": self.report["contract_sha256"], "sha256": audit_pairs._hash(path),
            "bytes": path.stat().st_size, "rows": len(frame), "sql_sha256": "0" * 64,
        })
        self.report["stages"][name] = {"rows": len(frame), "path": str(path), "reused": False}
        write_json(self.extraction / "report.json", self.report)

    def build(self):
        return audit_pairs.audit_pairs(self.run, memory_limit="256MB", threads=1)

    def read(self, name):
        with duckdb.connect() as con:
            return con.execute("SELECT * FROM read_parquet(?)", [str(self.output / name)]).df()

    def test_known_pair_masses_variants_invalid_keys_and_safe_resume(self):
        report = self.build()
        self.assertFalse(report["reused"])
        self.assertFalse(report["identity_corrections_approved"])
        self.assertFalse(report["scores_computed"])
        self.assertEqual(report["graph_only_sources_comparison"], "deferred")
        self.assertEqual(report["outputs"]["annual_pairs.parquet"]["rows"], 14)
        pairs = self.read("annual_pairs.parquet")
        self.assertFalse(pairs.source_id.eq("S5").any())  # Pure zero mass is excluded.
        row = pairs.loc[pairs.score_year.eq(2022) & pairs.source_id.eq("S1") & pairs.raw_issn_l.eq(A)].iloc[0]
        self.assertEqual((row.a_raw, row.a_filtered, row.retention_a_raw, row.retention_a_filtered), (5, 3, 5, 3))
        self.assertEqual((row.citing_n_raw, row.cited_n_raw), (7, 4))
        self.assertEqual((row.active_publication_years_raw, row.source_observed_raw_variants), (2, 2))
        row_2023 = pairs.loc[pairs.score_year.eq(2023) & pairs.source_id.eq("S1") & pairs.raw_issn_l.eq(A)].iloc[0]
        self.assertEqual((row_2023.a_raw, row_2023.active_publication_years_raw), (3, 1))
        counts = pairs.loc[pairs.score_year.eq(2022)].raw_key_status.value_counts().to_dict()
        self.assertEqual(counts, {"valid": 3, "missing": 2, "invalid_syntax": 1, "invalid_checksum": 1})
        self.assertEqual(int(pairs.source_id_missing.sum()), 2)
        self.assertEqual(report["annual_key_quality"]["2022"], {
            "pair_count": 7, "valid_raw_key_pairs": 3, "missing_raw_key_pairs": 2,
            "invalid_raw_key_pairs": 2, "invalid_syntax_pairs": 1, "invalid_checksum_pairs": 1,
            "null_raw_key_pairs": 1, "blank_raw_key_pairs": 1, "missing_source_id_pairs": 1})
        self.assertEqual(report["sources_with_multiple_raw_variants_by_year"], {"2022": 1, "2023": 1})
        variants = self.read("source_variants.parquet")
        s1 = variants.loc[variants.score_year.eq(2022) & variants.source_id.eq("S1")].iloc[0]
        self.assertEqual(set(s1.raw_issn_l_variants), {A, "bad-key"})
        self.assertEqual(s1.invalid_raw_variant_count, 1)
        for year, raw, filtered in ((2022, 14, 6), (2023, 8, 5)):
            annual = pairs.loc[pairs.score_year.eq(year)]
            self.assertEqual(int(annual.citing_n_raw.sum()), raw)
            self.assertEqual(int(annual.cited_n_raw.sum()), raw)
            self.assertEqual(int(annual.citing_n_filtered.sum()), filtered)
            self.assertEqual(int(annual.cited_n_filtered.sum()), filtered)
        self.assertTrue(self.build()["reused"])

    def test_running_or_failed_extraction_blocks_audit(self):
        lock = self.extraction / "extraction.lock"
        lock.write_text("123", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "still running"):
            self.build()
        lock.unlink()
        self.report["status"] = "STOP"
        write_json(self.extraction / "report.json", self.report)
        with self.assertRaisesRegex(RuntimeError, "completed extraction PASS"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_changed_input_payload_and_receipt_are_rejected(self):
        path = self.extraction / "2022" / "edges_raw_identity.parquet"
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaisesRegex(RuntimeError, "input/receipt/report disagreement"):
            self.build()

    def test_pair_retention_disagreement_fails_without_completion_report(self):
        changed = self.retention.copy()
        changed.loc[0, "a_raw"] += 1
        self.write_stage("raw_articles_by_publication_year",
                         self.extraction / "raw_articles_by_publication_year.parquet", changed)
        with self.assertRaisesRegex(RuntimeError, "disagree with cited-window retention"):
            self.build()
        self.assertFalse((self.output / "report.json").exists())
        self.assertTrue((self.output / "annual_pairs.parquet.partial").exists())

    def test_partial_or_modified_completed_audit_is_not_reused(self):
        self.build()
        partial = self.output / "annual_pairs.parquet.partial"
        partial.write_bytes(b"unfinished")
        with self.assertRaisesRegex(RuntimeError, "Incomplete pair-audit"):
            self.build()
        partial.unlink()
        (self.output / "annual_pairs.parquet").write_bytes(b"corrupt")
        with self.assertRaisesRegex(RuntimeError, "output differs"):
            self.build()

    def test_negative_or_non_nested_mass_fails_before_inventory(self):
        path = self.extraction / "2022" / "counts_raw_identity.parquet"
        with duckdb.connect() as con:
            bad = con.execute("SELECT * FROM read_parquet(?)", [str(path)]).df()
        bad.loc[0, "a_filtered"] = bad.loc[0, "a_raw"] + 1
        self.write_stage("counts_2022", path, bad)
        with self.assertRaisesRegex(RuntimeError, "Invalid row count or Raw/Filtered mass"):
            self.build()


if __name__ == "__main__":
    unittest.main()
