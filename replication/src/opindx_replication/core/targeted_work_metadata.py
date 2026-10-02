"""Retrieve reviewed Works metadata from the exact rows in a dated checkpoint.

The full Works extraction records the snapshot filename and zero-based row
number chosen for every work. This reader opens only the row groups containing
the selected rows. It never substitutes a live API record for snapshot data.
The output is an ignored local review cache, not a scoring input by itself.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq


RUN = Path(__file__).resolve().parents[1]
SOURCE_IDS = (
    "https://openalex.org/S207178839",  # JGR: Atmospheres / parent JGR
    "https://openalex.org/S4210169982",  # Eurasian Business/Economic Review
)
READ_COLUMNS = (
    "id", "doi", "title", "primary_location.source.id",
    "primary_location.source.display_name", "primary_location.source.issn_l",
    "primary_location.raw_source_name",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _receipt_hash(path: Path) -> str:
    receipt = path.with_suffix(".receipt.json")
    evidence = json.loads(receipt.read_text(encoding="utf-8"))
    if evidence["bytes"] != path.stat().st_size:
        raise ValueError(f"Changed checkpoint size: {path}")
    return evidence["sha256"]


def selected_rows(run: Path, source_ids: tuple[str, ...] = SOURCE_IDS) -> list[dict[str, Any]]:
    """Use the dedup choice, then check selected IDs against the Works checkpoint."""
    if not source_ids or len(set(source_ids)) != len(source_ids):
        raise ValueError("Supply distinct source IDs")
    root = run / "intermediates" / "extraction"
    raw, choice, works = (root / name for name in (
        "work_extract_raw.parquet", "dedup_choice.parquet", "works.parquet"))
    for path in (raw, choice, works):
        _receipt_hash(path)
    with duckdb.connect() as con:
        rows = con.execute("""
            SELECT r.work_id, r.source_id, r.raw_issn_l, r.year,
                   r.source_file, r.source_row
            FROM read_parquet(?) r
            JOIN read_parquet(?) d USING (work_id, source_file, source_row)
            WHERE r.source_id IN (SELECT unnest(?::VARCHAR[]))
            ORDER BY r.source_file, r.source_row
        """, [str(raw), str(choice), list(source_ids)]).fetchall()
        expected = con.execute("""
            SELECT source_id, count(*) FROM read_parquet(?)
            WHERE source_id IN (SELECT unnest(?::VARCHAR[])) GROUP BY source_id
        """, [str(works), list(source_ids)]).fetchall()
    selected = [dict(zip(("work_id", "source_id", "raw_issn_l", "year",
                          "source_file", "source_row"), row)) for row in rows]
    actual = {source: sum(row["source_id"] == source for row in selected)
              for source in source_ids}
    expected_by_id = dict(expected)
    if actual != {source: expected_by_id.get(source, 0) for source in source_ids}:
        raise ValueError(f"Dedup/raw source totals differ from Works: {actual} vs {expected}")
    if len({row["work_id"] for row in selected}) != len(selected):
        raise ValueError("Selected Works contain duplicate work IDs")
    return selected


def metadata_for_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Read exactly the selected row groups and verify both Work and Source IDs."""
    by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_file[row["source_file"]].append(row)
    result: list[dict[str, Any]] = []
    for filename, items in sorted(by_file.items()):
        parquet = pq.ParquetFile(filename)
        by_group: dict[int, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
        boundaries = [0]
        for group in range(parquet.num_row_groups):
            boundaries.append(boundaries[-1] + parquet.metadata.row_group(group).num_rows)
        for item in items:
            position = item["source_row"]
            if not isinstance(position, int) or position < 0 or position >= boundaries[-1]:
                raise ValueError(f"Row number outside snapshot file: {filename}:{position}")
            # A few row groups per file; a linear search avoids a second index.
            group = next(g for g in range(parquet.num_row_groups)
                         if boundaries[g] <= position < boundaries[g + 1])
            by_group[group].append((position - boundaries[group], item))
        for group, chosen in sorted(by_group.items()):
            table = parquet.read_row_group(group, columns=list(READ_COLUMNS))
            for (_, item), record in zip(chosen, table.take(
                    pa.array([offset for offset, _ in chosen], type=pa.int64())).to_pylist()):
                location = record.get("primary_location") or {}
                source = location.get("source") or {}
                if record["id"] != item["work_id"] or source.get("id") != item["source_id"]:
                    raise ValueError(f"Snapshot row changed: {filename}:{item['source_row']}")
                if source.get("issn_l") != item["raw_issn_l"]:
                    raise ValueError(f"Snapshot ISSN-L changed: {filename}:{item['source_row']}")
                result.append({**item, "doi": record.get("doi"),
                               "title": record.get("title"),
                               "raw_source_name": location.get("raw_source_name"),
                               "source_display_name": source.get("display_name")})
    if len(result) != len(rows):
        raise AssertionError("Selected metadata count changed")
    return sorted(result, key=lambda row: row["work_id"])


def extract(run: Path = RUN, source_ids: tuple[str, ...] = SOURCE_IDS) -> dict[str, Any]:
    run = run.resolve()
    config = json.loads((run / "config" / "run.json").read_text(encoding="utf-8"))
    manifest = run / "snapshot-manifests" / "works-manifest.json"
    record = json.loads((run / "run-record.json").read_text(encoding="utf-8"))
    if (_sha256(manifest) != record["snapshot"]["entities"]["works"]["manifest_sha256"]
            or config["snapshot_date"] != json.loads(manifest.read_text(encoding="utf-8"))["date"]):
        raise ValueError("Dated Works manifest mismatch")
    root = run / "intermediates" / "extraction"
    output_dir = run / "intermediates" / "source-repairs"
    output_dir.mkdir(exist_ok=True)
    output = output_dir / "targeted_work_metadata.parquet"
    receipt = output.with_suffix(".receipt.json")
    if output.exists() or receipt.exists():
        raise FileExistsError("Preserve the existing targeted metadata and receipt")
    selected = selected_rows(run, source_ids)
    works_root = (Path(config["raw_snapshot_root"]) / "works").resolve()
    if any(not Path(row["source_file"]).resolve().is_relative_to(works_root)
           for row in selected):
        raise ValueError("Selected snapshot file escapes the dated Works root")
    values = metadata_for_rows(selected)
    partial = output.with_suffix(".parquet.partial")
    if partial.exists():
        raise FileExistsError(f"Inspect incomplete output: {partial}")
    pq.write_table(pa.Table.from_pylist(values), partial, compression="zstd")
    os.replace(partial, output)
    report = {
        "version": "snapshot-targeted-work-metadata-v1",
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "snapshot_date": config["snapshot_date"],
        "works_manifest_sha256": _sha256(manifest),
        "work_extract_raw_sha256": _receipt_hash(root / "work_extract_raw.parquet"),
        "dedup_choice_sha256": _receipt_hash(root / "dedup_choice.parquet"),
        "works_sha256": _receipt_hash(root / "works.parquet"),
        "reader_code_sha256": _sha256(Path(__file__)),
        "source_ids": list(source_ids),
        "rows": len(values),
        "rows_by_source_id": {source: sum(row["source_id"] == source for row in values)
                              for source in source_ids},
        "files_read": len({row["source_file"] for row in values}),
        "bytes": output.stat().st_size,
        "sha256": _sha256(output),
    }
    receipt.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=RUN)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--source-id", action="append", dest="source_ids",
                        help="Repeat for all source IDs discovered by the current Sources preflight; defaults to the September JGR and Eurasian IDs")
    args = parser.parse_args()
    if not args.execute:
        parser.error("Reading snapshot files requires --execute")
    print(json.dumps(extract(args.run, tuple(args.source_ids) if args.source_ids else SOURCE_IDS), indent=2))


if __name__ == "__main__":
    main()
