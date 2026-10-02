"""Preparation must not invalidate timestamp-bound checkpoint provenance."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from opindx_replication import pipeline


class PreparationResumeTest(unittest.TestCase):
    def test_repeat_preparation_preserves_all_unchanged_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            snapshot = root / "snapshot"
            register = root / "register.csv"
            register.write_text("public,register\n", encoding="utf-8")
            manifests = {}
            for entity in ("works", "sources"):
                p = snapshot / entity / "manifest.json"
                p.parent.mkdir(parents=True)
                p.write_text(json.dumps({"files": [], "content_length": 0}), encoding="utf-8")
                manifests[entity] = pipeline.digest(p)
            profile = {"snapshot_date": "2026-09-23", "years": [2022, 2023, 2024, 2025, 2026],
                       "norwegian_sha256": pipeline.digest(register), "manifests": manifests}
            work = root / "work"
            with patch.object(pipeline, "profile", return_value=profile), \
                 patch.object(pipeline.extract, "_snapshot"), \
                 patch.object(pipeline.source_checkpoint, "_binding"):
                pipeline.prepare(snapshot, register, work)
                paths = [p for p in work.rglob("*") if p.is_file()]
                for p in paths:
                    os.utime(p, ns=(1700000000000000000, 1700000000000000000))
                before = {p: (pipeline.digest(p), p.stat().st_mtime_ns) for p in paths}
                pipeline.prepare(snapshot, register, work)
                self.assertEqual(before, {p: (pipeline.digest(p), p.stat().st_mtime_ns) for p in paths})


if __name__ == "__main__":
    unittest.main()
