"""Tiny synthetic extraction checks; these tests never access the real snapshot."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import extract


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class ExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="website-extract-test-")
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name) / "run"
        self.raw = Path(self.temp.name) / "raw" / "works"
        self.raw.mkdir(parents=True)
        self.output = self.run / "intermediates" / "extraction"
        self.config = {
            "snapshot_date": "2026-09-23", "raw_snapshot_root": str(self.raw.parent),
            "score_years": [2022, 2023],
            "year_convention": {"score_year_is_citing_year": True,
                                "cited_window_start_offset": -5, "cited_window_end_offset": -1},
            "planned_work_extract": {"publication_year_start": 2017,
                                     "publication_year_end": 2023,
                                     "retain_reference_arrays_for_years": [2022, 2023]},
            "paths_relative_to_run_root": {"intermediates": "intermediates"},
        }
        write_json(self.run / "config" / "run.json", self.config)
        # id, source, key, year, type, reference count, refs, update, paratext, partition.
        rows = [
            ("A", "SA", "0000-0000", 2017, "article", 1, ["unused"], "2026-01-01", False, "a"),
            ("B", "SB", "1111-1111", 2018, "article", 0, [], "2026-01-01", False, "a"),
            ("C", "SA", "0000-0000", 2021, "article", 1, ["unused"], "2026-01-01", False, "a"),
            ("C", "SA", "0000-0000", 2021, "article", 1, ["unused"], "2026-01-01", False, "a"),
            ("D", "SD_OLD", "2222-2222", 2021, "review", 1, [], "2026-01-01", False, "a"),
            ("D", "SD", "3333-3333", 2021, "review", 1, [], "2026-02-01", False, "z"),
            ("T", "ST_OLD", "4444-4444", 2021, "article", 1, [], "2026-01-01", False, "a"),
            ("T", "ST", "5555-5555", 2021, "article", 1, [], "2026-01-01", False, "z"),
            ("E", "SA", "0000-0000", 2022, "article", 8,
             ["A", "B", "C", "C", "D", "T", "E", "missing"], "2026-01-01", False, "a"),
            ("F", "SB", "1111-1111", 2023, "editorial", 4,
             ["A", "B", "C", "E"], "2026-01-01", False, "a"),
            ("excluded_type", "SA", "0000-0000", 2022, "peer-review", 1, ["C"], "2026-01-01", False, "a"),
            ("paratext", "SA", "0000-0000", 2022, "article", 1, ["C"], "2026-01-01", True, "a"),
            ("too_old", "SA", "0000-0000", 2016, "article", 1, [], "2026-01-01", False, "a"),
            ("future", "SA", "0000-0000", 2024, "article", 1, [], "2026-01-01", False, "a"),
            ("no_issn", "SA", None, 2022, "article", 1, ["C"], "2026-01-01", False, "a"),
        ]
        with duckdb.connect() as con:
            con.execute("""CREATE TABLE fixture (
                id VARCHAR, source_id VARCHAR, issn_l VARCHAR, publication_year INTEGER,
                type VARCHAR, referenced_works_count INTEGER, referenced_works VARCHAR[],
                updated_date VARCHAR, is_paratext BOOLEAN, partition VARCHAR)""")
            con.executemany("INSERT INTO fixture VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
            for partition in ("a", "z"):
                path = self.raw / f"{partition}.parquet"
                con.execute(f"""COPY (
                    SELECT id, struct_pack(source := struct_pack(id := source_id, issn_l := issn_l))
                        AS primary_location, publication_year, type, referenced_works_count,
                        referenced_works, updated_date, is_paratext
                    FROM fixture WHERE partition='{partition}'
                ) TO {extract._sql(path)} (FORMAT PARQUET)""")
        self.bind_manifest()

    def bind_manifest(self) -> None:
        entries = [{"url": f"s3://openalex/data/parquet/works/{path.name}",
                    "meta": {"content_length": path.stat().st_size}}
                   for path in sorted(self.raw.glob("*.parquet"))]
        size = sum(entry["meta"]["content_length"] for entry in entries)
        manifest = {"date": "2026-09-23", "entity": "works", "format": "parquet",
                    "content_length": size, "files": entries}
        copied = self.run / "snapshot-manifests" / "works-manifest.json"
        write_json(copied, manifest)
        write_json(self.raw / "manifest.json", manifest)
        write_json(self.run / "run-record.json", {"snapshot": {"entities": {"works": {
            "files": len(entries), "bytes": size,
            "manifest_sha256": hashlib.sha256(copied.read_bytes()).hexdigest()}}}})

    def build(self) -> dict:
        return extract.extract(self.run, memory_limit="256MB", threads=1)

    def read(self, relative: str, columns: str = "*") -> list[tuple]:
        with duckdb.connect() as con:
            return con.execute(f"SELECT {columns} FROM read_parquet({extract._sql(self.output / relative)})").fetchall()

    def test_checkpoint_uses_payload_update_not_partition_directory_date(self) -> None:
        partition = self.raw / "updated_date=2026-09-01"
        partition.mkdir()
        path = partition / "fixture.parquet"
        shutil.copyfile(self.raw / "a.parquet", path)
        with duckdb.connect() as con:
            actual = con.execute(
                f"SELECT DISTINCT updated_date FROM ({extract._checkpoint_sql([str(path)], [2022, 2023])})"
            ).fetchall()
        self.assertEqual(actual, [("2026-01-01",)])

    def test_known_annual_counts_edges_dedup_and_resume(self) -> None:
        report = self.build()
        self.assertEqual(report["raw_snapshot_scans_this_invocation"], 1)
        self.assertFalse(report["score_computed"])
        self.assertFalse(report["identities_projected"])
        self.assertEqual(report["validation"]["deduplicated_works"], 7)
        works = {row[0]: row[1:] for row in self.read("works.parquet", "work_id,source_id,refs")}
        self.assertEqual(works["D"][0], "SD")
        self.assertEqual(works["T"][0], "ST")
        self.assertIsNone(works["A"][1])
        self.assertEqual(len(works["E"][1]), 8)
        retained = self.read("raw_articles_by_publication_year.parquet",
                             "source_id,year,a_raw,a_filtered")
        self.assertIn(("SA", 2017, 1, 1), retained)
        self.assertIn(("SA", 2021, 1, 1), retained)
        for year in (2022, 2023):
            self.assertEqual(set(self.read(f"{year}/counts_raw_identity.parquet")), {
                ("SA", "0000-0000", 2, 2), ("SB", "1111-1111", 1, 0),
                ("SD", "3333-3333", 1, 1), ("ST", "5555-5555", 1, 1)})
        self.assertEqual(set(self.read("2022/edges_raw_identity.parquet",
                                      "citing_source_id,cited_source_id,n_raw,n_filtered")),
                         {("SA", "SA", 2, 2), ("SA", "SB", 1, 0),
                          ("SA", "SD", 1, 1), ("SA", "ST", 1, 1)})
        self.assertEqual(set(self.read("2023/edges_raw_identity.parquet",
                                      "citing_source_id,cited_source_id,n_raw,n_filtered")),
                         {("SB", "SA", 2, 2), ("SB", "SB", 1, 0)})
        self.assertEqual(report["validation"]["annual_totals"]["2022"]["n_raw"], 5)
        self.assertEqual(report["validation"]["annual_totals"]["2023"]["raw_key_self_citation_mass_retained"], 1)
        resumed = self.build()
        self.assertEqual(resumed["raw_snapshot_scans_this_invocation"], 0)
        self.assertTrue(all(stage["reused"] for stage in resumed["stages"].values()))

    def test_changed_configuration_rejects_reuse(self) -> None:
        self.build()
        self.config["annotation"] = "changed after extraction"
        write_json(self.run / "config" / "run.json", self.config)
        with self.assertRaisesRegex(RuntimeError, "contract changed"):
            self.build()

    def test_changed_snapshot_mtime_rejects_reuse(self) -> None:
        self.build()
        path = self.raw / "a.parquet"
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        with self.assertRaisesRegex(RuntimeError, "contract changed"):
            self.build()

    def test_changed_manifest_rejected_before_extraction(self) -> None:
        path = self.raw / "manifest.json"
        path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "manifest hash"):
            self.build()
        self.assertFalse((self.output / "work_extract_raw.parquet").exists())

    def test_only_manifest_listed_files_are_scanned(self) -> None:
        (self.raw / "unlisted.parquet").write_bytes(b"not a parquet file")
        report = self.build()
        self.assertEqual(report["validation"]["deduplicated_works"], 7)

    def test_manifest_path_escape_rejected(self) -> None:
        copied = self.run / "snapshot-manifests" / "works-manifest.json"
        manifest = json.loads(copied.read_text(encoding="utf-8"))
        manifest["files"][0]["url"] = "s3://openalex/data/parquet/works/../outside.parquet"
        write_json(copied, manifest)
        write_json(self.raw / "manifest.json", manifest)
        record_path = self.run / "run-record.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["snapshot"]["entities"]["works"]["manifest_sha256"] = hashlib.sha256(copied.read_bytes()).hexdigest()
        write_json(record_path, record)
        with self.assertRaisesRegex(ValueError, "Invalid or repeated Works manifest path"):
            self.build()

    def test_partial_checkpoint_is_not_reused_or_deleted(self) -> None:
        self.output.mkdir(parents=True)
        partial = self.output / "work_extract_raw.parquet.partial"
        partial.write_bytes(b"interrupted")
        with self.assertRaisesRegex(RuntimeError, "Incomplete .partial"):
            self.build()
        self.assertEqual(partial.read_bytes(), b"interrupted")

    def test_orphan_and_modified_output_are_rejected(self) -> None:
        self.build()
        receipt = self.output / "works.receipt.json"
        saved = receipt.read_bytes()
        receipt.unlink()
        with self.assertRaisesRegex(RuntimeError, "Orphan"):
            self.build()
        receipt.write_bytes(saved)
        (self.output / "works.parquet").write_bytes(b"corrupt")
        with self.assertRaisesRegex(RuntimeError, "does not match its receipt"):
            self.build()

    def test_invalid_window_fails_before_outputs(self) -> None:
        self.config["planned_work_extract"]["publication_year_start"] = 2018
        write_json(self.run / "config" / "run.json", self.config)
        with self.assertRaisesRegex(ValueError, "planned_work_extract"):
            self.build()
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
