import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("builder", ROOT / "tools/build_site_data.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
spec = importlib.util.spec_from_file_location("versions", ROOT / "tools/version_site_assets.py")
versions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(versions)


class SiteBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / "runs/2026-Q3"
        self.run.mkdir(parents=True)
        self.data = self.root / "site/data"
        pins = self.root / "score-years.json"
        pins.write_text('{"2024":"2026-Q3"}')
        self.patch = patch.multiple(builder, SITE_DATA=self.data, SCORE_YEARS=pins)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.manifest = {"run":"2026-Q3", "created":"2026-10-02", "years":[2024,2025],
                         "universes":{"n":"Norwegian Register","oa":"OpenAlex"},
                         "schema_version":2, "openalex_snapshot":"2026-09-23", "assets":{}}
        for year in self.manifest["years"]:
            columns = {c:["",""] for c in builder.COLUMNS}
            columns.update(openalex_id=["S101","S102"], score_year=[year,year],
                           is_open_access=[True,False], in_n=[True,False], in_oa=[True,True],
                           publications_raw=[0,4], publications_filtered=[0,3],
                           citations_raw=[0,8], citations_filtered=[0,7],
                           reference_coverage_pct=[None,75.0], active_years=[0,4])
            for universe in self.manifest["universes"]:
                for metric in ("share","per_article"):
                    for treatment in ("raw","filtered"):
                        columns[f"{metric}_{universe}_{treatment}"] = (
                            [0.0,1.5 if universe == "oa" else None] if metric == "share" else [None,2.0 if universe == "oa" else None])
            columns.update(oa_field_modal_share=[None,1.0], oa_field_modal_works=[0,3],
                           oa_field_classified_works=[0,3], oa_field_classification_coverage=[None,.75],
                           oa_field_tied_modes=[0,1], oa_field_other_works=[0,0],
                           norwegian_entries_json=["",""], norwegian_primary_id=["",""])
            for i in range(1,4):
                columns[f"oa_field_{i}"] = ["","Medicine" if i == 1 else ""]
                columns[f"oa_field_{i}_works"] = [0,3 if i == 1 else 0]
            self.save(year, pa.table(columns))

    def save(self, year, table):
        path = self.run / f"scores_{year}.parquet"
        pq.write_table(table, path)
        self.manifest["assets"][path.name] = {"sha256":builder.digest(path), "bytes":path.stat().st_size, "rows":len(table)}
        (self.run / "manifest.json").write_text(json.dumps(self.manifest))

    def test_builds_frozen_live_history_and_compact_ranks(self):
        builder.main(self.run.parent, "2026-Q3", "https://example.org/releases")
        index = json.loads((self.data / "index.json").read_text())
        self.assertEqual([(y["year"],y["status"]) for y in index["years"]], [(2025,"live"),(2024,"frozen")])
        self.assertNotIn("local_preview", index["journal_details"])
        history = pq.read_table(self.data / "history/02.parquet")
        self.assertEqual(history["oa_field_1"].to_pylist(), ["Medicine","Medicine"])
        self.assertEqual(history["citations_filtered"].to_pylist(), [7,7])
        ranks = pq.read_table(self.data / "ranks/2025-n-per_article-filtered.parquet")
        self.assertEqual(ranks["openalex_id"].to_pylist(), ["S101"])
        self.assertEqual(ranks["per_article_n_filtered"].to_pylist(), [None])
        self.assertNotIn("title", ranks.column_names)
        self.assertEqual(builder.digest(self.data / "scores_2025.parquet"), builder.digest(self.run / "scores_2025.parquet"))

    def test_corrupt_release_preserves_previous_site(self):
        self.data.mkdir(parents=True)
        (self.data / "marker").write_text("old site")
        with (self.run / "scores_2025.parquet").open("ab") as stream:
            stream.write(b"broken")
        with self.assertRaises(Exception):
            builder.main(self.run.parent, "2026-Q3")
        self.assertEqual((self.data / "marker").read_text(), "old site")

    def test_all_missing_row_rejected_even_with_matching_hash(self):
        table = pq.read_table(self.run / "scores_2025.parquet")
        for name in table.column_names:
            if name.startswith(("share_","per_article_")):
                values = table[name].to_pylist()
                values[0] = None
                table = table.set_column(table.schema.get_field_index(name), name, pa.array(values, type=pa.float64()))
        self.save(2025, table)
        with self.assertRaisesRegex(ValueError, "at least one metric"):
            builder.main(self.run.parent, "2026-Q3")

    def test_all_html_styles_and_local_imports_share_commit_version(self):
        site = self.root / "assets"
        site.mkdir()
        (site / "journal-help.html").write_text('<link href="styles.css">')
        (site / "app.js").write_text("import * as D from './journal-details.js';")
        (site / "journal-details.js").write_text("import * as E from './engine.js';")
        versions.version_assets(site, "abcdef0")
        self.assertIn('styles.css?v=abcdef0', (site / "journal-help.html").read_text())
        self.assertIn('./journal-details.js?v=abcdef0', (site / "app.js").read_text())
        self.assertIn('./engine.js?v=abcdef0', (site / "journal-details.js").read_text())


if __name__ == "__main__":
    unittest.main()
