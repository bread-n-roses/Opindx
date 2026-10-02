"""Inventory annual raw source/key pairs from a completed website extraction.

All reads are of completed local count, edge and publication-year checkpoints.
The inventory retains both edge roles separately and does not choose corrections,
compare Sources coverage, approve identities, compute scores, or publish data.
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


RUN = Path(__file__).resolve().parents[1]
VERSION = "website-raw-pair-audit-v1"
MASS_COLUMNS = (
    "a_raw", "a_filtered", "citing_n_raw", "citing_n_filtered",
    "cited_n_raw", "cited_n_filtered", "retention_a_raw", "retention_a_filtered",
)


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _sql(path: str | Path) -> str:
    return "'" + str(path).replace("\\", "/").replace("'", "''") + "'"


def _partial(path: Path) -> Path:
    return path.with_name(path.name + ".partial")


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = _partial(path)
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _bind_inputs(run: Path) -> tuple[dict[str, Any], dict[str, Path], Path]:
    config_path = run / "config" / "run.json"
    config = _json(config_path)
    years = config.get("score_years")
    if (not isinstance(years, list) or not years or any(type(year) is not int for year in years)
            or years != sorted(set(years))):
        raise ValueError("score_years must be sorted, unique integers")
    intermediates = (run / config["paths_relative_to_run_root"]["intermediates"]).resolve()
    if not intermediates.is_relative_to(run):
        raise ValueError("Intermediates must stay inside the dated run")
    extraction = intermediates / "extraction"
    if (extraction / "extraction.lock").exists() or list(extraction.rglob("*.partial")):
        raise RuntimeError("Extraction is still running or has incomplete .partial files")
    report_path = extraction / "report.json"
    contract_path = extraction / "contract.json"
    report, contract = _json(report_path), _json(contract_path)
    contract_hash = _digest(contract)
    if report.get("status") != "PASS" or report.get("score_years") != years:
        raise RuntimeError("A completed extraction PASS report for these score years is required")
    if (report.get("contract_sha256") != contract_hash
            or contract.get("config_sha256") != _hash(config_path)
            or contract.get("extractor_sha256") != _hash(run / "code" / "extract.py")):
        raise RuntimeError("Extraction report/config/extractor contract hashes disagree")
    paths = {"raw_articles_by_publication_year": extraction / "raw_articles_by_publication_year.parquet"}
    for year in years:
        paths[f"counts_{year}"] = extraction / str(year) / "counts_raw_identity.parquet"
        paths[f"edges_{year}"] = extraction / str(year) / "edges_raw_identity.parquet"
    files = []
    for name, path in paths.items():
        receipt_path = path.with_suffix(".receipt.json")
        receipt = _json(receipt_path)
        stage = report.get("stages", {}).get(name, {})
        stat = path.stat()
        if (receipt.get("contract_sha256") != contract_hash or receipt.get("bytes") != stat.st_size
                or receipt.get("sha256") != _hash(path)
                or stage.get("rows") != receipt.get("rows")
                or Path(stage.get("path", "")).resolve() != path.resolve()):
            raise RuntimeError(f"Extraction input/receipt/report disagreement: {name}")
        files.append({"name": name, "path": str(path), "sha256": receipt["sha256"],
                      "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns, "rows": receipt["rows"],
                      "receipt_path": str(receipt_path), "receipt_sha256": _hash(receipt_path)})
    controls = {"config": config_path, "extraction_report": report_path,
                "extraction_contract": contract_path, "extractor": run / "code" / "extract.py",
                "audit_code": Path(__file__)}
    binding = {"version": VERSION, "score_years": years, "duckdb_version": duckdb.__version__,
               "extraction_contract_sha256": contract_hash, "files": files,
               "controls": {name: {"path": str(path), "sha256": _hash(path)} for name, path in controls.items()},
               "verification_scope": "Extraction receipt content hashes before reading, then file size/mtime and control hashes after reading"}
    return binding, paths, intermediates / "identity-review" / "raw-pairs"


def _assert_unchanged(binding: dict[str, Any]) -> None:
    for item in binding["files"]:
        stat = Path(item["path"]).stat()
        if ((stat.st_size, stat.st_mtime_ns) != (item["bytes"], item["mtime_ns"])
                or _hash(Path(item["receipt_path"])) != item["receipt_sha256"]):
            raise RuntimeError("Extraction checkpoint/receipt changed during pair audit")
    if any(_hash(Path(item["path"])) != item["sha256"] for item in binding["controls"].values()):
        raise RuntimeError("Pair-audit control/config/code changed during the audit")


def _validate_input_tables(con: duckdb.DuckDBPyConnection, binding: dict[str, Any]) -> None:
    for item in binding["files"]:
        is_edge = item["name"].startswith("edges_")
        raw, filtered = ("n_raw", "n_filtered") if is_edge else ("a_raw", "a_filtered")
        rows, bad = con.execute(f"""
            SELECT count(*), count(*) FILTER (
                {raw} IS NULL OR {filtered} IS NULL OR {raw}<0 OR {filtered}<0
                OR {filtered}>{raw} OR {raw}%1<>0 OR {filtered}%1<>0)
            FROM read_parquet({_sql(item['path'])})
        """).fetchone()
        if rows != item["rows"] or bad:
            raise RuntimeError(f"Invalid row count or Raw/Filtered mass in {item['name']}")


def _pair_sql(year: int, paths: dict[str, Path]) -> str:
    counts, edges = paths[f"counts_{year}"], paths[f"edges_{year}"]
    retention = paths["raw_articles_by_publication_year"]
    checksum = " + ".join(f"try_cast(substr(normalized_raw_issn_l,{position},1) AS INTEGER)*{weight}"
                          for position, weight in ((1,8), (2,7), (3,6), (4,5), (6,4), (7,3), (8,2)))
    checksum += " + CASE substr(normalized_raw_issn_l,9,1) WHEN 'X' THEN 10 ELSE try_cast(substr(normalized_raw_issn_l,9,1) AS INTEGER) END"
    return f"""
        WITH mass_rows AS (
            SELECT source_id, raw_issn_l, a_raw, a_filtered,
                   0::BIGINT AS citing_n_raw, 0::BIGINT AS citing_n_filtered,
                   0::BIGINT AS cited_n_raw, 0::BIGINT AS cited_n_filtered,
                   0::BIGINT AS retention_a_raw, 0::BIGINT AS retention_a_filtered,
                   0::BIGINT AS active_publication_years_raw, 0::BIGINT AS active_publication_years_filtered
            FROM read_parquet({_sql(counts)}) WHERE a_raw>0 OR a_filtered>0
            UNION ALL
            SELECT citing_source_id, raw_citing_issn_l, 0,0,n_raw,n_filtered,0,0,0,0,0,0
            FROM read_parquet({_sql(edges)}) WHERE n_raw>0 OR n_filtered>0
            UNION ALL
            SELECT cited_source_id, raw_cited_issn_l, 0,0,0,0,n_raw,n_filtered,0,0,0,0
            FROM read_parquet({_sql(edges)}) WHERE n_raw>0 OR n_filtered>0
            UNION ALL
            SELECT source_id,raw_issn_l,0,0,0,0,0,0,sum(a_raw),sum(a_filtered),
                   count(DISTINCT year) FILTER (a_raw>0), count(DISTINCT year) FILTER (a_filtered>0)
            FROM read_parquet({_sql(retention)})
            WHERE year BETWEEN {year - 5} AND {year - 1} AND (a_raw>0 OR a_filtered>0)
            GROUP BY source_id,raw_issn_l
        ), pairs AS (
            SELECT {year}::INTEGER AS score_year, source_id, raw_issn_l,
                   {', '.join(f'sum({name})::BIGINT AS {name}' for name in MASS_COLUMNS)},
                   sum(active_publication_years_raw)::INTEGER AS active_publication_years_raw,
                   sum(active_publication_years_filtered)::INTEGER AS active_publication_years_filtered,
                   upper(trim(coalesce(raw_issn_l,''))) AS normalized_raw_issn_l,
                   source_id IS NULL OR trim(source_id)='' AS source_id_missing
            FROM mass_rows GROUP BY source_id,raw_issn_l
        )
        SELECT *, CASE
            WHEN normalized_raw_issn_l='' THEN 'missing'
            WHEN NOT regexp_full_match(normalized_raw_issn_l,'[0-9]{{4}}-[0-9]{{3}}[0-9X]') THEN 'invalid_syntax'
            WHEN ({checksum})%11<>0 THEN 'invalid_checksum'
            ELSE 'valid' END AS raw_key_status,
            count(*) OVER (PARTITION BY score_year,source_id)::INTEGER AS source_observed_raw_variants
        FROM pairs
    """


def audit_pairs(run: Path = RUN, *, memory_limit: str = "4GB", threads: int = 2) -> dict[str, Any]:
    run = run.resolve()
    binding, paths, output = _bind_inputs(run)
    if type(threads) is not int or threads < 1:
        raise ValueError("threads must be a positive integer")
    output.mkdir(parents=True, exist_ok=True)
    if list(output.rglob("*.partial")):
        raise RuntimeError("Incomplete pair-audit .partial outputs require inspection")
    lock = output / "audit_pairs.lock"
    with lock.open("x", encoding="utf-8") as stream:
        stream.write(str(os.getpid()))
    try:
        pairs, variants = output / "annual_pairs.parquet", output / "source_variants.parquet"
        report_path = output / "report.json"
        binding_hash = _digest(binding)
        if report_path.exists():
            report = _json(report_path)
            if report.get("status") != "PASS" or report.get("input_binding_sha256") != binding_hash:
                raise RuntimeError("Pair-audit binding changed; refusing reuse")
            for path in (pairs, variants):
                expected = report["outputs"][path.name]
                if not path.is_file() or _hash(path) != expected["sha256"]:
                    raise RuntimeError("Pair-audit output differs from its completion report")
            _assert_unchanged(binding)
            return {**report, "reused": True}
        if pairs.exists() or variants.exists():
            raise RuntimeError("Pair-audit outputs lack a completion report; refusing orphan reuse")
        with duckdb.connect() as con:
            con.execute(f"SET threads={threads}")
            con.execute(f"SET memory_limit={_sql(memory_limit)}")
            con.execute(f"SET temp_directory={_sql(output / 'duckdb_temp')}")
            con.execute("SET preserve_insertion_order=false")
            _validate_input_tables(con, binding)
            sql = " UNION ALL ".join(f"({_pair_sql(year, paths)})" for year in binding["score_years"])
            con.execute(f"COPY ({sql}) TO {_sql(_partial(pairs))} (FORMAT PARQUET, COMPRESSION ZSTD)")
            pairs_read = f"read_parquet({_sql(_partial(pairs))})"
            mismatches = con.execute(f"""
                SELECT count(*) FROM {pairs_read}
                WHERE a_raw<>retention_a_raw OR a_filtered<>retention_a_filtered
                   OR active_publication_years_raw NOT BETWEEN 0 AND 5
                   OR active_publication_years_filtered>active_publication_years_raw
            """).fetchone()[0]
            if mismatches:
                raise RuntimeError(f"{mismatches} raw pairs disagree with cited-window retention")
            con.execute(f"""
                COPY (SELECT score_year, source_id, bool_or(source_id_missing) AS source_id_missing,
                             count(*)::INTEGER AS observed_raw_variant_count,
                             list(raw_issn_l ORDER BY raw_issn_l NULLS FIRST) AS raw_issn_l_variants,
                             count(*) FILTER (raw_key_status='missing') AS missing_raw_variant_count,
                             count(*) FILTER (raw_key_status LIKE 'invalid_%') AS invalid_raw_variant_count,
                             {', '.join(f'sum({name})::BIGINT AS {name}' for name in MASS_COLUMNS)}
                      FROM {pairs_read} GROUP BY score_year,source_id)
                TO {_sql(_partial(variants))} (FORMAT PARQUET, COMPRESSION ZSTD)
            """)
            summary = con.execute(f"""
                SELECT score_year,raw_key_status,count(*) AS pair_count,
                       count(*) FILTER (source_id_missing) AS missing_source_id_pairs,
                       count(*) FILTER (raw_issn_l IS NULL) AS null_raw_key_pairs,
                       count(*) FILTER (raw_issn_l IS NOT NULL AND trim(raw_issn_l)='') AS blank_raw_key_pairs,
                       {', '.join(f'coalesce(sum({name}),0)::BIGINT AS {name}' for name in MASS_COLUMNS)}
                FROM {pairs_read} GROUP BY score_year,raw_key_status ORDER BY score_year,raw_key_status
            """)
            columns = [item[0] for item in summary.description]
            summary_rows = [dict(zip(columns, row)) for row in summary.fetchall()]
            multiple = con.execute(f"""
                SELECT score_year,count(*) FROM read_parquet({_sql(_partial(variants))})
                WHERE observed_raw_variant_count>1 AND NOT source_id_missing
                GROUP BY score_year ORDER BY score_year
            """).fetchall()
            row_counts = {path.name: con.execute(f"SELECT count(*) FROM read_parquet({_sql(_partial(path))})").fetchone()[0]
                          for path in (pairs, variants)}
        _assert_unchanged(binding)
        for path in (pairs, variants):
            os.replace(_partial(path), path)
        quality = {str(year): {"pair_count": 0, "valid_raw_key_pairs": 0,
                              "missing_raw_key_pairs": 0, "invalid_raw_key_pairs": 0,
                              "invalid_syntax_pairs": 0, "invalid_checksum_pairs": 0,
                              "null_raw_key_pairs": 0, "blank_raw_key_pairs": 0,
                              "missing_source_id_pairs": 0}
                   for year in binding["score_years"]}
        for row in summary_rows:
            annual = quality[str(row["score_year"])]
            for name in ("pair_count", "missing_source_id_pairs", "null_raw_key_pairs", "blank_raw_key_pairs"):
                annual[name] += row[name]
            status = row["raw_key_status"]
            annual[{"valid": "valid_raw_key_pairs", "missing": "missing_raw_key_pairs",
                    "invalid_syntax": "invalid_syntax_pairs", "invalid_checksum": "invalid_checksum_pairs"}[status]] += row["pair_count"]
            if status.startswith("invalid_"):
                annual["invalid_raw_key_pairs"] += row["pair_count"]
        report = {
            "status": "PASS", "version": VERSION, "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "input_binding_sha256": binding_hash, "input_binding": binding,
            "outputs": {path.name: {"sha256": _hash(path), "bytes": path.stat().st_size,
                                    "rows": row_counts[path.name]} for path in (pairs, variants)},
            "summary_by_year_and_raw_key_status": summary_rows,
            "annual_key_quality": quality,
            "sources_with_multiple_raw_variants_by_year": {
                str(year): dict(multiple).get(year, 0) for year in binding["score_years"]},
            "cited_window_pair_count_conservation_pass": True,
            "raw_edge_roles_counted_separately": True,
            "missing_source_id_note": "Null or blank source IDs are unidentified groups, not identified OpenAlex sources.",
            "graph_only_sources_comparison": "deferred",
            "identity_corrections_approved": False, "scores_computed": False,
        }
        _atomic_json(report_path, report)
        return {**report, "reused": False}
    finally:
        lock.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=RUN)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--memory-limit", default="4GB")
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    if not args.execute:
        parser.error("Explicit --execute is required to read completed extraction checkpoints")
    print(json.dumps(audit_pairs(args.run, memory_limit=args.memory_limit, threads=args.threads), indent=2))


if __name__ == "__main__":
    main()
