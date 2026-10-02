"""Review three OpenAlex source changes before corrected graph construction.

`--discover` checks latest Sources and lists the source IDs whose Work metadata
must be captured. `--execute` verifies complete snapshot-bound metadata,
combines reviewed Work decisions, and writes a separate, hash-bound preflight.
The original latest Sources and Works checkpoints remain unchanged.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

import bmc_continuity as bmc
import source_checkpoint
import source_repairs_eurasian as eurasian
import source_repairs_jgr as jgr


RUN = Path(__file__).resolve().parents[1]
VERSION = "website-source-repair-preflight-v1"
REPAIR_VERSION = "source-repair-v1"
LEDGER_COLUMNS = eurasian.LEDGER_COLUMNS
EXCLUSION_COLUMNS = eurasian.EXCLUSION_COLUMNS


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def _binding(path: Path) -> dict[str, Any]:
    path = path.resolve(strict=True)
    before = path.stat()
    digest = _sha256(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f"Input changed while hashing: {path}")
    return {"path": str(path), "sha256": digest, "bytes": after.st_size,
            "mtime_ns": after.st_mtime_ns}


def _aliases(value: object) -> set[str]:
    if value is None or (pd.api.types.is_scalar(value) and pd.isna(value)):
        return set()
    if isinstance(value, str) or not hasattr(value, "__iter__"):
        raise ValueError("Latest Sources ISSNs must be arrays")
    values = list(value)
    if any(not isinstance(item, str) for item in values):
        raise ValueError("Latest Sources ISSNs must contain strings")
    return {item.strip().upper() for item in values if item.strip()}


def discover_source_cases(latest: pd.DataFrame) -> dict[str, Any]:
    """Find candidate IDs from Source aliases/title before any Work read."""
    required = {"source_id", "raw_issn_l", "source_type", "title", "issns"}
    if not required.issubset(latest.columns) or latest.source_id.isna().any() or latest.source_id.duplicated().any():
        raise ValueError("Latest Sources lack unique IDs or case-review columns")
    eurasian_ids, jgr_ids = set(), set()
    jgr_titles = {jgr.PUBLISHER_SOURCE_NAME.casefold(), jgr.MAG_SOURCE_NAME.casefold()}
    eurasian_titles = {"eurasian business review", "eurasian economic review"}
    for row in latest.itertuples(index=False):
        source_id = row.source_id
        title = (row.title or "").strip().casefold()
        aliases = _aliases(row.issns)
        if (source_id in {eurasian.BUSINESS_ID, eurasian.ECONOMIC_ID}
                or aliases.intersection(eurasian.ALL_ISSNS) or title in eurasian_titles):
            eurasian_ids.add(source_id)
        if (source_id in {jgr.SOURCE_ID, jgr.JUNE_SOURCE_ID}
                or aliases.intersection(jgr.ATMOSPHERES_ISSNS) or title in jgr_titles):
            jgr_ids.add(source_id)
    if (eurasian.ECONOMIC_ID not in eurasian_ids
            or not eurasian_ids <= {eurasian.ECONOMIC_ID, eurasian.BUSINESS_ID}):
        raise ValueError(f"Eurasian source IDs changed; review before Works extraction: {sorted(eurasian_ids)}")
    if len(jgr_ids) != 1:
        raise ValueError(f"JGR Atmospheres source IDs changed or split: {sorted(jgr_ids)}")
    bmc_audit = bmc.audit_bmc_sources(latest)
    if bmc_audit["status"] != "APPROVED_CONTINUITY":
        raise ValueError(f"BMC source identity changed: {bmc_audit['review_reasons']}")
    # Case functions guard complete Source metadata, including allowed ISSNs.
    eurasian.repair_latest_sources(latest)
    jgr_source_id = next(iter(jgr_ids))
    jgr.repair_latest_sources(latest, source_id=jgr_source_id)
    target_ids = sorted(eurasian_ids | jgr_ids)
    return {"eurasian_source_ids": sorted(eurasian_ids),
            "jgr_source_id": jgr_source_id,
            "bmc_source_id": bmc.SOURCE_ID,
            "required_target_source_ids": target_ids,
            "bmc_source_audit_sha256": bmc_audit["evidence_sha256"]}


def _read_latest(run: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    source_path = run / "intermediates/sources/latest_sources.parquet"
    receipt_path = run / "intermediates/sources/latest_sources.receipt.json"
    source = _binding(source_path)
    receipt = _json(receipt_path)
    if (receipt.get("sha256") != source["sha256"] or receipt.get("bytes") != source["bytes"]):
        raise RuntimeError("Baseline latest Sources differ from their receipt")
    with duckdb.connect() as con:
        latest = con.execute("SELECT * FROM read_parquet(?)", [str(source_path)]).df()
    latest = source_checkpoint.normalize_latest(latest)
    if len(latest) != receipt.get("rows"):
        raise RuntimeError("Baseline latest Sources row count differs from receipt")
    return latest, {"source": source, "receipt": _binding(receipt_path)}


def _load_targeted(run: Path, discovery: dict[str, Any], baseline_works: Path,
                   snapshot_date: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    path = run / "intermediates/source-repairs/targeted_work_metadata.parquet"
    receipt_path = path.with_suffix(".receipt.json")
    metadata_binding = _binding(path)
    receipt_binding = _binding(receipt_path)
    receipt = _json(receipt_path)
    source_ids = receipt.get("source_ids")
    if (receipt.get("sha256") != metadata_binding["sha256"]
            or receipt.get("bytes") != metadata_binding["bytes"]
            or receipt.get("snapshot_date") != snapshot_date
            or not isinstance(source_ids, list)
            or set(source_ids) != set(discovery["required_target_source_ids"])):
        raise RuntimeError("Targeted Work metadata receipt does not cover the discovered source IDs")
    with duckdb.connect() as con:
        metadata = con.execute("SELECT * FROM read_parquet(?)", [str(path)]).df()
    required = {"work_id", "source_id", "raw_issn_l", "year", "doi", "title",
                "source_file", "source_row", "raw_source_name", "source_display_name"}
    if not required.issubset(metadata.columns):
        raise ValueError("Targeted Work metadata lacks the snapshot review columns")
    if (metadata.work_id.isna().any() or metadata.work_id.duplicated().any()
            or set(metadata.source_id) != set(source_ids)
            or len(metadata) != receipt.get("rows")
            or {source: int(metadata.source_id.eq(source).sum()) for source in source_ids}
            != receipt.get("rows_by_source_id")):
        raise RuntimeError("Targeted Work metadata rows disagree with its receipt")
    return metadata, {"metadata": metadata_binding, "receipt": receipt_binding}


def _verify_targeted_coverage(
    baseline_works: Path, metadata: pd.DataFrame, discovery: dict[str, Any],
) -> dict[str, Any]:
    """Match every relevant retained Work ID and original source/key/year."""
    source_ids = discovery["required_target_source_ids"]
    keys = sorted(set(eurasian.ALL_ISSNS)
                  | {jgr.PARENT_ISSN_L, jgr.ATMOSPHERES_ISSN_L, *jgr.ATMOSPHERES_ISSNS})
    with duckdb.connect() as con:
        baseline = con.execute("""
            SELECT work_id, source_id, raw_issn_l, year
            FROM read_parquet(?)
            WHERE source_id IN (SELECT unnest(?::VARCHAR[]))
               OR raw_issn_l IN (SELECT unnest(?::VARCHAR[]))
        """, [str(baseline_works), source_ids, keys]).df()
    expected = baseline.loc[:, ["work_id", "source_id", "raw_issn_l", "year"]]
    observed = metadata.loc[:, ["work_id", "source_id", "raw_issn_l", "year"]]
    if expected.work_id.isna().any() or expected.work_id.duplicated().any():
        raise RuntimeError("Baseline affected Works lack unique Work IDs")
    if observed.work_id.isna().any() or observed.work_id.duplicated().any():
        raise RuntimeError("Targeted affected Works lack unique Work IDs")
    merged = expected.merge(observed, on="work_id", how="outer", suffixes=("_baseline", "_target"),
                            indicator=True, validate="one_to_one")
    if (not merged._merge.eq("both").all()
            or any(not merged[f"{name}_baseline"].eq(merged[f"{name}_target"]).all()
                   for name in ("source_id", "raw_issn_l", "year"))):
        raise RuntimeError("Targeted metadata omits or changes affected baseline Works")
    return {"affected_work_rows": len(expected),
            "rows_by_source_id": {source: int(expected.source_id.eq(source).sum()) for source in source_ids}}


def _combine_decisions(metadata: pd.DataFrame, jgr_ledger: pd.DataFrame,
                       jgr_exclusions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    eur_ledger = eurasian.build_ledger(metadata)
    eur_exclusions = eurasian.build_exclusions(metadata)
    for name, frame, columns in (
        ("Eurasian ledger", eur_ledger, LEDGER_COLUMNS),
        ("JGR ledger", jgr_ledger, LEDGER_COLUMNS),
        ("Eurasian exclusions", eur_exclusions, EXCLUSION_COLUMNS),
        ("JGR exclusions", jgr_exclusions, EXCLUSION_COLUMNS),
    ):
        if tuple(frame.columns) != columns:
            raise ValueError(f"{name} columns differ from the reviewed ledger contract")
    ledger = pd.concat((eur_ledger, jgr_ledger), ignore_index=True).sort_values("work_id")
    exclusions = pd.concat((eur_exclusions, jgr_exclusions), ignore_index=True).sort_values("work_id")
    if (ledger.work_id.duplicated().any() or exclusions.work_id.duplicated().any()
            or set(ledger.work_id).intersection(exclusions.work_id)):
        raise ValueError("Case reviews contain duplicate or conflicting Work decisions")
    observed = metadata.set_index("work_id")
    for frame in (ledger, exclusions):
        for row in frame.itertuples(index=False):
            if (row.work_id not in observed.index
                    or observed.at[row.work_id, "source_id"] != row.expected_source_id
                    or observed.at[row.work_id, "raw_issn_l"] != row.expected_raw_issn_l):
                raise RuntimeError(f"Work decision preimage changed: {row.work_id}")
    return ledger.reset_index(drop=True), exclusions.reset_index(drop=True)


def _write_parquet(path: Path, frame: pd.DataFrame, columns: tuple[str, ...],
                   *, latest_sources: bool = False) -> None:
    partial = path.with_name(path.name + ".partial")
    with duckdb.connect() as con:
        con.register("stage", frame)
        if latest_sources:
            text_columns = ("source_id", "raw_issn_l", "source_type", "title", "publisher",
                            "oa_field", "oa_domain")
            casts = [f'CAST("{column}" AS VARCHAR) AS "{column}"' for column in text_columns]
            casts.extend(("CAST(issns AS VARCHAR[]) AS issns",
                          "CAST(is_open_access AS BOOLEAN) AS is_open_access",
                          "CAST(updated_date AS TIMESTAMP) AS updated_date"))
        else:
            casts = [f'CAST("{column}" AS VARCHAR) AS "{column}"' for column in columns]
        con.execute(f"COPY (SELECT {', '.join(casts)} FROM stage) TO ? "
                    "(FORMAT PARQUET, COMPRESSION ZSTD)", [str(partial)])
    os.replace(partial, path)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    partial = path.with_name(path.name + ".partial")
    with partial.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(partial, path)


def _verify_jgr_review(run: Path, metadata_binding: dict[str, Any],
                       candidate_id: str) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    root = run / "intermediates/source-repairs"
    report_path = root / "jgr_review.json"
    output_paths = {"ledger": root / "jgr_rekey_ledger.parquet",
                    "exclusions": root / "jgr_exclusions.parquet",
                    "content_audit": root / "jgr_content_audit.parquet"}
    exists = [path.exists() for path in (report_path, *output_paths.values())]
    if any(exists) and not all(exists):
        raise RuntimeError("Incomplete JGR review outputs require inspection")
    if not any(exists):
        jgr.review(run, source_id=candidate_id)
    report = _json(report_path)
    if (report.get("status") != "PASS" or report.get("candidate_source_id") != candidate_id
            or report.get("targeted_metadata_sha256") != metadata_binding["sha256"]
            or report.get("review_code_sha256") != _sha256(Path(jgr.__file__))):
        raise RuntimeError("JGR review report is stale or incomplete")
    files = {"report": _binding(report_path)}
    frames = {}
    with duckdb.connect() as con:
        for name, path in output_paths.items():
            files[name] = _binding(path)
            output = report.get("outputs", {}).get(name, {})
            if (Path(output.get("path", "")).as_posix() != path.relative_to(run).as_posix()
                    or output.get("sha256") != files[name]["sha256"]):
                raise RuntimeError(f"JGR {name} differs from its review report")
            frames[name] = con.execute("SELECT * FROM read_parquet(?)", [str(path)]).df()
            if len(frames[name]) != output.get("rows"):
                raise RuntimeError(f"JGR {name} row count differs from its report")
    if (tuple(frames["ledger"].columns) != LEDGER_COLUMNS
            or tuple(frames["exclusions"].columns) != EXCLUSION_COLUMNS):
        raise RuntimeError("JGR review ledger columns differ from the shared contract")
    return frames["ledger"], frames["exclusions"], {"files": files, "report": report}


def preflight(run: Path = RUN) -> dict[str, Any]:
    """Persist reviewed outputs or verify an identical completed preflight."""
    run = Path(run).resolve(strict=True)
    output = run / "intermediates" / REPAIR_VERSION
    output.mkdir(parents=True, exist_ok=True)
    if list(output.glob("*.partial")):
        raise RuntimeError("Incomplete source-repair preflight files require inspection")
    lock = output / "preflight.lock"
    with lock.open("x", encoding="utf-8") as stream:
        stream.write(str(os.getpid()))
    try:
        config = _json(run / "config/run.json")
        record = _json(run / "run-record.json")
        snapshot_date = config["snapshot_date"]
        if snapshot_date != record["snapshot"]["snapshot_date"]:
            raise RuntimeError("Dated snapshot config and run record disagree")
        latest, source_bindings = _read_latest(run)
        discovery = discover_source_cases(latest)
        works_path = run / "intermediates/extraction/works.parquet"
        baseline_works = _binding(works_path)
        works_receipt_path = works_path.with_suffix(".receipt.json")
        works_receipt = _json(works_receipt_path)
        if (works_receipt.get("sha256") != baseline_works["sha256"]
                or works_receipt.get("bytes") != baseline_works["bytes"]):
            raise RuntimeError("Baseline Works differ from their extraction receipt")
        metadata, metadata_bindings = _load_targeted(
            run, discovery, works_path, snapshot_date)
        targeted_receipt = _json(run / "intermediates/source-repairs/targeted_work_metadata.receipt.json")
        if targeted_receipt.get("works_sha256") != baseline_works["sha256"]:
            raise RuntimeError("Targeted Work metadata is bound to another Works checkpoint")
        works_manifest_path = run / "snapshot-manifests/works-manifest.json"
        if targeted_receipt.get("works_manifest_sha256") != _sha256(works_manifest_path):
            raise RuntimeError("Targeted Work metadata is bound to another Works manifest")
        coverage = _verify_targeted_coverage(works_path, metadata, discovery)
        bmc_work_keys = [bmc.PREDECESSOR_ISSN, bmc.CURRENT_ISSN]
        with duckdb.connect() as con:
            bmc_works = con.execute("""
                SELECT source_id, raw_issn_l, year, count(*)::BIGINT AS work_count
                FROM read_parquet(?)
                WHERE source_id=? OR raw_issn_l IN (SELECT unnest(?::VARCHAR[]))
                GROUP BY 1,2,3
            """, [str(works_path), bmc.SOURCE_ID, bmc_work_keys]).df()
        bmc_audit = bmc.audit_bmc_sources(latest, bmc_works)
        if bmc_audit["status"] != "APPROVED_CONTINUITY":
            raise RuntimeError(f"BMC Work identity changed: {bmc_audit['review_reasons']}")
        jgr_ledger, jgr_exclusions, jgr_binding = _verify_jgr_review(
            run, metadata_bindings["metadata"], discovery["jgr_source_id"])
        if jgr_binding["report"].get("inputs", {}).get("works_sha256") != baseline_works["sha256"]:
            raise RuntimeError("JGR review is bound to another Works checkpoint")
        if (jgr_binding["report"].get("inputs", {}).get("works_manifest_sha256")
                != targeted_receipt["works_manifest_sha256"]):
            raise RuntimeError("JGR review is bound to another Works manifest")
        ledger, exclusions = _combine_decisions(metadata, jgr_ledger, jgr_exclusions)
        eur_sources, eur_audit = eurasian.repair_latest_sources(latest)
        jgr_sources = jgr.repair_latest_sources(
            eur_sources, source_id=discovery["jgr_source_id"])
        repaired_sources, bmc_source_audit = bmc.adjust_latest_sources(jgr_sources)
        repaired_sources = source_checkpoint.normalize_latest(repaired_sources)
        code_files = [Path(__file__), Path(source_checkpoint.__file__),
                      Path(eurasian.__file__), Path(jgr.__file__), Path(bmc.__file__)]
        binding = {
            "version": VERSION, "snapshot_date": snapshot_date,
            "run_config": _binding(run / "config/run.json"),
            "run_record": _binding(run / "run-record.json"),
            "baseline_sources": source_bindings,
            "baseline_works": baseline_works,
            "baseline_works_receipt": _binding(works_receipt_path),
            "works_manifest": _binding(works_manifest_path),
            "targeted_metadata": metadata_bindings,
            "jgr_review": jgr_binding["files"],
            "code": [_binding(path) for path in code_files],
            "discovery": discovery,
        }
        binding_hash = _digest(binding)
        files = {
            "work_overrides": output / "work_overrides.parquet",
            "excluded_works": output / "excluded_works.parquet",
            "latest_sources": output / "latest_sources.parquet",
        }
        report_path = output / "preflight_report.json"
        receipt_paths = {name: path.with_suffix(".receipt.json") for name, path in files.items()}
        existing = [path.exists() for path in (*files.values(), *receipt_paths.values(), report_path)]
        if any(existing):
            if not all(existing):
                raise RuntimeError("Incomplete source-repair preflight outputs require inspection")
            report = _json(report_path)
            if (report.get("status") != "PASS" or report.get("version") != VERSION
                    or report.get("input_binding_sha256") != binding_hash
                    or report.get("input_binding") != binding):
                raise RuntimeError("Source-repair preflight differs from bound inputs")
            for name, path in files.items():
                file_binding = _binding(path)
                receipt = _json(receipt_paths[name])
                if (receipt.get("sha256") != file_binding["sha256"]
                        or receipt.get("bytes") != file_binding["bytes"]
                        or receipt.get("input_binding_sha256") != binding_hash
                        or report.get("outputs", {}).get(name) != receipt):
                    raise RuntimeError(f"Source-repair {name} changed after preflight")
            return report
        frames = {"work_overrides": (ledger, LEDGER_COLUMNS),
                  "excluded_works": (exclusions, EXCLUSION_COLUMNS),
                  "latest_sources": (repaired_sources, tuple(source_checkpoint.sources.LATEST_COLUMNS))}
        receipts = {}
        for name, (frame, columns) in frames.items():
            path = files[name]
            _write_parquet(path, frame, columns, latest_sources=name == "latest_sources")
            with duckdb.connect() as con:
                rows = con.execute("SELECT count(*) FROM read_parquet(?)", [str(path)]).fetchone()[0]
            if rows != len(frame):
                raise RuntimeError(f"Source-repair {name} Parquet roundtrip row count changed")
            receipt = {
                "version": VERSION, "repair_version": REPAIR_VERSION, "status": "PASS",
                "path": path.relative_to(run).as_posix(), "rows": rows,
                "sha256": _sha256(path), "bytes": path.stat().st_size,
                "input_binding_sha256": binding_hash,
            }
            if name == "latest_sources":
                receipt["baseline_sources_sha256"] = source_bindings["source"]["sha256"]
            _atomic_json(receipt_paths[name], receipt)
            receipts[name] = receipt
        report = {
            "version": VERSION, "repair_version": REPAIR_VERSION,
            "status": "PASS", "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "snapshot_date": snapshot_date, "source_discovery": discovery,
            "targeted_coverage": coverage,
            "case_audits": {"eurasian": eur_audit,
                            "jgr": jgr_binding["report"], "bmc": bmc_audit,
                            "bmc_source_adjustment": bmc_source_audit},
            "decision_counts": {"work_overrides": len(ledger),
                                "excluded_works": len(exclusions),
                                "latest_source_rows": len(repaired_sources)},
            "input_binding": binding, "input_binding_sha256": binding_hash,
            "outputs": receipts,
            "baseline_preserved": True,
            "scores_computed": False, "publication_performed": False,
        }
        _atomic_json(report_path, report)
        return report
    finally:
        lock.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=RUN)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--discover", action="store_true",
                      help="Check Sources first and list required Work metadata IDs")
    mode.add_argument("--execute", action="store_true",
                      help="Validate Work metadata and persist preflight decisions")
    args = parser.parse_args()
    if args.discover:
        latest, _ = _read_latest(args.run.resolve())
        print(json.dumps(discover_source_cases(latest), indent=2, sort_keys=True))
    else:
        report = preflight(args.run)
        print(json.dumps({"status": report["status"],
                          "decision_counts": report["decision_counts"],
                          "input_binding_sha256": report["input_binding_sha256"]},
                         indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
