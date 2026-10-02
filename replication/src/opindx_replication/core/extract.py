"""Independent, resumable Works extraction for the dated website EF/AIS run.

The only query against the raw Works files writes a shared checkpoint. Annual
Raw/Filtered endpoints are derived from local files and retain journal self
citations. These are raw identities, not score-ready canonical journal keys.
CLI execution requires --execute; importing this module never reads the snapshot.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any
from urllib.parse import unquote, urlparse

import duckdb


RUN = Path(__file__).resolve().parents[1]
VERSION = "website-works-extraction-v2"
# Preserve the baseline citing-side eligibility; cited works are article/review.
CITING_TYPES = (
    "article", "review", "letter", "editorial", "preprint", "book-chapter",
    "erratum", "other", "retraction", "report", "dissertation",
    "supplementary-materials", "dataset", "reference-entry", "book",
)


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _hash_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sql(value: str | Path) -> str:
    return "'" + str(value).replace("\\", "/").replace("'", "''") + "'"


def _partial(path: Path) -> Path:
    return path.with_name(path.name + ".partial")


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    partial = _partial(path)
    with partial.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(partial, path)


def _settings(run: Path) -> tuple[dict[str, Any], list[int], Path]:
    config = _json(run / "config" / "run.json")
    years = config.get("score_years")
    if (not isinstance(years, list) or not years
            or any(type(y) is not int or not 1900 <= y <= 9997 for y in years)
            or years != sorted(set(years))):
        raise ValueError("score_years must be sorted, unique integer years")
    convention = config.get("year_convention", {})
    if convention != {"score_year_is_citing_year": True,
                       "cited_window_start_offset": -5,
                       "cited_window_end_offset": -1}:
        raise ValueError("Only citation year t and cited window t-5 through t-1 are supported")
    window = config.get("planned_work_extract", {})
    if (window.get("publication_year_start") != min(years) - 5
            or window.get("publication_year_end") != max(years)
            or window.get("retain_reference_arrays_for_years") != years):
        raise ValueError("planned_work_extract does not match the score years")
    relative = Path(config.get("paths_relative_to_run_root", {}).get("intermediates", "intermediates"))
    output = (run / relative / "extraction").resolve()
    if not output.is_relative_to(run) or output == run:
        raise ValueError("Extraction outputs must remain inside the dated run")
    return config, years, output


def _snapshot(run: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Bind declared manifests plus file sizes/mtimes, without hashing raw payloads."""
    raw_root = Path(config["raw_snapshot_root"]).resolve()
    works_root = raw_root / "works"
    copied = run / "snapshot-manifests" / "works-manifest.json"
    raw_manifest = works_root / "manifest.json"
    expected = _json(run / "run-record.json")["snapshot"]["entities"]["works"]
    manifest_hash = _hash_file(copied)
    if manifest_hash != expected["manifest_sha256"] or _hash_file(raw_manifest) != manifest_hash:
        raise RuntimeError("Works manifest hash does not match the dated run record")
    manifest = _json(copied)
    entries = manifest.get("files", [])
    if (manifest.get("date") != config["snapshot_date"]
            or manifest.get("entity") != "works" or manifest.get("format") != "parquet"
            or len(entries) != expected["files"]
            or manifest.get("content_length") != expected["bytes"]):
        raise RuntimeError("Works manifest vintage, format, count, or bytes differ from run record")
    rows: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for entry in entries:
        url_path = unquote(urlparse(entry["url"]).path)
        if "/works/" not in url_path:
            raise ValueError("Works manifest entry lacks a /works/ path")
        relative = url_path.split("/works/", 1)[1]
        path = (works_root / relative).resolve()
        if not path.is_relative_to(works_root) or path.suffix != ".parquet" or path in seen:
            raise ValueError(f"Invalid or repeated Works manifest path: {relative}")
        seen.add(path)
        stat = path.stat()
        if stat.st_size != entry["meta"]["content_length"]:
            raise RuntimeError(f"Works file size differs from manifest: {relative}")
        rows.append({"path": path.as_posix(), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    rows.sort(key=lambda row: row["path"])
    if not rows or sum(row["bytes"] for row in rows) != expected["bytes"]:
        raise RuntimeError("Empty or inconsistent Works file inventory")
    return {"manifest_sha256": manifest_hash, "files": rows,
            "inventory_sha256": _hash_json(rows),
            "verification_scope": "Manifest hashes and raw file sizes/mtimes; raw payloads are not rehashed"}


def _checkpoint_sql(files: list[str], years: list[int]) -> str:
    paths = "[" + ",".join(_sql(path) for path in files) + "]"
    citing_years = ",".join(map(str, years))
    types = ",".join(_sql(value) for value in CITING_TYPES)
    # This is the sole raw-Works query; every later stage reads the local checkpoint.
    return f"""
        WITH selected AS (
            SELECT id AS work_id, primary_location.source.id AS source_id,
                   primary_location.source.issn_l AS raw_issn_l,
                   publication_year AS year, type AS work_type,
                   referenced_works_count,
                   CASE WHEN publication_year IN ({citing_years})
                        THEN referenced_works ELSE NULL END AS refs,
                   updated_date, filename AS source_file,
                   file_row_number AS source_row
            FROM read_parquet({paths}, filename=true, file_row_number=true,
                              hive_partitioning=false)
            WHERE publication_year BETWEEN {min(years) - 5} AND {max(years)}
              AND primary_location.source.issn_l IS NOT NULL
              AND type IN ({types}) AND is_paratext IS NOT TRUE
        )
        SELECT *, md5(to_json(struct_pack(
            source_id := source_id, issn_l := raw_issn_l, year := year,
            work_type := work_type, referenced_works_count := referenced_works_count,
            refs := refs))) AS row_fingerprint
        FROM selected
    """


def _stage(con: duckdb.DuckDBPyConnection, output: Path, sql: str,
           contract_hash: str) -> dict[str, Any]:
    receipt = output.with_suffix(".receipt.json")
    sql_hash = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    if _partial(output).exists() or _partial(receipt).exists():
        raise RuntimeError(f"Incomplete stage; inspect .partial files for {output}")
    if output.exists() != receipt.exists():
        raise RuntimeError(f"Orphan output/receipt; refusing unbound reuse: {output}")
    if output.exists():
        stored = _json(receipt)
        if (stored.get("contract_sha256") != contract_hash or stored.get("sql_sha256") != sql_hash
                or stored.get("bytes") != output.stat().st_size
                or stored.get("sha256") != _hash_file(output)):
            raise RuntimeError(f"Completed checkpoint does not match its receipt: {output}")
        return {"reused": True, "build_seconds": 0.0, "rows": stored["rows"], "path": str(output)}
    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    print(f"building {output.name}", flush=True)
    con.execute(f"COPY ({sql}) TO {_sql(_partial(output))} (FORMAT PARQUET, COMPRESSION ZSTD)")
    os.replace(_partial(output), output)
    rows = con.execute(f"SELECT count(*) FROM read_parquet({_sql(output)})").fetchone()[0]
    seconds = time.perf_counter() - started
    _atomic_json(receipt, {"contract_sha256": contract_hash, "sql_sha256": sql_hash,
                          "bytes": output.stat().st_size, "sha256": _hash_file(output),
                          "rows": rows, "build_seconds": seconds})
    return {"reused": False, "build_seconds": seconds, "rows": rows, "path": str(output)}


def _validate(con: duckdb.DuckDBPyConnection, raw: Path, works: Path, retention: Path,
              annual: dict[int, tuple[Path, Path]], years: list[int]) -> dict[str, Any]:
    distinct_ids, invalid_ids = con.execute(f"""
        SELECT count(DISTINCT work_id), count(*) FILTER (work_id IS NULL)
        FROM read_parquet({_sql(raw)})
    """).fetchone()
    n, unique_ids, invalid_refs = con.execute(f"""
        SELECT count(*), count(DISTINCT work_id),
               count(*) FILTER (year NOT IN ({','.join(map(str, years))}) AND refs IS NOT NULL)
        FROM read_parquet({_sql(works)})
    """).fetchone()
    if invalid_ids or n != unique_ids or n != distinct_ids or invalid_refs:
        raise RuntimeError("Work-ID deduplication or reference-retention validation failed")
    totals: dict[str, Any] = {}
    for year, (counts, edges) in annual.items():
        count_stats = con.execute(f"""
            SELECT coalesce(sum(a_raw),0), coalesce(sum(a_filtered),0),
                   count(*) FILTER (a_raw < 1 OR a_filtered < 0 OR a_filtered > a_raw)
            FROM read_parquet({_sql(counts)})
        """).fetchone()
        expected = con.execute(f"""
            SELECT count(*), count(*) FILTER (has_refs)
            FROM read_parquet({_sql(works)})
            WHERE is_ar AND year BETWEEN {year - 5} AND {year - 1}
        """).fetchone()
        retained = con.execute(f"""
            SELECT coalesce(sum(a_raw),0), coalesce(sum(a_filtered),0)
            FROM read_parquet({_sql(retention)})
            WHERE year BETWEEN {year - 5} AND {year - 1}
        """).fetchone()
        edge_stats = con.execute(f"""
            SELECT coalesce(sum(n_raw),0), coalesce(sum(n_filtered),0),
                   count(*) FILTER (n_raw < 1 OR n_filtered < 0 OR n_filtered > n_raw),
                   coalesce(sum(n_raw) FILTER (raw_citing_issn_l=raw_cited_issn_l),0)
            FROM read_parquet({_sql(edges)})
        """).fetchone()
        if count_stats[:2] != expected or count_stats[:2] != retained or count_stats[2] or edge_stats[2]:
            raise RuntimeError(f"Endpoint conservation/nesting validation failed for {year}")
        totals[str(year)] = {"a_raw": count_stats[0], "a_filtered": count_stats[1],
                             "n_raw": edge_stats[0], "n_filtered": edge_stats[1],
                             "raw_key_self_citation_mass_retained": edge_stats[3]}
    return {"deduplicated_works": n, "one_row_per_work": True,
            "count_conservation_and_raw_filtered_nesting": True, "annual_totals": totals}


def extract(run: Path = RUN, *, memory_limit: str = "8GB", threads: int = 4) -> dict[str, Any]:
    """Execute extraction only; caller must separately authorize real snapshot work."""
    run = run.resolve()
    config, years, output = _settings(run)
    if type(threads) is not int or threads < 1:
        raise ValueError("threads must be a positive integer")
    output.mkdir(parents=True, exist_ok=True)
    partials = sorted(output.rglob("*.partial"))
    if partials:
        raise RuntimeError(f"Incomplete .partial files must be inspected before resuming: {partials}")
    lock = output / "extraction.lock"
    with lock.open("x", encoding="utf-8") as stream:
        stream.write(str(os.getpid()))
    con = None
    try:
        snapshot = _snapshot(run, config)
        raw_sql = _checkpoint_sql([row["path"] for row in snapshot["files"]], years)
        contract = {"version": VERSION, "config_sha256": _hash_file(run / "config" / "run.json"),
                    "extractor_sha256": _hash_file(Path(__file__)),
                    "duckdb_version": duckdb.__version__, "snapshot": snapshot,
                    "checkpoint_sql_sha256": hashlib.sha256(raw_sql.encode("utf-8")).hexdigest()}
        contract_path = output / "contract.json"
        if contract_path.exists():
            if _json(contract_path) != contract:
                raise RuntimeError("Snapshot/config/extractor contract changed; refusing checkpoint reuse")
        else:
            if list(output.rglob("*.parquet")) or list(output.rglob("*.receipt.json")):
                raise RuntimeError("Extraction caches exist without a bound contract")
            _atomic_json(contract_path, contract)
        contract_hash = _hash_json(contract)
        con = duckdb.connect()
        con.execute(f"SET memory_limit={_sql(memory_limit)}")
        con.execute(f"SET threads={threads}")
        con.execute(f"SET temp_directory={_sql(output / 'duckdb_temp')}")
        con.execute("SET preserve_insertion_order=false")
        stages: dict[str, Any] = {}
        raw = output / "work_extract_raw.parquet"
        choice = output / "dedup_choice.parquet"
        works = output / "works.parquet"
        retention = output / "raw_articles_by_publication_year.parquet"
        stages["raw_checkpoint"] = _stage(con, raw, raw_sql, contract_hash)
        stages["dedup_choice"] = _stage(con, choice, f"""
            SELECT work_id, source_file, source_row
            FROM read_parquet({_sql(raw)})
            QUALIFY row_number() OVER (
                PARTITION BY work_id ORDER BY updated_date DESC NULLS LAST,
                    source_file DESC, raw_issn_l ASC, row_fingerprint ASC,
                    source_row ASC
            ) = 1
        """, contract_hash)
        # source_row breaks otherwise identical same-file ties and prevents join fanout.
        stages["works"] = _stage(con, works, f"""
            SELECT r.work_id, r.source_id, r.raw_issn_l, r.year, r.work_type,
                   r.work_type IN ('article','review') AS is_ar,
                   coalesce(r.referenced_works_count,0) > 0 AS has_refs, r.refs
            FROM read_parquet({_sql(raw)}) r
            JOIN read_parquet({_sql(choice)}) c
              ON r.work_id=c.work_id AND r.source_file=c.source_file AND r.source_row=c.source_row
        """, contract_hash)
        stages["raw_articles_by_publication_year"] = _stage(con, retention, f"""
            SELECT source_id, raw_issn_l, year,
                   count(*)::BIGINT AS a_raw,
                   count(*) FILTER (has_refs)::BIGINT AS a_filtered
            FROM read_parquet({_sql(works)})
            WHERE is_ar AND year BETWEEN {min(years) - 5} AND {max(years) - 1}
            GROUP BY source_id, raw_issn_l, year
        """, contract_hash)
        annual: dict[int, tuple[Path, Path]] = {}
        for year in years:
            counts = output / str(year) / "counts_raw_identity.parquet"
            edges = output / str(year) / "edges_raw_identity.parquet"
            annual[year] = counts, edges
            stages[f"counts_{year}"] = _stage(con, counts, f"""
                SELECT source_id, raw_issn_l, sum(a_raw)::BIGINT AS a_raw,
                       sum(a_filtered)::BIGINT AS a_filtered
                FROM read_parquet({_sql(retention)})
                WHERE year BETWEEN {year - 5} AND {year - 1}
                GROUP BY source_id, raw_issn_l
            """, contract_hash)
            stages[f"edges_{year}"] = _stage(con, edges, f"""
                WITH citing AS (
                    SELECT source_id AS citing_source_id, raw_issn_l AS raw_citing_issn_l,
                           unnest(list_distinct(refs)) AS cited_work_id
                    FROM read_parquet({_sql(works)})
                    WHERE year={year} AND has_refs
                ), cited AS (
                    SELECT work_id, source_id, raw_issn_l, has_refs
                    FROM read_parquet({_sql(works)})
                    WHERE is_ar AND year BETWEEN {year - 5} AND {year - 1}
                )
                SELECT e.citing_source_id, e.raw_citing_issn_l,
                       c.source_id AS cited_source_id, c.raw_issn_l AS raw_cited_issn_l,
                       count(*)::BIGINT AS n_raw,
                       count(*) FILTER (c.has_refs)::BIGINT AS n_filtered
                FROM citing e JOIN cited c ON e.cited_work_id=c.work_id
                GROUP BY e.citing_source_id, e.raw_citing_issn_l, c.source_id, c.raw_issn_l
            """, contract_hash)
        validation = _validate(con, raw, works, retention, annual, years)
        if (_hash_file(run / "config" / "run.json") != contract["config_sha256"]
                or _snapshot(run, config) != snapshot
                or _hash_file(Path(__file__)) != contract["extractor_sha256"]):
            raise RuntimeError("Inputs or extractor changed during extraction")
        report = {"status": "PASS", "version": VERSION,
                  "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                  "contract_sha256": contract_hash, "score_years": years,
                  "raw_snapshot_scans_this_invocation": int(not stages["raw_checkpoint"]["reused"]),
                  "score_computed": False, "identities_projected": False,
                  "self_citation_rows_retained": True, "stages": stages,
                  "validation": validation}
        _atomic_json(output / "report.json", report)
        return report
    finally:
        if con is not None:
            con.close()
        lock.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=RUN)
    parser.add_argument("--execute", action="store_true", help="Actually scan the configured snapshot")
    parser.add_argument("--memory-limit", default="8GB")
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    if not args.execute:
        parser.error("Extraction is a calculation stage; explicit --execute is required")
    print(json.dumps(extract(args.run, memory_limit=args.memory_limit, threads=args.threads), indent=2))


if __name__ == "__main__":
    main()
