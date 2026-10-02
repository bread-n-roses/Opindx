"""Exact snapshot-row retrieval must not drift to a neighboring Work."""

import sys
import tempfile
import unittest
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from targeted_work_metadata import metadata_for_rows  # noqa: E402


class TargetedWorkMetadataTests(unittest.TestCase):
    def test_exact_row_group_lookup_and_source_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "works.parquet"
            source = {"id": "S1", "display_name": "Journal A", "issn_l": "1234-5679"}
            records = [
                {"id": "W1", "doi": "https://doi.org/10.1/one", "title": "One",
                 "primary_location": {"source": source, "raw_source_name": "Journal A"}},
                {"id": "W2", "doi": "https://doi.org/10.1/two", "title": "Two",
                 "primary_location": {"source": source, "raw_source_name": "Journal A"}},
            ]
            pq.write_table(pa.Table.from_pylist(records), path, row_group_size=1)
            selected = [{"work_id": "W2", "source_id": "S1",
                         "raw_issn_l": "1234-5679", "year": 2024,
                         "source_file": path.as_posix(), "source_row": 1}]
            result = metadata_for_rows(selected)
            self.assertEqual(result[0]["doi"], "https://doi.org/10.1/two")
            self.assertEqual(result[0]["source_display_name"], "Journal A")
            selected[0]["source_row"] = 0
            with self.assertRaisesRegex(ValueError, "Snapshot row changed"):
                metadata_for_rows(selected)


if __name__ == "__main__":
    unittest.main()
