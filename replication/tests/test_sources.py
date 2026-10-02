"""Synthetic Sources metadata: version ties, annual identities and type conflicts."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

import duckdb
import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from identity import AnnualIdentityMaps, SourceKeyOverride
from sources import (
    LATEST_COLUMNS, journal_export_view, project_sources, read_latest_sources,
    read_manifest_sources, source_paths_from_manifest,
)


A, B, C = "1234-5679", "0000-0000", "2049-3630"


def maps(aliases=None, overrides=None, corrections=None):
    return AnnualIdentityMaps(2022, overrides or {}, aliases or {}, corrections or {}, "approved_for_score_year")


def source(source_id="S1", key=A, **changes):
    row = {
        "source_id": source_id, "raw_issn_l": key, "source_type": "journal",
        "title": "Example journal", "publisher": "Example publisher",
        "issns": (key,) if key else (), "oa_field": "Economics",
        "oa_domain": "Social Sciences", "is_open_access": False,
        "updated_date": "2026-09-01",
    }
    row.update(changes)
    return row


def raw_source(**changes):
    row = {
        "id": "S1", "issn_l": A, "type": "journal", "display_name": "Current title",
        "host_organization_name": "Current publisher", "issn": [A, "1234-567X"],
        "topics": [
            {"field": {"display_name": "Economics"}, "domain": {"display_name": "Social Sciences"}},
            {"field": {"display_name": "History"}, "domain": {"display_name": "Arts and Humanities"}},
        ],
        "is_oa": False, "updated_date": "2026-09-01",
    }
    row.update(changes)
    return row


def write_fixture(path, rows):
    with duckdb.connect() as con:
        con.register("fixture", pd.DataFrame(rows))
        con.execute("COPY fixture TO ? (FORMAT PARQUET)", [str(path)])


class SourceMetadataTests(unittest.TestCase):
    def test_latest_by_id_and_first_topic_with_identical_duplicate_audit(self):
        rows = [raw_source(updated_date="2026-02-01", display_name="Old title"), raw_source(), raw_source()]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.parquet"
            write_fixture(path, rows)
            latest, audit = read_latest_sources(path)
        self.assertEqual(list(latest.columns), list(LATEST_COLUMNS))
        self.assertEqual(latest.iloc[0].title, "Current title")
        self.assertEqual(latest.iloc[0].oa_field, "Economics")
        self.assertEqual(latest.iloc[0].publisher, "Current publisher")
        self.assertFalse(latest.iloc[0].is_open_access)
        self.assertEqual(audit["older_rows_discarded"], 1)
        self.assertEqual(audit["identical_latest_duplicates_collapsed"], 1)

    def test_latest_tied_conflicting_payload_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.parquet"
            write_fixture(path, [raw_source(), raw_source(type="book series")])
            with self.assertRaisesRegex(ValueError, "Conflicting latest"):
                read_latest_sources(path)

    def test_latest_uses_payload_timestamp_not_partition_directory_date(self):
        with tempfile.TemporaryDirectory() as directory:
            partition = Path(directory) / "updated_date=2026-09-01"
            partition.mkdir()
            path = partition / "sources.parquet"
            write_fixture(path, [
                raw_source(updated_date="2026-09-01 09:00:00", display_name="Morning title"),
                raw_source(updated_date="2026-09-01 17:00:00", display_name="Evening title"),
            ])
            latest, audit = read_latest_sources(path)
        self.assertEqual(latest.iloc[0].title, "Evening title")
        self.assertEqual(audit["older_rows_discarded"], 1)

    def test_missing_optional_metadata_remains_missing(self):
        row = raw_source(topics=[])
        for key in ("host_organization_name", "issn", "is_oa"):
            row.pop(key)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.parquet"
            write_fixture(path, [row])
            latest, audit = read_latest_sources(path)
        self.assertIsNone(latest.iloc[0].oa_field)
        self.assertEqual(latest.iloc[0].issns, ())
        self.assertTrue(pd.isna(latest.iloc[0].is_open_access))
        self.assertEqual(audit["optional_columns_missing"], ["publisher", "issns", "is_open_access"])

    def test_null_topic_and_issn_arrays_remain_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.parquet"
            write_fixture(path, [raw_source(topics=None, issn=None), raw_source(id="S2")])
            latest, _ = read_latest_sources(path)
        self.assertTrue(pd.isna(latest.iloc[0].oa_field))
        self.assertEqual(latest.iloc[0].issns, ())

    def test_missing_required_schema_and_invalid_vintage_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.parquet"
            row = raw_source()
            row.pop("type")
            write_fixture(path, [row])
            with self.assertRaisesRegex(ValueError, "required columns"):
                read_latest_sources(path)
            write_fixture(path, [raw_source(updated_date="unknown")])
            with self.assertRaisesRegex(ValueError, "invalid updated_date"):
                read_latest_sources(path)

    def test_canonical_metadata_wins_and_collision_remains_auditable(self):
        latest = pd.DataFrame([
            source("S1", A, title="Canonical title"),
            source("S2", B, source_type="book series", oa_field="History", title="Old title", is_open_access=True),
        ])
        effective, provenance, audit = project_sources(2022, latest, maps({B: A}))
        item = effective.iloc[0]
        self.assertEqual((item.issn_l, item.source_type, item.oa_field), (A, "journal", "Economics"))
        self.assertEqual((item.openalex_id, item.journal_title), ("S1", "Canonical title"))
        self.assertFalse(item.is_open_access)
        self.assertEqual(item.source_type_resolution, "canonical_arm")
        self.assertEqual(len(audit["identity_collisions"]), 1)
        self.assertEqual(set(provenance.issn_l), {A})
        self.assertEqual(set(provenance.score_year), {2022})
        self.assertEqual(set(item.issns), {A, B})

    def test_missing_canonical_attribute_uses_only_unanimous_component(self):
        latest = pd.DataFrame([source("S1", A, source_type=None, oa_field=" "), source("S2", B)])
        effective, _, _ = project_sources(2022, latest, maps({B: A}))
        self.assertEqual(effective.iloc[0].source_type, "journal")
        self.assertEqual(effective.iloc[0].source_type_resolution, "unanimous_component_fallback")
        self.assertEqual(effective.iloc[0].oa_field, "Economics")

    def test_unresolved_component_and_canonical_type_conflicts_exclude_oa(self):
        latest = pd.DataFrame([source("S1", A), source("S2", B, source_type="book series")])
        effective, _, audit = project_sources(2022, latest, maps({A: C, B: C}))
        self.assertEqual(effective.iloc[0].source_type, "Multiple source types")
        self.assertEqual(effective.iloc[0].source_type_resolution, "component_conflict")
        self.assertIsNone(effective.iloc[0].openalex_id)
        self.assertEqual(audit["oa_journal_nodes"], 0)
        latest.loc[1, "raw_issn_l"] = A
        effective, _, _ = project_sources(2022, latest, maps())
        self.assertEqual(effective.iloc[0].source_type_resolution, "canonical_arm_conflict")

    def test_source_override_precedes_quarantine_and_alias_then_key_correction(self):
        latest = pd.DataFrame([
            source("S1", None), source("S2", "1234-5678"), source("S3", None),
        ])
        effective, provenance, audit = project_sources(
            2022, latest, maps({B: A}, {"S1": SourceKeyOverride(("",), B)}, {A: C})
        )
        self.assertEqual(effective.issn_l.tolist(), [C])
        self.assertEqual(audit["quarantined_source_rows"], 2)
        self.assertEqual(provenance.loc[0, "corrected_source_issn_l"], B)
        self.assertEqual(provenance.loc[1, "quarantine_reason"], "invalid_issn_l")
        self.assertEqual(provenance.loc[2, "quarantine_reason"], "missing_issn_l")

    def test_approval_year_duplicate_ids_and_missing_override_source_are_guarded(self):
        latest = pd.DataFrame([source()])
        with self.assertRaisesRegex(ValueError, "reviewed annual"):
            project_sources(2026, latest, maps())
        with self.assertRaisesRegex(ValueError, "unique nonmissing"):
            project_sources(2022, pd.concat([latest, latest]), maps())
        with self.assertRaisesRegex(ValueError, "absent from Sources"):
            project_sources(2022, latest, maps(overrides={"absent": SourceKeyOverride((A,), A)}))
        with self.assertRaisesRegex(ValueError, "preimage changed"):
            project_sources(2022, latest, maps(overrides={"S1": SourceKeyOverride((B,), A)}))

    def test_empty_operational_projection_still_has_scorer_schema(self):
        effective, _, audit = project_sources(2022, pd.DataFrame([source(key=None)]), maps())
        self.assertTrue(effective.empty)
        self.assertTrue({"issn_l", "source_type", "journal_title"}.issubset(effective.columns))
        self.assertEqual(audit["quarantined_source_rows"], 1)

    def test_bare_export_id_preserves_source_url_in_provenance(self):
        latest = pd.DataFrame([source("https://openalex.org/S123", A)])
        effective, provenance, _ = project_sources(2022, latest, maps())
        exported = journal_export_view(effective)
        self.assertEqual(exported.iloc[0].openalex_id, "S123")
        self.assertEqual(exported.iloc[0].openalex_url, "https://openalex.org/S123")
        self.assertEqual(provenance.iloc[0].source_id, "https://openalex.org/S123")

    def test_export_rejects_missing_multiple_and_repeated_canonical_ids(self):
        for latest, mapping in (
            (pd.DataFrame([source("S1", A)]), maps({A: C})),
            (pd.DataFrame([source("S1", A), source("S2", A)]), maps()),
        ):
            effective, _, audit = project_sources(2022, latest, mapping)
            self.assertEqual(audit["journal_nodes_missing_unique_canonical_source"], 1)
            with self.assertRaisesRegex(ValueError, "exactly one canonical"):
                journal_export_view(effective)
        latest = pd.DataFrame([source("S1", A), source("https://openalex.org/S1", B)])
        effective, _, _ = project_sources(2022, latest, maps())
        with self.assertRaisesRegex(ValueError, "unique effective"):
            journal_export_view(effective)

    def test_manifest_resolves_without_scanning_payload_and_rejects_escape(self):
        manifest = {
            "date": "2026-09-23", "format": "parquet", "entity": "sources",
            "record_count": 1, "content_length": 10,
            "files": [{"url": "s3://openalex/data/parquet/sources/updated_date=2026-09-01/part.parquet",
                       "meta": {"content_length": 10, "record_count": 1}}],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sources"
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            files = source_paths_from_manifest(path, root, snapshot_date="2026-09-23")
            self.assertEqual(len(files), 1)
            self.assertFalse(files[0].exists())
            manifest["files"][0]["url"] = "s3://openalex/data/parquet/sources/../../outside.parquet"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid or repeated"):
                source_paths_from_manifest(path, root, snapshot_date="2026-09-23")

    def test_manifest_reader_binds_hash_sizes_and_listed_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sources"
            root.mkdir()
            parquet = root / "fixture.parquet"
            write_fixture(parquet, [raw_source()])
            size = parquet.stat().st_size
            manifest = {
                "date": "2026-09-23", "format": "parquet", "entity": "sources",
                "record_count": 1, "content_length": size,
                "files": [{"url": "s3://openalex/data/parquet/sources/fixture.parquet",
                           "meta": {"content_length": size, "record_count": 1}}],
            }
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            latest, audit = read_manifest_sources(path, root, snapshot_date="2026-09-23", expected_manifest_sha256=digest)
            self.assertEqual(len(latest), 1)
            self.assertEqual(audit["manifest_sha256"], digest)
            with self.assertRaisesRegex(ValueError, "manifest hash"):
                read_manifest_sources(path, root, snapshot_date="2026-09-23", expected_manifest_sha256="wrong")
            manifest["content_length"] += 1
            manifest["files"][0]["meta"]["content_length"] += 1
            path.write_text(json.dumps(manifest), encoding="utf-8")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, "file size"):
                read_manifest_sources(path, root, snapshot_date="2026-09-23", expected_manifest_sha256=digest)


if __name__ == "__main__":
    unittest.main()
