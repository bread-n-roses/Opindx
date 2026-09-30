"""Turn a CSV from the back-end pipeline into a data run in the layout of SPEC.md.

Set the three values at the bottom of this file and run it (in VS Code: the Run button).
The CSV has one row per journal and score year. Needs pandas and pyarrow.
Stops with an error if a column is missing or a snapshot date is not the same for the whole file.
"""
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

UNIVERSES = {"n": "Norwegian Register", "oa": "OpenAlex"}
# CSV column -> column in the published data (see SPEC.md).
RENAME = {"journal_title": "title",
          "eligible_publications_raw": "publications_raw",
          "eligible_publications_filtered": "publications_filtered",
          "incoming_citations_raw": "citations_raw",
          "incoming_citations_filtered": "citations_filtered",
          "active_publication_years_of_5": "active_years"}
SCORE_COLUMNS = []  # the score columns per universe, in the order they are written
for u in UNIVERSES:
    for treatment in ("raw", "filtered"):
        RENAME[f"ef_{u}_{treatment}"] = f"share_{u}_{treatment}"
        RENAME[f"ais_{u}_{treatment}"] = f"per_article_{u}_{treatment}"
        SCORE_COLUMNS += [f"share_{u}_{treatment}", f"per_article_{u}_{treatment}"]
COLUMNS = ["openalex_id", "title", "publisher", "issn_l", "issns", "oa_domain", "oa_field", "norwegian_area",
           "norwegian_field", "norwegian_level", "norwegian_register_url", "is_open_access", "score_year",
           "publications_raw", "publications_filtered", "citations_raw", "citations_filtered",
           "reference_coverage_pct", "active_years", *[f"in_{u}" for u in UNIVERSES], *SCORE_COLUMNS]


def one_value(df, column):
    """A run has one OpenAlex and one register snapshot, so these columns hold a single date."""
    values = df[column].dropna().unique()
    if len(values) != 1:
        sys.exit(f"{column}: expected one value for the whole run, found {len(values)}")
    return str(values[0])


def import_run(csv_file, output_location, run_name):
    if not Path(csv_file).is_file():
        sys.exit(f"Cannot find the CSV: {csv_file}\nSet file_path at the bottom of this script.")
    # norwegian_level is text: a journal with two register entries has a value like "1 | 2".
    df = pd.read_csv(csv_file, encoding="utf-8-sig", dtype={"norwegian_level": "string"}, low_memory=False)
    if "oa_field_modal_share" in df.columns:
        sys.exit("Rolling-field CSV detected: use the integrated AIS Opindx exporter to preserve field-method provenance and shares.")
    df = df.rename(columns=RENAME)
    text = df.columns[df.dtypes == "object"]
    df[text] = df[text].replace("", None)  # an empty cell is a missing value, not empty text
    df["norwegian_level"] = df["norwegian_level"].replace("", None)
    # A journal is in the Norwegian Register universe when the run gives it a register entry;
    # every row in the export is a journal in OpenAlex.
    df["in_n"] = df["norwegian_register_url"].notna()
    df["in_oa"] = True
    missing = [column for column in COLUMNS if column not in df.columns]
    if missing:
        sys.exit(f"{csv_file}: missing columns {missing}")

    out = Path(output_location) / run_name
    out.mkdir(parents=True, exist_ok=True)
    years = sorted(df["score_year"].unique())
    for year in years:
        rows = df[df["score_year"] == year]
        rows[COLUMNS].to_parquet(out / f"scores_{year}.parquet", index=False)
        print(f"  {year}: {len(rows):,} journals, " +
              ", ".join(f"{rows[f'in_{u}'].sum():,} in {name}" for u, name in UNIVERSES.items()))
    manifest = {"run": run_name, "created": date.today().isoformat(), "dummy": False,
                "openalex_snapshot": one_value(df, "oa_snapshot_version"),
                "norwegian_register_snapshot": one_value(df, "norwegian_register_snapshot"),
                "years": [int(year) for year in years], "universes": UNIVERSES}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Wrote {out}")


# ---------------------------------------------------------------------------
# Set these three values, then run this file.
# ---------------------------------------------------------------------------

file_path = r"C:\path\to\journal-data-2023-2025.csv"  # the CSV the pipeline wrote
output_location = r"C:\path\to\export"                # the folder to write the run into
run_name = "2026-Q3"                                  # the name of this run, and the release tag on GitHub

if __name__ == "__main__":
    import_run(file_path, output_location, run_name)
