"""Convert an existing website CSV into local Opindx release files.

Adapted from Tabo Weitzel's tools/import_run.py in bread-n-roses/Opindx,
commit 0fedb4a989f092ddf61899130998ffaef5c8e2cd (30 September 2026 review):
https://github.com/bread-n-roses/Opindx/blob/0fedb4a989f092ddf61899130998ffaef5c8e2cd/tools/import_run.py

The upstream column mapping, membership convention and manifest format are kept.
This integration adds workspace-relative defaults, CLI arguments, input checks,
round-trip float parsing, and verified/repeatable local output. It does not
calculate scores, change the website, or make network/Git calls.

Called automatically by combine_repaired_handoff.py --execute. --check can
also inspect an existing CSV without writing.
Needs pandas and pyarrow, as does the original importer.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import tempfile

import pandas as pd


WEBSITE_RUNS = Path(__file__).resolve().parents[1]
DEFAULT_CSV = WEBSITE_RUNS / "2026-09-23/exports/source-repair-v1/combined/journal-data-2022-2026.csv"
DEFAULT_OUTPUT = WEBSITE_RUNS / "2026-09-23/exports/opindx"
DEFAULT_RUN = "2026-09-23-source-repair-v1"

# Same public columns and score mapping as the original Opindx importer.
UNIVERSES = {"n": "Norwegian Register", "oa": "OpenAlex"}
RENAME = {
    "journal_title": "title",
    "eligible_publications_raw": "publications_raw",
    "eligible_publications_filtered": "publications_filtered",
    "incoming_citations_raw": "citations_raw",
    "incoming_citations_filtered": "citations_filtered",
    "active_publication_years_of_5": "active_years",
}
SCORE_COLUMNS = []
for u in UNIVERSES:
    for treatment in ("raw", "filtered"):
        RENAME[f"ef_{u}_{treatment}"] = f"share_{u}_{treatment}"
        RENAME[f"ais_{u}_{treatment}"] = f"per_article_{u}_{treatment}"
        SCORE_COLUMNS += [f"share_{u}_{treatment}", f"per_article_{u}_{treatment}"]

TEXT_COLUMNS = ["openalex_id", "title", "publisher", "issn_l", "issns", "oa_domain", "oa_field",
                "norwegian_area", "norwegian_field", "norwegian_level", "norwegian_register_url"]
INTEGER_COLUMNS = ["score_year", "publications_raw", "publications_filtered", "citations_raw",
                   "citations_filtered", "active_years"]
COLUMNS = [*TEXT_COLUMNS, "is_open_access", "score_year", "publications_raw", "publications_filtered",
           "citations_raw", "citations_filtered", "reference_coverage_pct", "active_years",
           *[f"in_{u}" for u in UNIVERSES], *SCORE_COLUMNS]
SNAPSHOT_COLUMNS = ["oa_snapshot_version", "norwegian_register_snapshot"]
REVERSE_NAMES = {value: key for key, value in RENAME.items()}
INPUT_COLUMNS = [REVERSE_NAMES.get(c, c) for c in COLUMNS if c not in ("in_n", "in_oa")] + SNAPSHOT_COLUMNS


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def one_value(frame: pd.DataFrame, column: str) -> str:
    values = frame[column].dropna().unique()
    if frame[column].isna().any() or len(values) != 1:
        raise ValueError(f"{column}: expected one nonmissing snapshot date for the whole CSV")
    value = str(values[0])
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError(f"{column}: expected YYYY-MM-DD")
    return value


def read_input(csv_file: Path) -> tuple[pd.DataFrame, dict, dict | None]:
    """Check any adjacent pipeline receipt, then read only the public columns."""
    csv_file = Path(csv_file).resolve(strict=True)
    receipt_path = csv_file.with_suffix(".receipt.json")
    receipt = None
    input_hash = sha256(csv_file)
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        bound = receipt.get("output", {})
        if (receipt.get("status") != "PASS" or not receipt.get("csv_all_cells_roundtrip_verified")
                or bound.get("sha256") != input_hash or bound.get("bytes") != csv_file.stat().st_size):
            raise ValueError("CSV does not match its PASS pipeline receipt")
    header = pd.read_csv(csv_file, encoding="utf-8-sig", nrows=0)
    missing = sorted(set(INPUT_COLUMNS) - set(header.columns))
    if missing:
        raise ValueError(f"CSV is missing required columns: {', '.join(missing)}")
    types = {REVERSE_NAMES.get(c, c): "string" for c in TEXT_COLUMNS + SNAPSHOT_COLUMNS}
    types.update({REVERSE_NAMES.get(c, c): "Int64" for c in INTEGER_COLUMNS})
    types.update({REVERSE_NAMES.get(c, c): "float64" for c in SCORE_COLUMNS + ["reference_coverage_pct"]})
    types["is_open_access"] = "boolean"
    frame = pd.read_csv(csv_file, encoding="utf-8-sig", usecols=INPUT_COLUMNS, dtype=types,
                        keep_default_na=False, na_values=[""], float_precision="round_trip",
                        low_memory=False).rename(columns=RENAME)
    if frame.empty:
        raise ValueError("CSV has no journal rows")
    if frame["openalex_id"].isna().any() or not frame["openalex_id"].str.fullmatch(r"S[0-9]+").all():
        raise ValueError("Every row must have an OpenAlex source ID such as S12345")
    if frame["score_year"].isna().any() or not frame["score_year"].between(1, 9999).all():
        raise ValueError("Every row must have an integer score year")
    if frame.duplicated(["openalex_id", "score_year"]).any():
        raise ValueError("CSV has duplicate (openalex_id, score_year) rows")
    snapshots = {c: one_value(frame, c) for c in SNAPSHOT_COLUMNS}
    # Upstream convention; the dated pipeline checks annual URL/membership parity.
    frame["in_n"] = frame["norwegian_register_url"].notna()
    frame["in_oa"] = True
    if receipt:
        rows_by_year = {str(int(y)): int(n) for y, n in frame.groupby("score_year").size().items()}
        if (receipt.get("rows") != len(frame) or receipt.get("rows_by_year") != rows_by_year
                or any(receipt.get(c) != snapshots[c] for c in SNAPSHOT_COLUMNS)):
            raise ValueError("CSV rows or snapshot dates disagree with the pipeline receipt")
        for annual in receipt.get("annual", []):
            actual = int(frame.loc[frame["score_year"] == annual["score_year"], "in_n"].sum())
            if actual != annual["norwegian_members"]:
                raise ValueError("Norwegian membership disagrees with the pipeline receipt")
    source = {"sha256": input_hash, "bytes": csv_file.stat().st_size, "rows": len(frame), **snapshots}
    return frame[COLUMNS], source, receipt


FIELD_COLUMNS = ["oa_field_modal_share", "oa_field_modal_works", "oa_field_classified_works",
                 "oa_field_classification_coverage", "oa_field_tied_modes"]


def attach_fields(frame: pd.DataFrame, source: dict, path: Path) -> tuple[pd.DataFrame, dict]:
    """Apply a complete, hash-bound annual field sidecar without touching scores."""
    path = Path(path)
    receipt = json.loads(path.with_suffix(".receipt.json").read_text(encoding="utf-8"))
    if (receipt.get("status") != "PASS" or receipt.get("source_csv_sha256") != source["sha256"]
            or receipt.get("output_sha256") != sha256(path)):
        raise ValueError("Field sidecar does not match its PASS receipt and source CSV")
    fields = pd.read_parquet(path)
    keys = ["openalex_id", "score_year", "issn_l"]
    required = keys + ["oa_field", "oa_domain"] + FIELD_COLUMNS
    if set(fields.columns) != set(required) or fields.duplicated(keys).any() or len(fields) != len(frame):
        raise ValueError("Field sidecar has incomplete schema or duplicate/missing rows")
    fields = fields.astype({key: frame[key].dtype for key in keys})
    result = frame.drop(columns=["oa_field", "oa_domain"]).merge(fields, on=keys, how="left", validate="one_to_one", indicator=True, sort=False)
    if not result.pop("_merge").eq("both").all() or result[["oa_field", "oa_domain"]].isna().any().any():
        raise ValueError("Field sidecar does not cover every journal-year identity")
    known = result.oa_field.ne("Unknown")
    modal, classified, total = result.oa_field_modal_works, result.oa_field_classified_works, result.publications_raw
    if (classified.isna().any() or modal.isna().any() or total.isna().any()
            or (modal < 0).any() or (classified < modal).any() or (classified > total).any()
            or not classified.gt(0).eq(known).all() or not modal.gt(0).eq(known).all()):
        raise ValueError("Invalid rolling field vote counts")
    for column, expected in (
        ("oa_field_modal_share", modal / classified.replace(0, float("nan"))),
        ("oa_field_classification_coverage", classified / total.replace(0, float("nan"))),
    ):
        pd.testing.assert_series_equal(result[column].astype(float), expected.astype(float), check_names=False, check_exact=True)
    count_columns = ["oa_field_modal_works", "oa_field_classified_works", "oa_field_tied_modes"]
    if any(result[c].isna().any() or result[c].lt(0).any() or result[c].mod(1).ne(0).any() for c in count_columns):
        raise ValueError("Field counts must be nonnegative integers")
    if not result.oa_field_tied_modes.ge(1).eq(known).all():
        raise ValueError("Invalid tied-mode count")
    unchanged = [c for c in COLUMNS if c not in ("oa_field", "oa_domain")]
    pd.testing.assert_frame_equal(result[unchanged], frame[unchanged].reset_index(drop=True), check_exact=True)
    return result[COLUMNS + FIELD_COLUMNS], {**receipt["method"], "sidecar_sha256": receipt["output_sha256"],
                                            "receipt_sha256": sha256(path.with_suffix(".receipt.json"))}


def verify_output(folder: Path, frame: pd.DataFrame, manifest: dict) -> None:
    """Verify all converted cells, including zeroes, nulls and text identifiers."""
    actual_manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    for key, value in manifest.items():
        if key != "created" and actual_manifest.get(key) != value:
            raise ValueError(f"Existing output differs: manifest {key}")
    expected_files = {"manifest.json", *[f"scores_{y}.parquet" for y in manifest["years"]]}
    if {p.name for p in folder.iterdir()} != expected_files:
        raise ValueError("Output folder has missing or unexpected files")
    for year in manifest["years"]:
        expected = frame.loc[frame["score_year"] == year, list(frame.columns)].reset_index(drop=True)
        actual = pd.read_parquet(folder / f"scores_{year}.parquet")
        pd.testing.assert_frame_equal(actual, expected, check_dtype=False, check_exact=True)


def import_run(csv_file: Path | str, output_location: Path | str, run_name: str,
               *, check_only: bool = False, fields: Path | str | None = None) -> Path:
    """Keep the upstream callable signature; write only a complete local run."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_name):
        raise ValueError("Run name must be a single folder/tag name, using letters, digits, '.', '_' or '-'")
    frame, source, receipt = read_input(Path(csv_file))
    field_method = None
    if fields is not None:
        frame, field_method = attach_fields(frame, source, Path(fields))
    years = sorted(int(y) for y in frame["score_year"].unique())
    manifest = {"run": run_name, "created": date.today().isoformat(), "dummy": False,
                "openalex_snapshot": source["oa_snapshot_version"],
                "norwegian_register_snapshot": source["norwegian_register_snapshot"],
                "years": years, "universes": UNIVERSES}
    if field_method is not None:
        manifest["field_assignment"] = field_method
        manifest["source_csv_sha256"] = source["sha256"]
    output = Path(output_location).resolve()
    destination = output / run_name
    print(f"Input: {Path(csv_file).resolve()}")
    print(f"Pipeline receipt: {'PASS, hash verified' if receipt else 'not present; conversion checks only'}")
    for year in years:
        rows = frame[frame["score_year"] == year]
        print(f"  {year}: {len(rows):,} journals, {int(rows['in_n'].sum()):,} Norwegian Register members")
    print(f"Local output: {destination}")
    if check_only:
        print("Input checks passed. No files written.")
        return destination
    if destination.exists():
        verify_output(destination, frame, manifest)
        if sha256(Path(csv_file)) != source["sha256"]:
            raise ValueError("Input CSV changed during output verification")
        print("Existing local files match the input; kept unchanged.")
        return destination
    output.mkdir(parents=True, exist_ok=True)
    # All conversion/checking occurs in a temporary folder on the same volume.
    # A failed import leaves no incomplete run under the final run name.
    with tempfile.TemporaryDirectory(prefix=".opindx-import-", dir=output) as temporary:
        staged = Path(temporary) / run_name
        staged.mkdir()
        for year in years:
            frame.loc[frame["score_year"] == year, list(frame.columns)].to_parquet(staged / f"scores_{year}.parquet", index=False)
        (staged / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        verify_output(staged, frame, manifest)
        if sha256(Path(csv_file)) != source["sha256"]:
            raise ValueError("Input CSV changed during conversion; no run installed")
        if destination.exists():
            raise FileExistsError(f"Output appeared during conversion: {destination}")
        staged.rename(destination)
    print("Created and verified local manifest/Parquet files. Nothing uploaded.")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Existing website CSV")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Parent directory for the local run")
    parser.add_argument("--run-name", default=DEFAULT_RUN, help="A new release/run name, never the existing 2026-Q3")
    parser.add_argument("--fields", type=Path, help="Verified annual rolling-field Parquet sidecar")
    parser.add_argument("--check", action="store_true", help="Validate the input and show output paths without writing")
    args = parser.parse_args()
    try:
        import_run(args.csv, args.output, args.run_name, check_only=args.check, fields=args.fields)
    except (ValueError, OSError, AssertionError) as error:
        parser.exit(1, f"Import stopped: {error}\n")


if __name__ == "__main__":
    main()
