"""Rebuild raw annual graph inputs after reviewed Work-level source repairs.

The September baseline checkpoints are immutable. This command reads their
deduplicated Works and a closed, reviewed Work override ledger. It writes a
separate, hash-bound corrected checkpoint before any annual identity projection
or scoring. Future snapshots must run the case preflight before this command.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

import duckdb

import audit_pairs
import extract


RUN = Path(__file__).resolve().parents[1]
VERSION = "website-source-repair-graph-v1"
LEDGER_COLUMNS = (
    "work_id", "expected_source_id", "expected_raw_issn_l",
    "target_source_id", "target_raw_issn_l", "reason",
)
EXCLUSION_COLUMNS = ("work_id", "expected_source_id", "expected_raw_issn_l", "reason")


def rebuild(run: Path = RUN, *, ledger: Path, exclusions: Path | None = None,
            memory_limit: str = "8GB",
            threads: int = 4) -> dict:
    run = run.resolve()
    ledger = ledger.resolve()
    preflight_dir = run / "intermediates" / "source-repair-v1"
    if ledger != preflight_dir / "work_overrides.parquet":
        raise ValueError("The reviewed preflight Work ledger is required")
    if exclusions is None or exclusions.resolve() != preflight_dir / "excluded_works.parquet":
        raise ValueError("The reviewed preflight exclusion ledger is required")
    exclusions = exclusions.resolve()
    preflight_path = preflight_dir / "preflight_report.json"
    preflight = extract._json(preflight_path)
    if preflight.get("status") != "PASS" or preflight.get("repair_version") != "source-repair-v1":
        raise RuntimeError("Reviewed source-repair preflight must PASS before graph rebuild")
    for name, path in (("work_overrides", ledger), ("excluded_works", exclusions),
                       ("latest_sources", preflight_dir / "latest_sources.parquet")):
        receipt = extract._json(path.with_suffix(".receipt.json"))
        if (receipt.get("status") != "PASS"
                or receipt.get("input_binding_sha256") != preflight.get("input_binding_sha256")
                or receipt.get("sha256") != extract._hash_file(path)
                or receipt.get("bytes") != path.stat().st_size
                or preflight.get("outputs", {}).get(name) != receipt):
            raise RuntimeError(f"Reviewed source-repair {name} differs from preflight")
    if threads < 1:
        raise ValueError("threads must be positive")
    config, years, _ = extract._settings(run)
    baseline = run / "intermediates" / "extraction"
    original = baseline / "works.parquet"
    baseline_report = extract._json(baseline / "report.json")
    if baseline_report.get("status") != "PASS" or not original.exists():
        raise RuntimeError("Completed baseline Works extraction is required")
    original_receipt = extract._json(original.with_suffix(".receipt.json"))
    if original_receipt["sha256"] != extract._hash_file(original):
        raise RuntimeError("Baseline Works differ from their receipt")
    output = run / "intermediates" / "source-repair-v1" / "graph"
    output.mkdir(parents=True, exist_ok=True)
    if list(output.rglob("*.partial")):
        raise RuntimeError("Incomplete corrected stage requires inspection")
    contract = {
        "version": VERSION,
        "snapshot_date": config["snapshot_date"],
        "score_years": years,
        "baseline_works_sha256": original_receipt["sha256"],
        "baseline_report_sha256": extract._hash_file(baseline / "report.json"),
        "ledger_sha256": extract._hash_file(ledger),
        "ledger_relative_path": ledger.relative_to(run).as_posix(),
        "exclusions_sha256": extract._hash_file(exclusions) if exclusions else None,
        "exclusions_relative_path": exclusions.relative_to(run).as_posix() if exclusions else None,
        "preflight_report_sha256": extract._hash_file(preflight_path),
        "repaired_sources_sha256": extract._hash_file(preflight_dir / "latest_sources.parquet"),
        "repaired_sources_receipt_sha256": extract._hash_file(preflight_dir / "latest_sources.receipt.json"),
        "builder_sha256": extract._hash_file(Path(__file__)),
        "extractor_sha256": extract._hash_file(Path(extract.__file__)),
        "pair_auditor_sha256": extract._hash_file(Path(audit_pairs.__file__)),
        "duckdb_version": duckdb.__version__,
    }
    contract_path = output / "contract.json"
    if contract_path.exists():
        if extract._json(contract_path) != contract:
            raise RuntimeError("Corrected graph contract changed; refusing cache reuse")
    elif list(output.rglob("*.parquet")) or list(output.rglob("*.receipt.json")):
        raise RuntimeError("Unbound corrected graph cache exists")
    contract_hash = extract._hash_json(contract)
    completed_report = output / "report.json"
    if completed_report.exists():
        report = extract._json(completed_report)
        if (report.get("status") != "PASS" or report.get("version") != VERSION
                or report.get("contract_sha256") != contract_hash):
            raise RuntimeError("Existing corrected graph report does not match its contract")
        for stage in report.get("stages", {}).values():
            path = Path(stage["path"]).resolve()
            if not path.is_relative_to(output):
                raise RuntimeError("Corrected graph report names an output outside its graph directory")
            receipt = extract._json(path.with_suffix(".receipt.json"))
            if (receipt.get("contract_sha256") != contract_hash
                    or receipt.get("sha256") != extract._hash_file(path)
                    or receipt.get("bytes") != path.stat().st_size
                    or receipt.get("rows") != stage["rows"]):
                raise RuntimeError(f"Corrected graph stage changed after completion: {path}")
        return report
    lock = output / "rebuild.lock"
    with lock.open("x", encoding="utf-8") as stream:
        stream.write(str(os.getpid()))
    con = None
    try:
        con = duckdb.connect()
        con.execute(f"SET memory_limit={extract._sql(memory_limit)}")
        con.execute(f"SET threads={threads}")
        con.execute(f"SET temp_directory={extract._sql(output / 'duckdb_temp')}")
        con.execute("SET preserve_insertion_order=false")
        columns = [row[0] for row in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet({extract._sql(ledger)})").fetchall()]
        if tuple(columns) != LEDGER_COLUMNS:
            raise ValueError(f"Override ledger columns must equal {LEDGER_COLUMNS}")
        con.execute(f"CREATE TEMP VIEW overrides AS SELECT * FROM read_parquet({extract._sql(ledger)})")
        invalid = con.execute("""
            SELECT count(*) FILTER (work_id IS NULL OR trim(work_id)='' OR
                    expected_source_id IS NULL OR trim(expected_source_id)='' OR
                    expected_raw_issn_l IS NULL OR trim(expected_raw_issn_l)='' OR
                    target_source_id IS NULL OR trim(target_source_id)='' OR
                    target_raw_issn_l IS NULL OR trim(target_raw_issn_l)='' OR
                    reason IS NULL OR trim(reason)=''),
                   count(*) - count(DISTINCT work_id),
                   count(*) FILTER (expected_source_id=target_source_id AND
                                    expected_raw_issn_l=target_raw_issn_l)
            FROM overrides
        """).fetchone()
        if any(invalid):
            raise ValueError(f"Invalid, duplicate or unchanged Work override rows: {invalid}")
        preimage = con.execute(f"""
            SELECT count(*) AS ledger_rows,
                   count(*) FILTER (w.work_id IS NULL) AS absent,
                   count(*) FILTER (w.work_id IS NOT NULL AND
                      (w.source_id IS DISTINCT FROM o.expected_source_id OR
                       w.raw_issn_l IS DISTINCT FROM o.expected_raw_issn_l)) AS changed
            FROM overrides o LEFT JOIN read_parquet({extract._sql(original)}) w USING (work_id)
        """).fetchone()
        if preimage[1] or preimage[2]:
            raise RuntimeError(f"Work override preimages are absent or changed: {preimage}")
        excluded_rows = 0
        if exclusions is not None:
            excluded_columns = [row[0] for row in con.execute(
                f"DESCRIBE SELECT * FROM read_parquet({extract._sql(exclusions)})").fetchall()]
            if tuple(excluded_columns) != EXCLUSION_COLUMNS:
                raise ValueError(f"Exclusion ledger columns must equal {EXCLUSION_COLUMNS}")
            con.execute(f"CREATE TEMP VIEW exclusions AS SELECT * FROM read_parquet({extract._sql(exclusions)})")
            excluded = con.execute(f"""
                SELECT count(*), count(*)-count(DISTINCT e.work_id),
                       count(*) FILTER (o.work_id IS NOT NULL),
                       count(*) FILTER (w.work_id IS NULL),
                       count(*) FILTER (w.work_id IS NOT NULL AND
                           (w.source_id IS DISTINCT FROM e.expected_source_id OR
                            w.raw_issn_l IS DISTINCT FROM e.expected_raw_issn_l)),
                       count(*) FILTER (e.work_id IS NULL OR trim(e.work_id)='' OR
                           e.expected_source_id IS NULL OR trim(e.expected_source_id)='' OR
                           e.expected_raw_issn_l IS NULL OR trim(e.expected_raw_issn_l)='' OR
                           e.reason IS NULL OR trim(e.reason)='')
                FROM exclusions e
                LEFT JOIN read_parquet({extract._sql(original)}) w ON e.work_id=w.work_id
                LEFT JOIN overrides o ON e.work_id=o.work_id
            """).fetchone()
            excluded_rows = excluded[0]
            if any(excluded[1:]):
                raise RuntimeError(f"Exclusion preimages are invalid, absent or duplicated: {excluded}")
        if not contract_path.exists():
            extract._atomic_json(contract_path, contract)
        works = output / "works.parquet"
        stages = {}
        exclusion_join = "LEFT JOIN exclusions x USING (work_id)" if exclusions is not None else ""
        exclusion_where = "WHERE x.work_id IS NULL" if exclusions is not None else ""
        stages["works"] = extract._stage(con, works, f"""
            SELECT w.work_id,
                   coalesce(o.target_source_id, w.source_id) AS source_id,
                   coalesce(o.target_raw_issn_l, w.raw_issn_l) AS raw_issn_l,
                   w.year, w.work_type, w.is_ar, w.has_refs, w.refs
            FROM read_parquet({extract._sql(original)}) w
            LEFT JOIN overrides o USING (work_id)
            {exclusion_join}
            {exclusion_where}
        """, contract_hash)
        if stages["works"]["rows"] != original_receipt["rows"] - excluded_rows:
            raise RuntimeError("Corrected Works count differs from reviewed exclusions")
        retention = output / "raw_articles_by_publication_year.parquet"
        stages["raw_articles_by_publication_year"] = extract._stage(con, retention, f"""
            SELECT source_id, raw_issn_l, year,
                   count(*)::BIGINT AS a_raw,
                   count(*) FILTER (has_refs)::BIGINT AS a_filtered
            FROM read_parquet({extract._sql(works)})
            WHERE is_ar AND year BETWEEN {min(years) - 5} AND {max(years) - 1}
            GROUP BY source_id, raw_issn_l, year
        """, contract_hash)
        annual = {}
        for year in years:
            counts = output / str(year) / "counts_raw_identity.parquet"
            edges = output / str(year) / "edges_raw_identity.parquet"
            annual[year] = counts, edges
            stages[f"counts_{year}"] = extract._stage(con, counts, f"""
                SELECT source_id, raw_issn_l, sum(a_raw)::BIGINT AS a_raw,
                       sum(a_filtered)::BIGINT AS a_filtered
                FROM read_parquet({extract._sql(retention)})
                WHERE year BETWEEN {year - 5} AND {year - 1}
                GROUP BY source_id, raw_issn_l
            """, contract_hash)
            stages[f"edges_{year}"] = extract._stage(con, edges, f"""
                WITH citing AS (
                    SELECT source_id AS citing_source_id, raw_issn_l AS raw_citing_issn_l,
                           unnest(list_distinct(refs)) AS cited_work_id
                    FROM read_parquet({extract._sql(works)})
                    WHERE year={year} AND has_refs
                ), cited AS (
                    SELECT work_id, source_id, raw_issn_l, has_refs
                    FROM read_parquet({extract._sql(works)})
                    WHERE is_ar AND year BETWEEN {year - 5} AND {year - 1}
                )
                SELECT e.citing_source_id, e.raw_citing_issn_l,
                       c.source_id AS cited_source_id, c.raw_issn_l AS raw_cited_issn_l,
                       count(*)::BIGINT AS n_raw,
                       count(*) FILTER (c.has_refs)::BIGINT AS n_filtered
                FROM citing e JOIN cited c ON e.cited_work_id=c.work_id
                GROUP BY e.citing_source_id, e.raw_citing_issn_l, c.source_id, c.raw_issn_l
            """, contract_hash)
            pair_path = output / str(year) / "annual_pairs.parquet"
            stages[f"pairs_{year}"] = extract._stage(
                con, pair_path, audit_pairs._pair_sql(year, {
                    f"counts_{year}": counts, f"edges_{year}": edges,
                    "raw_articles_by_publication_year": retention,
                }), contract_hash)
        validation = extract._validate(con, works, works, retention, annual, years)
        if (extract._hash_file(ledger) != contract["ledger_sha256"]
                or (exclusions and extract._hash_file(exclusions) != contract["exclusions_sha256"])
                or extract._hash_file(preflight_path) != contract["preflight_report_sha256"]
                or extract._hash_file(preflight_dir / "latest_sources.parquet") != contract["repaired_sources_sha256"]
                or extract._hash_file(original) != contract["baseline_works_sha256"]):
            raise RuntimeError("Inputs changed during corrected graph rebuild")
        report = {
            "version": VERSION, "status": "PASS",
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "contract_sha256": contract_hash,
            "ledger_rows": preimage[0], "preimages_verified": True,
            "excluded_work_rows": excluded_rows,
            "score_years": years, "stages": stages, "validation": validation,
            "raw_snapshot_scans_this_invocation": 0,
            "baseline_preserved": True,
        }
        extract._atomic_json(output / "report.json", report)
        return report
    finally:
        if con is not None:
            con.close()
        lock.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=RUN)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--exclusions", type=Path)
    parser.add_argument("--memory-limit", default="8GB")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        parser.error("Explicit --execute is required")
    report = rebuild(args.run, ledger=args.ledger, exclusions=args.exclusions,
                     memory_limit=args.memory_limit,
                     threads=args.threads)
    print(json.dumps({"status": report["status"], "ledger_rows": report["ledger_rows"],
                      "excluded_work_rows": report["excluded_work_rows"],
                      "score_years": report["score_years"]}, indent=2))


if __name__ == "__main__":
    main()
