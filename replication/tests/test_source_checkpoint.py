"""Sources checkpoint checks using only temporary synthetic snapshots."""

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import source_checkpoint


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def raw_source(**changes):
    row = {
        "id": "S1", "issn_l": "1234-5679", "type": "journal", "display_name": "Journal",
        "host_organization_name": "Publisher", "issn": ["1234-5679", " 2049-3630 ", "1234-5679"],
        "topics": [{"field": {"display_name": "Economics"},
                    "domain": {"display_name": "Social Sciences"}}],
        "is_oa": True, "updated_date": "2026-09-02 10:11:12.123456",
    }
    row.update(changes)
    return row


class SourcesCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="website-sources-checkpoint-test-")
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name) / "run"
        self.raw = Path(self.temp.name) / "raw" / "sources"
        self.raw.mkdir(parents=True)
        self.output = self.run / "intermediates" / "sources"
        self.config_path = self.run / "config" / "run.json"
        self.config = {"snapshot_date": "2026-09-23", "raw_snapshot_root": str(self.raw.parent),
                       "paths_relative_to_run_root": {"intermediates": "intermediates"}}
        write_json(self.config_path, self.config)
        batches = [[raw_source(updated_date="2026-09-01", display_name="Older title")],
                   [raw_source(), raw_source(id="S2", issn_l=None, issn=None, topics=[],
                                            is_oa=None, host_organization_name=None)]]
        entries = []
        for index, rows in enumerate(batches):
            path = self.raw / f"part{index}.parquet"
            with duckdb.connect() as con:
                con.register("rows", pd.DataFrame(rows))
                con.execute("COPY rows TO ? (FORMAT PARQUET)", [str(path)])
            entries.append({"url": f"s3://openalex/data/parquet/sources/{path.name}",
                            "meta": {"content_length": path.stat().st_size, "record_count": len(rows)}})
        size = sum(item["meta"]["content_length"] for item in entries)
        manifest = {"entity": "sources", "format": "parquet", "date": "2026-09-23",
                    "content_length": size, "record_count": 3, "files": entries}
        self.copied = self.run / "snapshot-manifests" / "sources-manifest.json"
        write_json(self.copied, manifest)
        write_json(self.raw / "manifest.json", manifest)
        write_json(self.run / "run-record.json", {"snapshot": {"entities": {"sources": {
            "files": 2, "bytes": size,
            "manifest_sha256": hashlib.sha256(self.copied.read_bytes()).hexdigest()}}}})

    def build(self):
        return source_checkpoint.checkpoint_sources(self.run)

    def test_fresh_checkpoint_and_resume_preserve_aliases_missing_metadata_and_review_gates(self):
        real_reader = source_checkpoint.sources.read_manifest_sources
        with patch.object(source_checkpoint.sources, "read_manifest_sources", wraps=real_reader) as reader:
            latest, report = self.build()
            self.assertEqual(reader.call_count, 1)
        self.assertEqual(len(latest), 2)
        self.assertEqual(latest.iloc[0].title, "Journal")
        self.assertEqual(latest.iloc[0].issns, ("1234-5679", "2049-3630"))
        self.assertEqual(latest.iloc[1].issns, ())
        self.assertTrue(pd.isna(latest.iloc[1].publisher))
        self.assertTrue(pd.isna(latest.iloc[1].is_open_access))
        self.assertFalse(report["reused"])
        self.assertEqual(report["raw_sources_scans_this_invocation"], 1)
        self.assertEqual(report["rows"], 2)
        self.assertFalse(report["scores_computed"])
        self.assertFalse(report["identity_projection_performed"])
        self.assertEqual(len(report["review_gates"]), 3)
        with patch.object(source_checkpoint.sources, "read_manifest_sources", side_effect=AssertionError("raw read on resume")):
            reloaded, resumed = self.build()
        pd.testing.assert_frame_equal(latest, reloaded)
        self.assertTrue(resumed["reused"])
        self.assertEqual(resumed["raw_sources_scans_this_invocation"], 0)
        self.assertTrue(all(isinstance(value, tuple) for value in reloaded.issns))
        self.assertFalse((self.output / "source_checkpoint.lock").exists())

    def test_changed_config_rejects_reuse(self):
        self.build()
        write_json(self.config_path, {**self.config, "changed": True})
        with self.assertRaisesRegex(RuntimeError, "contract changed"):
            self.build()

    def test_all_empty_aliases_and_optional_metadata_round_trip(self):
        real_reader = source_checkpoint.sources.read_manifest_sources

        def empty_optional_fields(*args, **kwargs):
            frame, audit = real_reader(*args, **kwargs)
            frame["issns"] = [() for _ in range(len(frame))]
            for column in ("publisher", "oa_field", "oa_domain"):
                frame[column] = None
            frame["is_open_access"] = pd.Series([pd.NA] * len(frame), dtype="boolean")
            return frame, audit

        with patch.object(source_checkpoint.sources, "read_manifest_sources", side_effect=empty_optional_fields):
            latest, _ = self.build()
        restored, _ = self.build()
        pd.testing.assert_frame_equal(latest, restored)
        self.assertEqual(list(restored.issns), [(), ()])
        self.assertTrue(restored.is_open_access.isna().all())

    def test_changed_raw_inventory_rejects_reuse(self):
        self.build()
        path = self.raw / "part0.parquet"
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        with self.assertRaisesRegex(RuntimeError, "contract changed"):
            self.build()

    def test_manifest_hash_mismatch_rejected_before_read(self):
        self.copied.write_text(self.copied.read_text(encoding="utf-8") + " ", encoding="utf-8")
        with patch.object(source_checkpoint.sources, "read_manifest_sources", side_effect=AssertionError("unexpected read")):
            with self.assertRaisesRegex(ValueError, "manifest hashes differ"):
                self.build()
        self.assertFalse(self.output.exists())

    def test_partial_file_is_preserved_and_blocks_resume(self):
        self.output.mkdir(parents=True)
        partial = self.output / "latest_sources.parquet.partial"
        partial.write_bytes(b"unfinished")
        with self.assertRaisesRegex(RuntimeError, "Incomplete Sources checkpoint"):
            self.build()
        self.assertEqual(partial.read_bytes(), b"unfinished")

    def test_orphan_and_corrupt_cache_are_not_reused(self):
        self.build()
        receipt = self.output / "latest_sources.receipt.json"
        saved = receipt.read_bytes()
        receipt.unlink()
        with self.assertRaisesRegex(RuntimeError, "Orphan Sources"):
            self.build()
        receipt.write_bytes(saved)
        (self.output / "latest_sources.parquet").write_bytes(b"corrupt")
        with self.assertRaisesRegex(RuntimeError, "hash receipt"):
            self.build()

    def test_wrong_receipt_row_count_and_missing_contract_are_rejected(self):
        self.build()
        receipt_path = self.output / "latest_sources.receipt.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        write_json(receipt_path, {**receipt, "rows": 999})
        with self.assertRaisesRegex(RuntimeError, "row count differs"):
            self.build()
        (self.output / "contract.json").unlink()
        with self.assertRaisesRegex(RuntimeError, "without its bound contract"):
            self.build()

    def test_normalization_rejects_scalar_or_malformed_aliases(self):
        latest, _ = self.build()
        latest.at[0, "issns"] = "1234-5679"
        with self.assertRaisesRegex(ValueError, "scalar text"):
            source_checkpoint.normalize_latest(latest)
        latest.at[0, "issns"] = [123]
        with self.assertRaisesRegex(ValueError, "only strings"):
            source_checkpoint.normalize_latest(latest)

    def test_output_path_escape_rejected(self):
        write_json(self.config_path, {**self.config, "paths_relative_to_run_root": {"intermediates": "../../escape"}})
        with self.assertRaisesRegex(ValueError, "within the dated run"):
            self.build()
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
