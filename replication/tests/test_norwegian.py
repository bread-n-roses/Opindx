"""Synthetic annual membership checks; no local register or graph is read."""

import csv
import io
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from norwegian import BASE_COLUMNS, DEFAULT_YEARS, build_annual_rosters, normalize_issn


COLUMNS = [*BASE_COLUMNS, *(f"Level {year}" for year in DEFAULT_YEARS)]


def record(**changes):
    value = {
        "journal_id": "0007",
        "Original Title": "Example journal",
        "International Title": "Example journal (international)",
        "Print ISSN": " 1234 567x ",
        "Online ISSN": "2049-3630",
        "NPI Academic Discipline": "Social Science",
        "NPI Scientific Field": "Economics",
        "Series": "0",
        "Ceased": "",
        **{f"Level {year}": "1" for year in DEFAULT_YEARS},
    }
    value.update(changes)
    return value


def csv_text(rows, columns=COLUMNS):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, delimiter=";", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def reader(rows, columns=COLUMNS):
    return csv.DictReader(io.StringIO(csv_text(rows, columns)), delimiter=";")


class NorwegianMembershipTests(unittest.TestCase):
    def test_inclusive_cessation_and_each_year_own_level(self):
        result = build_annual_rosters(reader([
            record(**{"Ceased": "2024", "Level 2022": "0", "Level 2023": "2"})
        ]))
        self.assertEqual([(item.score_year, item.norwegian_level) for item in result], [(2023, 2), (2024, 1)])
        self.assertEqual(result[-1].ceased_year, 2024)

    def test_eligibility_series_and_levels(self):
        rows = [
            record(journal_id="journal", Series="0"),
            record(journal_id="series", Series="1"),
            record(journal_id="other", Series="2"),
            record(journal_id="under-review", **{"Level 2022": "X"}),
            record(journal_id="missing-level", **{"Level 2022": ""}),
        ]
        result = build_annual_rosters(reader(rows), years=[2022])
        self.assertEqual([item.journal_id for item in result], ["journal", "series"])

    def test_preserves_ids_metadata_and_normalizes_issns(self):
        item, = build_annual_rosters(reader([record()]), years=[2026])
        self.assertEqual(item.journal_id, "0007")
        self.assertEqual(item.print_issn, "1234-567X")
        self.assertEqual(item.online_issn, "2049-3630")
        self.assertEqual((item.discipline, item.field), ("Social Science", "Economics"))
        self.assertEqual(item.original_title, "Example journal")
        self.assertIsNone(item.ceased_year)

    def test_blank_identifiers_do_not_remove_membership(self):
        item, = build_annual_rosters(reader([record(**{"Print ISSN": "", "Online ISSN": " "})]), [2022])
        self.assertEqual((item.print_issn, item.online_issn), ("", ""))

    def test_identical_duplicates_collapse_but_conflicts_fail(self):
        self.assertEqual(len(build_annual_rosters(reader([record(), record()]), [2022])), 1)
        with self.assertRaisesRegex(ValueError, "Ambiguous duplicate.*2022"):
            build_annual_rosters(reader([record(), record(**{"Level 2022": "2"})]), [2022])
        with self.assertRaisesRegex(ValueError, "Ambiguous duplicate"):
            build_annual_rosters(reader([record(), record(**{"NPI Scientific Field": "History"})]), [2022])
        with self.assertRaisesRegex(ValueError, "Ambiguous duplicate"):
            build_annual_rosters(reader([record(), record(**{"Level 2022": "0"})]), [2022])

    def test_path_input_accepts_bom_and_historical_years(self):
        columns = [*BASE_COLUMNS, "Level 2004"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "register.csv"
            path.write_text(csv_text([record(**{"Level 2004": "2"})], columns), encoding="utf-8-sig")
            item, = build_annual_rosters(path, [2004])
        self.assertEqual((item.score_year, item.norwegian_level), (2004, 2))

    def test_missing_required_header_fails_even_with_no_rows(self):
        for missing in ("journal_id", "Ceased", "Level 2022"):
            with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, "missing required columns"):
                build_annual_rosters(reader([], [name for name in COLUMNS if name != missing]), [2022])

    def test_invalid_headers_and_row_width_fail(self):
        for text in ("journal_id;journal_id\n", "\n", ";journal_id\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                build_annual_rosters(csv.DictReader(io.StringIO(text), delimiter=";"), [2022])
        header = ";".join(COLUMNS)
        for data in ("too;short", ";".join(["x"] * (len(COLUMNS) + 1))):
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, "missing or excess cells"):
                build_annual_rosters(csv.DictReader(io.StringIO(header + "\n" + data), delimiter=";"), [2022])

    def test_invalid_required_values_fail(self):
        for changes in ({"journal_id": " "}, {"Ceased": "unknown"}, {"Print ISSN": "not-an-issn"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                build_annual_rosters(reader([record(**changes)]), [2022])

    def test_score_year_validation_and_deterministic_order(self):
        for years in ([], [2022, 2022], [True], ["2022"], [1899]):
            with self.subTest(years=years), self.assertRaises(ValueError):
                build_annual_rosters(reader([]), years)
        result = build_annual_rosters(reader([record(journal_id="b"), record(journal_id="a")]), [2023, 2022])
        self.assertEqual([(item.score_year, item.journal_id) for item in result], [(2022, "a"), (2022, "b"), (2023, "a"), (2023, "b")])

    def test_issn_normalization_rejects_syntax_without_checksum_policy(self):
        self.assertEqual(normalize_issn("1234\u2011567x"), "1234-567X")
        self.assertEqual(normalize_issn("12345678"), "1234-5678")
        with self.assertRaises(ValueError):
            normalize_issn("1234--5678")


if __name__ == "__main__":
    unittest.main()
